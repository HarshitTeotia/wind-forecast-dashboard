"""Small known cases are test fixtures only; production analytics reads the Elexon CSVs."""

from datetime import date
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pandas as pd

from app.analytics import (
    METRIC_FIELDS,
    build_comparison,
    calculate_metrics,
    comparison_records,
    decision_cutoff,
    load_dataset,
)
from app.main import app
from app import analytics

DAY = date(2026, 1, 1)
START = "2026-01-01T00:00:00Z"
BEFORE = "2025-12-31T09:30:00Z"
CUTOFF = "2025-12-31T10:00:00Z"
AFTER = "2025-12-31T10:30:00Z"


def actual_frame(rows=()):
    frame = pd.DataFrame(rows, columns=["startTime", "generation"])
    frame["startTime"] = pd.to_datetime(frame["startTime"], utc=True)
    return frame


def forecast_frame(rows=()):
    frame = pd.DataFrame(rows, columns=["startTime", "publishTime", "generation"])
    for field in ["startTime", "publishTime"]:
        frame[field] = pd.to_datetime(frame[field], utc=True)
    return frame


def compare(actuals=(), forecasts=()):
    return build_comparison(actual_frame(actuals), forecast_frame(forecasts), delivery_date=DAY)


class SelectionTests(unittest.TestCase):
    def test_single_generation_fast_path_preserves_validation(self):
        for value, expected in [(0, ("selected", 0.0)), (-10, ("selected", -10.0)),
                                (110.5, ("selected", 110.5)), ("110", ("selected", 110.0)),
                                (True, ("invalid", None)), (False, ("invalid", None)),
                                (None, ("invalid", None)), (pd.NA, ("invalid", None)),
                                ("invalid", ("invalid", None)), (float("nan"), ("invalid", None)),
                                (float("inf"), ("invalid", None)), (-float("inf"), ("invalid", None))]:
            with self.subTest(value=value):
                self.assertEqual(analytics.resolve_generation(pd.Series([value])), expected)
        self.assertEqual(analytics.resolve_generation(pd.Series([pd.NA], dtype="Int64")), ("invalid", None))
        self.assertEqual(analytics.resolve_generation(pd.Series([110], dtype="Int64")), ("selected", 110.0))

    def test_latest_before_cutoff_and_after_cutoff_rejected(self):
        frame = compare([(START, 100)], [(START, BEFORE, 110), (START, AFTER, 120)])
        row = frame.iloc[0]
        self.assertEqual(row["publishTime"], pd.Timestamp(BEFORE))
        self.assertEqual(row["forecastMW"], 110)
        self.assertEqual(row["errorMW"], 10)

    def test_publication_exactly_at_cutoff_is_eligible(self):
        row = compare([(START, 100)], [(START, BEFORE, 110), (START, CUTOFF, 115)]).iloc[0]
        self.assertEqual(row["publishTime"], pd.Timestamp(CUTOFF))
        self.assertEqual(row["forecastMW"], 115)

    def test_after_cutoff_only_is_missing(self):
        frame = compare([(START, 100)], [(START, AFTER, 120)])
        self.assertEqual(frame.iloc[0]["forecastStatus"], "missing")
        self.assertTrue(pd.isna(frame.iloc[0]["forecastMW"]))
        self.assertTrue(pd.isna(frame.iloc[0]["errorMW"]))

    def test_duplicate_identical_publications_are_not_conflicts(self):
        frame = compare([(START, 100)], [(START, BEFORE, 110)] * 2)
        self.assertEqual(frame.iloc[0]["forecastStatus"], "selected")
        self.assertEqual(frame.iloc[0]["forecastMW"], 110)

    def test_conflicting_latest_publications_are_flagged(self):
        rows = [(START, BEFORE, 110), (START, BEFORE, 120)]
        frame = compare([(START, 100)], rows)
        self.assertEqual(frame.iloc[0]["forecastStatus"], "conflict")
        self.assertEqual(frame.iloc[0]["publishTime"], pd.Timestamp(BEFORE))
        self.assertTrue(pd.isna(frame.iloc[0]["errorMW"]))
        summary = calculate_metrics(frame)
        self.assertEqual(summary["conflictingForecastIntervals"], 1)
        self.assertEqual(summary["sampleSize"], 0)
        self.assertEqual(summary["missingDayAheadForecasts"], 24)

    def test_older_conflict_does_not_disqualify_newer_unambiguous_revision(self):
        rows = [(START, BEFORE, 110), (START, BEFORE, 120), (START, CUTOFF, 115)]
        self.assertEqual(compare([(START, 100)], rows).iloc[0]["forecastMW"], 115)

    def test_actuals_match_exact_timestamps_without_aggregation(self):
        half_hour = "2026-01-01T00:30:00Z"
        frame = compare([(START, 100), (half_hour, 900)], [(START, BEFORE, 110)])
        self.assertEqual(frame.iloc[0]["actualMW"], 100)
        self.assertEqual(frame.iloc[0]["errorMW"], 10)
        self.assertEqual(len(frame), 24)
        self.assertNotIn(pd.Timestamp(half_hour), frame["startTime"].tolist())
        unmatched = compare([(half_hour, 900)], [(START, BEFORE, 110)])
        self.assertTrue(pd.isna(unmatched.iloc[0]["actualMW"]))
        self.assertEqual(unmatched.iloc[0]["actualStatus"], "missing")

    def test_missing_or_nonfinite_values_do_not_become_zero(self):
        frame = compare([(START, float("inf"))], [(START, BEFORE, 110)])
        self.assertEqual(calculate_metrics(frame)["missingActualValues"], 24)
        self.assertTrue(pd.isna(frame.iloc[0]["errorMW"]))
        invalid = compare([(START, 100)], [(START, BEFORE, 110), (START, CUTOFF, None)])
        self.assertEqual(invalid.iloc[0]["forecastStatus"], "invalid")
        self.assertTrue(pd.isna(invalid.iloc[0]["errorMW"]))

    def test_conflicting_actuals_are_not_chosen_arbitrarily(self):
        frame = compare([(START, 100), (START, 101)], [(START, BEFORE, 110)])
        self.assertEqual(frame.iloc[0]["actualStatus"], "conflict")
        self.assertTrue(pd.isna(frame.iloc[0]["errorMW"]))
        self.assertEqual(calculate_metrics(frame)["conflictingActualIntervals"], 1)

    def test_previous_local_calendar_day_and_summer_timezone(self):
        self.assertEqual(decision_cutoff(pd.Timestamp(START)), pd.Timestamp(CUTOFF))
        summer = pd.Timestamp("2026-06-01T23:00:00Z")  # June 2 locally.
        self.assertEqual(decision_cutoff(summer), pd.Timestamp("2026-06-01T09:00:00Z"))
        dst_day = pd.Timestamp("2026-03-29T10:00:00Z")
        self.assertEqual(decision_cutoff(dst_day), pd.Timestamp("2026-03-28T10:00:00Z"))

    def test_inputs_are_not_modified(self):
        actuals = actual_frame([(START, 100)])
        forecasts = forecast_frame([(START, BEFORE, 110)])
        old_actuals, old_forecasts = actuals.copy(deep=True), forecasts.copy(deep=True)
        build_comparison(actuals, forecasts, delivery_date=DAY)
        pd.testing.assert_frame_equal(actuals, old_actuals)
        pd.testing.assert_frame_equal(forecasts, old_forecasts)

    def test_full_grid_retains_missing_forecasts(self):
        frame = build_comparison(actual_frame(), forecast_frame())
        self.assertEqual(len(frame), 744)
        self.assertEqual(calculate_metrics(frame)["missingDayAheadForecasts"], 744)


