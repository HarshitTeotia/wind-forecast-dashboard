"""Download and validate January 2026 Elexon wind data: python -m app.elexon."""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import pandas as pd

BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets"
MONTH_START = pd.Timestamp("2026-01-01", tz="UTC")
MONTH_END = pd.Timestamp("2026-02-01", tz="UTC")
EXPECTED_INTERVALS = pd.date_range(MONTH_START, MONTH_END, freq="30min", inclusive="left")
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY = 30


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Respect Retry-After, capped so the entire retry sequence is bounded."""
    value = response.headers.get("Retry-After", "") if response is not None else ""
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 2 ** attempt
    if not math.isfinite(delay):
        delay = 2 ** attempt
    return min(MAX_RETRY_DELAY, max(0, delay))


def request_records(client: httpx.Client, dataset: str, params: dict[str, str]) -> list[dict]:
    for attempt in range(MAX_ATTEMPTS):
        response = None
        try:
            response = client.get(f"{BASE_URL}/{dataset}/stream", params=params)
            if response.status_code != 200:
                response.raise_for_status()
                raise ValueError(f"{dataset}: expected HTTP 200, got {response.status_code}")
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            retryable = response is None or response.status_code in {408, 429, 500, 502, 503, 504}
            if not retryable or attempt == MAX_ATTEMPTS - 1:
                if response is not None:
                    print(f"{dataset}: HTTP {response.status_code}: {response.text[:300]}", flush=True)
                raise
            delay = retry_delay(response, attempt)
            print(f"{dataset}: {type(exc).__name__}; retry {attempt + 1}/{MAX_ATTEMPTS - 1} in {delay:g}s", flush=True)
            time.sleep(delay)
            continue

        content_type = response.headers.get("content-type", "not provided")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(
                f"{dataset}: expected JSON records, Content-Type={content_type}; "
                f"payload begins {response.text[:160]!r}"
            ) from exc
        # Stream endpoints return a list; also accept the standard API data envelope.
        records = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError(f"{dataset}: expected a JSON array (or data array), got {type(records).__name__}")
        malformed = sum(not isinstance(record, dict) for record in records)
        if malformed:
            raise ValueError(f"{dataset}: {malformed} non-object records; ingestion stopped")
        print(f"{dataset} {params}: HTTP 200; Content-Type={content_type}; JSON records={len(records)}", flush=True)
        if not records:
            print(f"WARNING: {dataset} returned no records for this range", flush=True)
        return records
    raise RuntimeError("Retry loop exited unexpectedly")


def fetch_range(
    client: httpx.Client,
    dataset: str,
    start: str,
    end: str,
    *,
    allow_split: bool = True,
) -> list[dict]:
    prefix = "settlementDate" if dataset == "FUELHH" else "publishDateTime"
    params = {f"{prefix}From": start, f"{prefix}To": end}
    if dataset == "FUELHH":
        params["fuelType"] = "WIND"
    try:
        return request_records(client, dataset, params)
    except (httpx.HTTPStatusError, httpx.TransportError) as exc:
        # Preserve the exact reference requests first; reduce ranges only on failure.
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        if status not in {None, 400, 413, 422, 500, 502, 503, 504} or not allow_split:
            raise
        left, right = pd.Timestamp(start), pd.Timestamp(end)
        if right - left <= pd.Timedelta(days=1):
            raise
        midpoint = (left + pd.Timedelta(days=(right - left).days // 2)).date().isoformat()
        print(f"{dataset}: retrying smaller ranges at {midpoint}", flush=True)
        # Inclusive boundaries can overlap; normalization removes exact duplicates.
        return fetch_range(client, dataset, start, midpoint) + fetch_range(client, dataset, midpoint, end)


def fetch_actual_generation(client: httpx.Client) -> list[dict]:
    return fetch_range(client, "FUELHH", "2026-01-01", "2026-02-01")


def fetch_forecast_generation(client: httpx.Client) -> tuple[list[dict], dict]:
    january = fetch_range(client, "WINDFOR", "2026-01-01", "2026-02-01")
    lookback_start = pd.Timestamp("2025-12-25", tz="UTC")
    lookback = fetch_range(client, "WINDFOR", "2025-12-25", "2026-01-01")
    records = january + lookback
    checks = []
    # Check the preceding week, extending only if older publications still deliver in January.
    for _ in range(4):
        previous = lookback_start - pd.Timedelta(days=7)
        older = fetch_range(client, "WINDFOR", previous.date().isoformat(), lookback_start.date().isoformat())
        normalized, _ = normalize_records(older, forecast=True)
        relevant = normalized[normalized["publishTime"] < lookback_start]
        check = {
            "from": previous.date().isoformat(),
            "to": lookback_start.date().isoformat(),
            "raw_records": len(older),
            "earlier_publications_delivering_in_january": len(relevant),
        }
        checks.append(check)
        records.extend(older)
        if not older:
            print("WARNING: empty lookback check; historical lookback sufficiency is unverified", flush=True)
            break
        if relevant.empty:
            break
        lookback_start = previous
    else:
        print("WARNING: earlier relevant forecasts still found at the bounded lookback limit", flush=True)
    return records, {
        "january_reference_raw_records": len(january),
        "provisional_lookback_raw_records": len(lookback),
        "retained_publication_window_from": lookback_start.date().isoformat(),
        "lookback_checks": checks,
        "cutoff_coverage": "No procurement cutoff configured; suitability for a specific cutoff is not yet verified.",
    }


def normalize_records(records: list[dict], *, forecast: bool = False) -> tuple[pd.DataFrame, dict]:
    columns = ["startTime", "publishTime", "generation"] if forecast else ["startTime", "generation"]
    required = columns if forecast else ["startTime", "fuelType", "generation"]
    frame = pd.DataFrame.from_records(records)
    quality = {"downloaded_records": len(frame)}
    if frame.empty:
        empty = pd.DataFrame({column: pd.Series(dtype="datetime64[ns, UTC]" if column.endswith("Time") else "float64") for column in columns})
        return empty, {**quality, "empty_result": True}
    absent = [field for field in required if field not in frame.columns]
    if absent:
        raise ValueError(f"Missing required fields: {', '.join(absent)}")
    quality["records_with_missing_required_values"] = int(frame[required].isna().any(axis=1).sum())
    valid = pd.Series(True, index=frame.index)
    for column in ["startTime", "publishTime"] if forecast else ["startTime"]:
        # Reject numeric epochs and timezone-free strings rather than guessing their meaning.
        timestamp_strings = frame[column].where(frame[column].map(lambda value: isinstance(value, str))).astype("string")
        has_timezone = timestamp_strings.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", na=False)
        frame[column] = pd.to_datetime(timestamp_strings.where(has_timezone), utc=True, errors="coerce", format="mixed")
        invalid = frame[column].isna()
        quality[f"invalid_or_missing_{column}"] = int(invalid.sum())
        valid &= ~invalid
    boolean_generation = frame["generation"].map(lambda value: isinstance(value, bool))
    frame["generation"] = pd.to_numeric(frame["generation"], errors="coerce")
    invalid_generation = ~frame["generation"].map(lambda value: pd.notna(value) and math.isfinite(value)) | boolean_generation
    quality["null_or_invalid_generation"] = int(invalid_generation.sum())
    valid &= ~invalid_generation
    quality["malformed_records_excluded"] = int((~valid).sum())
    january = frame["startTime"].ge(MONTH_START) & frame["startTime"].lt(MONTH_END)
    quality["outside_january_delivery_records"] = int((frame["startTime"].notna() & ~january).sum())
    aligned = frame["startTime"].isin(EXPECTED_INTERVALS)
    quality["january_off_grid_records"] = int((january & ~aligned).sum())
    if not forecast:
        wind = frame["fuelType"].eq("WIND")
        quality["non_wind_or_missing_fuel_type_records"] = int((~wind).sum())
        valid &= wind
    selected = frame.loc[valid & january & aligned, columns]
    quality["exact_duplicate_records_removed"] = int(selected.duplicated().sum())
    normalized = selected.drop_duplicates().sort_values(columns, kind="stable").reset_index(drop=True)
    keys = ["startTime", "publishTime"] if forecast else ["startTime"]
    quality["conflicting_key_groups"] = int((normalized.groupby(keys)["generation"].nunique() > 1).sum())
    if quality["conflicting_key_groups"]:
        print(f"WARNING: {quality['conflicting_key_groups']} conflicting key groups preserved; no value chosen", flush=True)
    return normalized, quality


def coverage_summary(frame: pd.DataFrame, *, forecast: bool = False) -> dict:
    deliveries = pd.DatetimeIndex(frame["startTime"].unique())
    missing = EXPECTED_INTERVALS.difference(deliveries)
    summary = {
        "total_records": len(frame),
        "first_delivery_utc": str(frame["startTime"].min()),
        "last_delivery_utc": str(frame["startTime"].max()),
        "unique_delivery_intervals": len(deliveries),
        "expected_half_hour_intervals": len(EXPECTED_INTERVALS),
        "missing_half_hour_intervals": len(missing),
        "first_missing_intervals_utc": [value.isoformat() for value in missing[:8]],
        "delivery_minutes_present": sorted(frame["startTime"].dt.minute.unique().tolist()),
    }
    if forecast:
        revisions = frame.groupby("startTime")["publishTime"].nunique()
        jan1 = frame[frame["startTime"] < MONTH_START + pd.Timedelta(days=1)]
        pre_january = jan1[jan1["publishTime"] < MONTH_START]
        summary.update({
            "earliest_publication_utc": str(frame["publishTime"].min()),
            "latest_publication_utc": str(frame["publishTime"].max()),
            "revisions_per_covered_delivery": {"min": int(revisions.min()), "median": float(revisions.median()), "max": int(revisions.max())},
            "jan1_deliveries_with_december_publications": int(pre_january["startTime"].nunique()),
            "jan1_covered_deliveries": int(jan1["startTime"].nunique()),
            "december_publication_records_retained": int((frame["publishTime"] < MONTH_START).sum()),
        })
    return summary


def save_csv(frame: pd.DataFrame, path: Path) -> None:
    """Replace each CSV atomically; never append or leave a partially written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.tmp")
    try:
        frame.to_csv(temporary, index=False, date_format="%Y-%m-%dT%H:%M:%SZ", lineterminator="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    with httpx.Client(headers={"accept": "text/plain"}, timeout=httpx.Timeout(90, connect=15)) as client:
        raw_actuals = fetch_actual_generation(client)
        raw_forecasts, lookback = fetch_forecast_generation(client)
    actuals, actual_quality = normalize_records(raw_actuals)
    forecasts, forecast_quality = normalize_records(raw_forecasts, forecast=True)
    print("\nActual generation validation:", json.dumps(actual_quality, indent=2), flush=True)
    print("\nForecast generation validation:", json.dumps(forecast_quality, indent=2), flush=True)
    if actuals.empty or forecasts.empty:
        raise ValueError("At least one normalized dataset is empty; existing CSVs have not been replaced")
    for name, frame, forecast in [("Actual generation", actuals, False), ("Forecast generation", forecasts, True)]:
        summary = coverage_summary(frame, forecast=forecast)
        print(f"\n{name} coverage:\n{json.dumps(summary, indent=2)}", flush=True)
        if summary["missing_half_hour_intervals"]:
            print(f"WARNING: {name} half-hour coverage is incomplete; no missing values were filled", flush=True)
    end_date_rows = sum(str(record.get("settlementDate")) == "2026-02-01" for record in raw_actuals)
    print(f"\nFUELHH end-date check: {end_date_rows} records on 2026-02-01; "
          + ("end date is inclusive (these records were excluded)." if end_date_rows else "inclusivity could not be confirmed from returned records."), flush=True)
    print(f"\nForecast lookback:\n{json.dumps(lookback, indent=2)}", flush=True)
    save_csv(actuals, DATA_DIR / "wind_actuals.csv")
    save_csv(forecasts, DATA_DIR / "wind_forecasts.csv")
    print(f"\nSaved {len(actuals)} actuals and {len(forecasts)} forecast revisions to {DATA_DIR}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
