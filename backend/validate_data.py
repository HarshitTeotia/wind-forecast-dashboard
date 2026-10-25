"""Read-only diagnostics for the existing January CSV snapshot: python validate_data.py."""

import hashlib
import json
import math
import sys

import pandas as pd

from app.analytics import HOURLY_INTERVALS, build_comparison, calculate_metrics, load_dataset
from app.elexon import DATA_DIR, EXPECTED_INTERVALS

# Recorded before this validation phase; these are regression checks, not production data.
BASELINES = {
    0: {"sampleSize": 682, "missingDayAheadForecasts": 62, "maeMW": 2207.800586510264},
    10: {"sampleSize": 744, "missingDayAheadForecasts": 0, "maeMW": 2107.1760752688174,
         "rmseMW": 2611.2428706079936, "meanBiasErrorMW": 1839.9206989247311,
         "overforecastRate": 0.8440860215053764},
    23: {"sampleSize": 744, "missingDayAheadForecasts": 0, "maeMW": 1919.2809139784947},
}


def distribution(values: pd.Series) -> dict:
    values = values.dropna()
    if values.empty:
        return {"n": 0, **dict.fromkeys(["min", "max", "mean", "median", "p10", "p90"])}
    return {"n": len(values), "min": float(values.min()), "max": float(values.max()),
            "mean": float(values.mean()), "median": float(values.median()),
            "p10": float(values.quantile(0.1)), "p90": float(values.quantile(0.9))}


