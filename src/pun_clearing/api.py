"""
HTTP API for the PUN clearing model.

Run locally:   uvicorn pun_clearing.api:app --reload
Docs:          http://localhost:8000/docs

Solving one hour takes from a few seconds to a few minutes, and results are
cached in memory, so asking for the same hour again is instant.
"""

from functools import lru_cache

from fastapi import FastAPI, HTTPException, Path

from pun_clearing import service
from pun_clearing.service import HourResult

app = FastAPI(
    title="PUN clearing",
    description="MILP clearing of the Italian day-ahead market with a uniform purchase "
                "price (Savelli et al., 2017), validated against published GME prices.",
    version="0.1.0",
)


@lru_cache(maxsize=256)
def _cached_clear(date: str, hour: int) -> HourResult:
    return service.clear_hour(date, hour)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/dates")
def dates() -> list[str]:
    """Dates (YYYYMMDD) with GME data available on the server."""
    return service.available_dates()


@app.get("/clear/{date}/{hour}")
def clear(
    date: str = Path(pattern=r"^\d{8}$", examples=["20100504"]),
    hour: int = Path(ge=1, le=25, examples=[9]),
) -> HourResult:
    """Clear one hour: model PUN vs. published PUN, and zonal prices if the market split."""
    try:
        return _cached_clear(date, hour)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0])) from e
