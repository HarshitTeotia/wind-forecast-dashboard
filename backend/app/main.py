import os
from datetime import date as Date
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .analytics import (
    CUTOFF_ASSUMPTION,
    METHODOLOGY,
    TIMEZONE,
    calculate_metrics,
    comparison_records,
    load_comparison,
)
from .elexon import MONTH_END, MONTH_START

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(title="Wind Forecast API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def comparison_data(cutoff_hour: int, delivery_date: Date | None = None):
    try:
        return load_comparison(cutoff_hour, delivery_date)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Historical data unavailable: {exc}. Check backend/data; run python -m app.elexon from backend if the files are missing.",
        ) from exc


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "message": "Wind Forecast API is running"}


@app.get("/api/comparison")
def comparison(
    date: Annotated[Date | None, Query(description="January 2026 delivery date (YYYY-MM-DD)")] = None,
    cutoff_hour: Annotated[int, Query(ge=0, le=23)] = 10,
) -> dict:
    if date is not None and not MONTH_START.date() <= date < MONTH_END.date():
        raise HTTPException(status_code=422, detail="Only January 2026 delivery dates are available")
    frame = comparison_data(cutoff_hour, date)
    return {
        "methodology": METHODOLOGY,
        "cutoffHour": cutoff_hour,
        "cutoffTimezone": TIMEZONE,
        "cutoffAssumption": CUTOFF_ASSUMPTION,
        "deliveryDate": date.isoformat() if date else None,
        "sampleSize": int(frame["errorMW"].notna().sum()),
        "totalHourlyIntervals": len(frame),
        "records": comparison_records(frame),
    }


@app.get("/api/metrics")
def metrics(cutoff_hour: Annotated[int, Query(ge=0, le=23)] = 10) -> dict:
    return {
        "methodology": METHODOLOGY,
        "cutoffHour": cutoff_hour,
        "cutoffTimezone": TIMEZONE,
        "cutoffAssumption": CUTOFF_ASSUMPTION,
        "periodStart": MONTH_START.isoformat().replace("+00:00", "Z"),
        "periodEndExclusive": MONTH_END.isoformat().replace("+00:00", "Z"),
        **calculate_metrics(comparison_data(cutoff_hour)),
    }
