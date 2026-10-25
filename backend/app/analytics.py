"""Day-ahead selection and hourly timestamp-matched wind accuracy analytics."""

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from threading import Lock
import json
import math
from pathlib import Path

import pandas as pd

from .elexon import DATA_DIR, MONTH_END, MONTH_START

TIMEZONE = "Europe/London"
METHODOLOGY = (
    "Hourly timestamp-matched comparison: WINDFOR forecasts versus FUELHH actuals "
    "at the identical timestamp; actuals are not hourly aggregated. "
    "No interpolation or forward-filling is used."
)
CUTOFF_ASSUMPTION = "Illustrative business assumption; not a verified market deadline."
HOURLY_INTERVALS = pd.date_range(MONTH_START, MONTH_END, freq="h", inclusive="left")
METRIC_FIELDS = (
    "maeMW", "rmseMW", "meanBiasErrorMW", "overforecastRate", "underforecastRate",
    "p90AbsoluteErrorMW", "maxAbsoluteErrorMW",
)


def load_dataset(path: Path, *, forecast: bool = False) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = ["startTime", "publishTime", "generation"] if forecast else ["startTime", "generation"]
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {', '.join(sorted(missing))}")
    for column in ["startTime", "publishTime"] if forecast else ["startTime"]:
        values = frame[column].astype("string")
        values = values.where(values.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", na=False))
        frame[column] = pd.to_datetime(values, utc=True, errors="coerce", format="mixed")
        if frame[column].isna().any():
            raise ValueError(f"{path.name}: invalid or missing {column}; repair the source CSV")
    return frame


def decision_cutoff(start_time: pd.Timestamp, cutoff_hour: int = 10) -> pd.Timestamp:
    if not 0 <= cutoff_hour <= 23:
        raise ValueError("cutoff_hour must be between 0 and 23")
    delivery_date = start_time.tz_convert(TIMEZONE).date()
    local_cutoff = datetime.combine(delivery_date - timedelta(days=1), time(cutoff_hour))
    # Localize the previous calendar day, rather than subtracting 24 hours in UTC.
    return pd.Timestamp(local_cutoff).tz_localize(TIMEZONE).tz_convert("UTC")


def resolve_generation(values: pd.Series) -> tuple[str, float | None]:
    # Most timestamp groups contain one numeric value. Avoid constructing several
    # temporary Series for that case; preserve the full duplicate/conflict path.
    if len(values) == 1 and values.dtype.kind in "iuf":
        value = values.iloc[0]
        return ("selected", float(value)) if pd.notna(value) and math.isfinite(value) else ("invalid", None)
    numeric = pd.to_numeric(values, errors="coerce")
    booleans = values.map(lambda value: isinstance(value, bool))
    finite = numeric.map(lambda value: pd.notna(value) and math.isfinite(value))
    unique = numeric.where(finite & ~booleans).drop_duplicates()
    if len(unique) > 1:
        return "conflict", None
    if unique.empty or pd.isna(unique.iloc[0]):
        return "invalid", None
    return "selected", float(unique.iloc[0])


def select_forecasts(
    forecasts: pd.DataFrame,
    deliveries: pd.DatetimeIndex,
    cutoff_hour: int = 10,
) -> pd.DataFrame:
    groups = dict(tuple(forecasts.groupby("startTime")))
    rows = []
    for start in deliveries:
        cutoff = decision_cutoff(start, cutoff_hour)
        row = {
            "startTime": start, "decisionCutoff": cutoff, "publishTime": pd.NaT,
            "forecastMW": None, "forecastStatus": "missing",
        }
        group = groups.get(start)
        if group is not None:
            eligible = group[group["publishTime"] <= cutoff]
            if not eligible.empty:
                latest = eligible["publishTime"].max()
                status, value = resolve_generation(eligible.loc[eligible["publishTime"] == latest, "generation"])
                row.update(publishTime=latest, forecastMW=value, forecastStatus=status)
        rows.append(row)
    selected = pd.DataFrame(rows, columns=[
        "startTime", "decisionCutoff", "publishTime", "forecastMW", "forecastStatus",
    ])
    for column in ["startTime", "decisionCutoff", "publishTime"]:
        selected[column] = pd.to_datetime(selected[column], utc=True)
    selected["forecastMW"] = pd.to_numeric(selected["forecastMW"], errors="coerce")
    return selected


def build_comparison(
    actuals: pd.DataFrame,
    forecasts: pd.DataFrame,
    cutoff_hour: int = 10,
    delivery_date: date | None = None,
) -> pd.DataFrame:
    if not 0 <= cutoff_hour <= 23:
        raise ValueError("cutoff_hour must be between 0 and 23")
    if delivery_date is not None and not MONTH_START.date() <= delivery_date < MONTH_END.date():
        raise ValueError("Only January 2026 delivery dates are available")
    deliveries = HOURLY_INTERVALS
    if delivery_date is not None:
        deliveries = deliveries[deliveries.tz_convert(TIMEZONE).date == delivery_date]
    comparison = select_forecasts(forecasts, deliveries, cutoff_hour)
    actual_groups = dict(tuple(actuals.groupby("startTime")))
    actual_values, actual_statuses = [], []
    for start in deliveries:
        group = actual_groups.get(start)
        status, value = ("missing", None) if group is None else resolve_generation(group["generation"])
        actual_values.append(value)
        actual_statuses.append(status)
    comparison["actualMW"] = pd.to_numeric(pd.Series(actual_values), errors="coerce")
    comparison["actualStatus"] = actual_statuses
    comparison["errorMW"] = comparison["forecastMW"] - comparison["actualMW"]
    comparison["absoluteErrorMW"] = comparison["errorMW"].abs()
    return comparison


# The bundled historical snapshot is immutable for a process lifetime. Restart to refresh.
_comparison_lock = Lock()


@lru_cache(maxsize=1)
def _load_snapshot() -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        load_dataset(DATA_DIR / "wind_actuals.csv"),
        load_dataset(DATA_DIR / "wind_forecasts.csv", forecast=True),
    )