def snapshot_report() -> dict:
    paths = [DATA_DIR / "wind_actuals.csv", DATA_DIR / "wind_forecasts.csv"]
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    actuals, forecasts = load_dataset(paths[0]), load_dataset(paths[1], forecast=True)
    checks = {}
    for name, frame, expected in [("actuals", actuals, EXPECTED_INTERVALS), ("forecasts", forecasts, HOURLY_INTERVALS)]:
        frame["generation"] = pd.to_numeric(frame["generation"], errors="raise")
        finite = frame["generation"].map(lambda value: pd.notna(value) and math.isfinite(value))
        checks[name] = {
            "records": len(frame), "unique_deliveries": int(frame["startTime"].nunique()),
            "missing_expected_deliveries": len(expected.difference(frame["startTime"].unique())),
            "off_grid_records": int((~frame["startTime"].isin(expected)).sum()),
            "duplicate_records": int(frame.duplicated().sum()),
            "invalid_generation": int((~finite).sum()),
            "negative_generation": int((frame["generation"] < 0).sum()),
            "zero_generation": int((frame["generation"] == 0).sum()),
            "generation_mw": distribution(frame.loc[finite, "generation"]),
        }
        if not finite.all() or checks[name]["off_grid_records"] or checks[name]["duplicate_records"]:
            raise ValueError(f"{name}: invalid values, off-grid records, or duplicates: {checks[name]}")

    frames = {hour: build_comparison(actuals, forecasts, hour) for hour in BASELINES}
    metrics = {str(hour): calculate_metrics(frame) for hour, frame in frames.items()}
    baseline_differences = []
    for hour, baseline in BASELINES.items():
        for field, expected in baseline.items():
            actual = metrics[str(hour)][field]
            if actual is None or not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9):
                baseline_differences.append({"cutoff": hour, "field": field, "expected": expected, "actual": actual})

    # Check every selected publication at each requested cutoff, not only the default.
    forecast_groups = dict(tuple(forecasts.groupby("startTime")))
    for hour, frame in frames.items():
        selected = frame[frame["publishTime"].notna()]
        if not (selected["publishTime"] <= selected["decisionCutoff"]).all():
            raise ValueError(f"Publication after cutoff {hour}")
        for row in selected.itertuples(index=False):
            revisions = forecast_groups[row.startTime]
            eligible = revisions[revisions["publishTime"] <= row.decisionCutoff]
            if row.publishTime != eligible["publishTime"].max():
                raise ValueError(f"Not the latest eligible publication: {row.startTime}")
    common = set.intersection(*(set(frame.loc[frame["errorMW"].notna(), "startTime"]) for frame in frames.values()))
    common_metrics = {}
    for hour, frame in frames.items():
        errors = frame.loc[frame["startTime"].isin(common), "errorMW"]
        common_metrics[str(hour)] = {"n": len(errors), "maeMW": float(errors.abs().mean()), "biasMW": float(errors.mean())}
    frame = frames[10]
    matched = frame.loc[frame["errorMW"].notna()].copy()
    nonzero = matched["actualMW"] != 0
    matched["publicationLeadHours"] = (matched["startTime"] - matched["publishTime"]).dt.total_seconds() / 3600
    matched["cutoffLeadHours"] = (matched["startTime"] - matched["decisionCutoff"]).dt.total_seconds() / 3600
    lead_groups = {}
    for label, mask in {
        "up_to_24h": matched["publicationLeadHours"] <= 24,
        "24_to_36h": matched["publicationLeadHours"].between(24, 36, inclusive="right"),
        "over_36h": matched["publicationLeadHours"] > 36,
    }.items():
        group = matched.loc[mask, "errorMW"]
        lead_groups[label] = {"n": len(group), "maeMW": float(group.abs().mean()) if len(group) else None,
                              "biasMW": float(group.mean()) if len(group) else None}
    daily = matched.groupby(matched["startTime"].dt.tz_convert("Europe/London").dt.strftime("%Y-%m-%d"))["errorMW"].agg(["count", "mean"])
    variance = matched["actualMW"].var()
    slope = float(matched["actualMW"].cov(matched["forecastMW"]) / variance) if variance > 0 else None
    first = matched.iloc[0]
    hashes_unchanged = all(hashlib.sha256(path.read_bytes()).hexdigest() == before[path.name] for path in paths)
    if not hashes_unchanged:
        raise ValueError("Source CSV changed during diagnostics")
    return {
        "source_sha256": before, "source_hashes_unchanged": hashes_unchanged,
        "source_checks": checks,
        "forecast_statistics_weighting": "Raw forecast summary counts every stored revision; selected summary counts one day-ahead value per matched hour.",
        "selected_10_forecast_mw": distribution(matched["forecastMW"]),
        "selected_10_actual_mw": distribution(matched["actualMW"]),
        "error_mw": distribution(matched["errorMW"]),
        "forecast_to_actual_ratio": distribution(matched.loc[nonzero, "forecastMW"] / matched.loc[nonzero, "actualMW"]),
        "zero_actuals_excluded_from_ratio": int((~nonzero).sum()),
        "pearson_correlation": float(matched["forecastMW"].corr(matched["actualMW"])),
        "exploratory_forecast_vs_actual_fit": {"slope": slope, "interceptMW": float(matched["forecastMW"].mean() - slope * matched["actualMW"].mean()) if slope is not None else None},
        "selected_forecasts_above_maximum_observed_actual": int((matched["forecastMW"] > actuals["generation"].max()).sum()),
        "plausibility_limit": "No capacity metadata is stored. Negative/non-finite and data-relative extremes are reported; physical capacity plausibility cannot be certified.",
        "publication_lead_hours": distribution(matched["publicationLeadHours"]),
        "cutoff_lead_hours": distribution(matched["cutoffLeadHours"]),
        "lead_time_groups": lead_groups,
        "lead_time_caution": "Descriptive associations only; lead time, delivery hour, weather and sample composition are confounded. Cutoff 00 has a smaller sample.",
        "daily_mean_error_mw": {day: {"n": int(row['count']), "meanErrorMW": float(row['mean'])} for day, row in daily.iterrows()},
        "cutoff_metrics": metrics, "common_sample_cutoff_metrics": common_metrics, "baseline_differences": baseline_differences,
        "first_selected_record": {"startTime": first['startTime'].isoformat(), "publishTime": first['publishTime'].isoformat(), "decisionCutoff": first['decisionCutoff'].isoformat(), "forecastMW": float(first['forecastMW']), "actualMW": float(first['actualMW']), "errorMW": float(first['errorMW'])},
    }


if __name__ == "__main__":
    try:
        report = snapshot_report()
        print(json.dumps(report, indent=2, allow_nan=False))
        sys.exit(1 if report["baseline_differences"] else 0)
    except (OSError, ValueError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
