"""Validation regressions using captured Elexon records; no API success is mocked."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import pandas as pd

from app.elexon import (
    MAX_ATTEMPTS,
    coverage_summary,
    normalize_records,
    request_records,
    retry_delay,
    save_csv,
)

# Actual records observed in the live January 2026 download.
ACTUAL = {"startTime": "2026-01-01T00:00:00Z", "fuelType": "WIND", "generation": 13756}
FORECAST = {"startTime": "2026-01-01T00:00:00Z", "publishTime": "2025-12-30T02:30:00Z", "generation": 19028}
REVISION = {"startTime": "2026-01-01T00:00:00Z", "publishTime": "2025-12-30T04:30:00Z", "generation": 19025}


class NormalizationTests(unittest.TestCase):
    def test_actual_filtering_and_exact_duplicates(self):
        records = [ACTUAL, ACTUAL.copy(), {**ACTUAL, "fuelType": "GAS"},
                   {**ACTUAL, "startTime": "2026-02-01T00:00:00Z"}]
        frame, quality = normalize_records(records)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["generation"], 13756)
        self.assertEqual(quality["exact_duplicate_records_removed"], 1)
        self.assertEqual(quality["outside_january_delivery_records"], 1)
        self.assertEqual(quality["non_wind_or_missing_fuel_type_records"], 1)

    def test_invalid_generation_reported(self):
        records = [ACTUAL] + [{**ACTUAL, "generation": value} for value in [None, "bad", True, float("inf")]]
        frame, quality = normalize_records(records)
        self.assertEqual(len(frame), 1)
        self.assertEqual(quality["null_or_invalid_generation"], 4)
        self.assertEqual(quality["malformed_records_excluded"], 4)

    def test_timestamp_validation_and_utc_conversion(self):
        records = [
            {**ACTUAL, "startTime": value}
            for value in [None, "invalid", 123, "2026-01-01T00:00:00", "2026-01-01T01:00:00+01:00"]
        ]
        frame, quality = normalize_records(records)
        self.assertEqual(quality["invalid_or_missing_startTime"], 4)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["startTime"], pd.Timestamp("2026-01-01", tz="UTC"))

    def test_wholly_invalid_timestamp_columns_are_reported(self):
        for field in ["startTime", "publishTime"]:
            for values in [[123, 456], [None, None]]:
                with self.subTest(field=field, values=values):
                    records = [{**FORECAST, field: value} for value in values]
                    frame, quality = normalize_records(records, forecast=True)
                    self.assertTrue(frame.empty)
                    self.assertEqual(quality[f"invalid_or_missing_{field}"], 2)
                    self.assertEqual(quality["malformed_records_excluded"], 2)

    def test_off_grid_records_are_reported(self):
        frame, quality = normalize_records([ACTUAL, {**ACTUAL, "startTime": "2026-01-01T00:15:00Z"}])
        self.assertEqual(len(frame), 1)
        self.assertEqual(quality["january_off_grid_records"], 1)

    def test_empty_and_missing_schema(self):
        frame, quality = normalize_records([], forecast=True)
        self.assertTrue(frame.empty)
        self.assertEqual(list(frame), ["startTime", "publishTime", "generation"])
        self.assertTrue(quality["empty_result"])
        with self.assertRaisesRegex(ValueError, "Missing required fields"):
            normalize_records([{"startTime": ACTUAL["startTime"]}])

    def test_revisions_preserved_and_missing_intervals_not_filled(self):
        frame, quality = normalize_records([FORECAST, FORECAST.copy(), REVISION], forecast=True)
        self.assertEqual(len(frame), 2)
        self.assertEqual(quality["exact_duplicate_records_removed"], 1)
        summary = coverage_summary(frame, forecast=True)
        self.assertEqual(summary["unique_delivery_intervals"], 1)
        self.assertEqual(summary["missing_half_hour_intervals"], 1487)
        self.assertEqual(summary["revisions_per_covered_delivery"], {"min": 2, "median": 2.0, "max": 2})

    def test_missing_fields_on_individual_records_reported(self):
        frame, quality = normalize_records([FORECAST, {"startTime": FORECAST["startTime"], "generation": 19028}], forecast=True)
        self.assertEqual(len(frame), 1)
        self.assertEqual(quality["records_with_missing_required_values"], 1)
        self.assertEqual(quality["invalid_or_missing_publishTime"], 1)

    def test_csv_replacement_is_idempotent(self):
        frame, _ = normalize_records([FORECAST, REVISION], forecast=True)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "wind.csv"
            save_csv(frame, path)
            first = path.read_bytes()
            save_csv(frame, path)
            self.assertEqual(path.read_bytes(), first)
            self.assertIn(b"2025-12-30T02:30:00Z", first)
            self.assertFalse(path.with_suffix(".csv.tmp").exists())


class RequestFailureTests(unittest.TestCase):
    def test_rate_limit_retries_are_bounded(self):
        def limited(request):
            return httpx.Response(429, headers={"Retry-After": "9999"}, request=request)

        with httpx.Client(transport=httpx.MockTransport(limited)) as client:
            with patch("app.elexon.time.sleep") as sleep:
                with self.assertRaises(httpx.HTTPStatusError):
                    request_records(client, "WINDFOR", {})
                self.assertEqual(sleep.call_count, MAX_ATTEMPTS - 1)
                self.assertTrue(all(call.args[0] == 30 for call in sleep.call_args_list))

    def test_timeout_retries_are_bounded(self):
        def timeout(request):
            raise httpx.ReadTimeout("Timed out", request=request)

        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            with patch("app.elexon.time.sleep") as sleep:
                with self.assertRaises(httpx.ReadTimeout):
                    request_records(client, "WINDFOR", {})
                self.assertEqual(sleep.call_count, MAX_ATTEMPTS - 1)

    def test_non_transient_error_is_not_retried(self):
        def bad_request(request):
            return httpx.Response(400, request=request, text="Invalid parameters")

        with httpx.Client(transport=httpx.MockTransport(bad_request)) as client:
            with patch("app.elexon.time.sleep") as sleep:
                with self.assertRaises(httpx.HTTPStatusError):
                    request_records(client, "FUELHH", {})
                sleep.assert_not_called()

    def test_retry_after_date_and_invalid_values(self):
        self.assertEqual(retry_delay(httpx.Response(429, headers={"Retry-After": "Wed, 01 Jan 2020 00:00:00 GMT"}), 0), 0)
        self.assertEqual(retry_delay(httpx.Response(429, headers={"Retry-After": "invalid"}), 1), 2)


if __name__ == "__main__":
    unittest.main()