@lru_cache(maxsize=24)
def _monthly_comparison(cutoff_hour: int) -> pd.DataFrame:
    actuals, forecasts = _load_snapshot()
    return build_comparison(actuals, forecasts, cutoff_hour)


def load_comparison(cutoff_hour: int = 10, delivery_date: date | None = None) -> pd.DataFrame:
    if not 0 <= cutoff_hour <= 23:
        raise ValueError("cutoff_hour must be between 0 and 23")
    if delivery_date is not None and not MONTH_START.date() <= delivery_date < MONTH_END.date():
        raise ValueError("Only January 2026 delivery dates are available")
    # lru_cache alone can compute the same miss concurrently. Share the initial work
    # between metrics/day requests and return copies so callers cannot corrupt it.
    with _comparison_lock:
        frame = _monthly_comparison(cutoff_hour)
        if delivery_date is not None:
            frame = frame.loc[frame["startTime"].dt.tz_convert(TIMEZONE).dt.date == delivery_date]
        return frame.reset_index(drop=True).copy(deep=True)


def calculate_metrics(comparison: pd.DataFrame) -> dict:
    errors = comparison["errorMW"].dropna()
    summary = {
        "sampleSize": len(errors),
        "totalHourlyIntervals": len(comparison),
        "eligibleForecastIntervals": int(comparison["forecastStatus"].eq("selected").sum()),
        "eligibleComparisonIntervals": len(errors),
        "missingDayAheadForecasts": int(comparison["forecastMW"].isna().sum()),
        "noEligiblePublicationIntervals": int(comparison["forecastStatus"].eq("missing").sum()),
        "conflictingForecastIntervals": int(comparison["forecastStatus"].eq("conflict").sum()),
        "invalidForecastIntervals": int(comparison["forecastStatus"].eq("invalid").sum()),
        "missingActualValues": int(comparison["actualMW"].isna().sum()),
        "conflictingActualIntervals": int(comparison["actualStatus"].eq("conflict").sum()),
        "rateUnit": "fraction",
        **dict.fromkeys(METRIC_FIELDS),
    }
    if not errors.empty:
        absolute = errors.abs()
        summary.update({
            "maeMW": float(absolute.mean()),
            "rmseMW": math.sqrt(float((errors ** 2).mean())),
            "meanBiasErrorMW": float(errors.mean()),
            "overforecastRate": float((errors > 0).mean()),
            "underforecastRate": float((errors < 0).mean()),
            "p90AbsoluteErrorMW": float(absolute.quantile(0.9)),
            "maxAbsoluteErrorMW": float(absolute.max()),
        })
    return summary


def comparison_records(comparison: pd.DataFrame) -> list[dict]:
    # pandas emits UTC ISO timestamps and JSON null for missing values.
    return json.loads(comparison.to_json(orient="records", date_format="iso", date_unit="s"))