class MetricsTests(unittest.TestCase):
    def test_known_positive_negative_and_zero_errors(self):
        starts = [f"2026-01-01T{hour:02}:00:00Z" for hour in range(3)]
        actuals = [(start, 100) for start in starts]
        forecasts = [(start, BEFORE, value) for start, value in zip(starts, [110, 80, 100])]
        frame = compare(actuals, forecasts)
        self.assertEqual(frame["errorMW"].dropna().tolist(), [10, -20, 0])
        self.assertEqual(frame["absoluteErrorMW"].dropna().tolist(), [10, 20, 0])
        summary = calculate_metrics(frame)
        self.assertEqual(summary["sampleSize"], 3)
        self.assertEqual(summary["eligibleComparisonIntervals"], 3)
        self.assertEqual(summary["eligibleForecastIntervals"], 3)
        self.assertEqual(summary["missingDayAheadForecasts"], 21)
        self.assertEqual(summary["missingActualValues"], 21)
        self.assertEqual(summary["maeMW"], 10)
        self.assertAlmostEqual(summary["rmseMW"], math.sqrt(500 / 3))
        self.assertAlmostEqual(summary["meanBiasErrorMW"], -10 / 3)
        self.assertAlmostEqual(summary["overforecastRate"], 1 / 3)
        self.assertAlmostEqual(summary["underforecastRate"], 1 / 3)
        self.assertEqual(summary["p90AbsoluteErrorMW"], 18)
        self.assertEqual(summary["maxAbsoluteErrorMW"], 20)

    def test_no_valid_matches_returns_null_metrics(self):
        summary = calculate_metrics(compare())
        self.assertEqual(summary["sampleSize"], 0)
        self.assertTrue(all(summary[field] is None for field in METRIC_FIELDS))

    def test_missing_actuals_excluded_independently(self):
        frame = compare([], [(START, BEFORE, 110)])
        summary = calculate_metrics(frame)
        self.assertEqual(summary["eligibleForecastIntervals"], 1)
        self.assertEqual(summary["eligibleComparisonIntervals"], 0)
        self.assertEqual(summary["missingDayAheadForecasts"], 23)
        self.assertEqual(summary["missingActualValues"], 24)

    def test_serialization_has_utc_timestamps_and_json_nulls(self):
        records = comparison_records(compare())
        self.assertEqual(records[0]["startTime"], START)
        self.assertEqual(records[0]["decisionCutoff"], CUTOFF)
        self.assertIsNone(records[0]["publishTime"])
        self.assertIsNone(records[0]["errorMW"])
        json.dumps(records, allow_nan=False)


class DatasetAndApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_invalid_cutoffs_and_dates(self):
        for endpoint in ["comparison", "metrics"]:
            for hour in [-1, 24, "invalid"]:
                with self.subTest(endpoint=endpoint, hour=hour):
                    self.assertEqual(self.client.get(f"/api/{endpoint}", params={"cutoff_hour": hour}).status_code, 422)
        for value in ["invalid", "2026-02-01", "2025-12-31"]:
            self.assertEqual(self.client.get('/api/comparison', params={"date": value}).status_code, 422)

    def test_valid_cutoff_boundaries(self):
        for hour in [0, 23]:
            frame = compare()
            with patch('app.main.load_comparison', return_value=frame) as load:
                response = self.client.get('/api/comparison', params={"cutoff_hour": hour})
                self.assertEqual(response.status_code, 200)
                load.assert_called_once_with(hour, None)

    def test_date_filter_and_shared_selection(self):
        frame = compare([(START, 100)], [(START, BEFORE, 110)])
        with patch('app.main.load_comparison', return_value=frame) as load:
            response = self.client.get('/api/comparison?date=2026-01-01')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["sampleSize"], 1)
            self.assertEqual(response.json()["totalHourlyIntervals"], 24)
            load.assert_called_once_with(10, DAY)
        with patch('app.main.load_comparison', return_value=frame) as load:
            response = self.client.get('/api/metrics')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["maeMW"], 10)
            load.assert_called_once_with(10, None)

    def test_missing_or_malformed_files_return_503(self):
        for error in [FileNotFoundError('missing CSV'), ValueError('invalid timestamp')]:
            with patch('app.main.load_comparison', side_effect=error):
                for endpoint in ['comparison', 'metrics']:
                    self.assertEqual(self.client.get(f'/api/{endpoint}').status_code, 503)
                self.assertEqual(self.client.get('/api/health').json(), {"status": "ok", "message": "Wind Forecast API is running"})

    def test_csv_timestamp_validation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / 'invalid.csv'
            for value in ['invalid', '', '2026-01-01T00:00:00']:
                path.write_text(f'startTime,generation\n{value},100\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'invalid or missing startTime'):
                    load_dataset(path)


class ComparisonCacheTests(unittest.TestCase):
    def setUp(self):
        analytics._monthly_comparison.cache_clear()
        analytics._load_snapshot.cache_clear()
        self.addCleanup(analytics._monthly_comparison.cache_clear)
        self.addCleanup(analytics._load_snapshot.cache_clear)
        self.actuals = actual_frame([(START, 100)])
        self.forecasts = forecast_frame([(START, BEFORE, 110), (START, AFTER, 120)])

    def test_cutoffs_dates_and_mutation_are_isolated(self):
        with patch('app.analytics.load_dataset', side_effect=[self.actuals, self.forecasts]) as read:
            with patch('app.analytics.build_comparison', wraps=build_comparison) as build:
                for hour in [0, 10, 23]:
                    expected = build_comparison(self.actuals, self.forecasts, hour)
                    monthly = analytics.load_comparison(hour)
                    pd.testing.assert_frame_equal(monthly, expected)
                    daily = analytics.load_comparison(hour, DAY)
                    pd.testing.assert_frame_equal(daily, expected.iloc[:24].reset_index(drop=True))
                    daily.loc[0, 'forecastMW'] = -999
                    monthly.loc[0, 'forecastStatus'] = 'corrupted'
                    pd.testing.assert_frame_equal(analytics.load_comparison(hour), expected)
                    next_day = analytics.load_comparison(hour, date(2026, 1, 2))
                    self.assertEqual(len(next_day), 24)
                    self.assertEqual(next_day.iloc[0]['startTime'], pd.Timestamp('2026-01-02T00:00:00Z'))
                self.assertEqual(build.call_count, 3)
                self.assertEqual(read.call_count, 2)
                self.assertEqual(analytics._monthly_comparison.cache_info().maxsize, 24)

    def test_concurrent_requests_share_initial_calculation(self):
        with patch('app.analytics.load_dataset', side_effect=[self.actuals, self.forecasts]) as read:
            with patch('app.analytics.build_comparison', wraps=build_comparison) as build:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    frames = list(pool.map(lambda _: analytics.load_comparison(10), range(4)))
                self.assertEqual(read.call_count, 2)
                self.assertEqual(build.call_count, 1)
                for frame in frames[1:]:
                    pd.testing.assert_frame_equal(frame, frames[0])
                    self.assertIsNot(frame, frames[0])

    def test_failed_load_is_retried_and_successful_snapshot_retained(self):
        with patch('app.analytics.load_dataset', side_effect=[FileNotFoundError('missing CSV'), self.actuals, self.forecasts]) as read:
            with self.assertRaises(FileNotFoundError):
                analytics.load_comparison(10)
            recovered = analytics.load_comparison(10, DAY)
            self.assertEqual(recovered.iloc[0]['forecastMW'], 110)
            analytics.load_comparison(23)
            self.assertEqual(read.call_count, 3)

    def test_invalid_filters_do_not_load_or_populate_cache(self):
        with patch('app.analytics.load_dataset') as read:
            for hour, day in [(-1, None), (24, None), (10, date(2026, 2, 1))]:
                with self.assertRaises(ValueError):
                    analytics.load_comparison(hour, day)
            read.assert_not_called()
            self.assertEqual(analytics._monthly_comparison.cache_info().currsize, 0)
