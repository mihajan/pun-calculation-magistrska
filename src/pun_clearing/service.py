"""
Clear single hours and compare them with the published GME prices.

This is the layer shared by the CLI and the HTTP API: it loads a day of GME
data (cached), solves one hour and returns a plain result object.
"""

import os
import re
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pun_clearing.conf import options
from pun_clearing.dataio.gme_importer import GMEImporter
from pun_clearing.model.data_classes import HourlyData
from pun_clearing.model.pun_model import build_and_solve

# Two prices are "different" (market split) if they differ by more than this.
SPLIT_TOLERANCE = 1e-6
# Reported prices are rounded to this many decimals (GME publishes the PUN with 6).
DECIMALS = 6


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, DECIMALS) + 0.0   # + 0.0 turns -0.0 into 0.0


@dataclass
class ZonePrice:
    zone: str
    model: float
    real: float | None
    difference: float | None


@dataclass
class HourResult:
    date: str
    hour: int
    pun_model: float
    pun_real: float | None
    pun_difference: float | None
    market_split: bool
    zonal_prices: list[ZonePrice] = field(default_factory=list)  # only if market_split
    solve_time_s: float = 0.0
    solver_status: str | None = None


def data_dir() -> Path:
    """Directory with the GME files; set PUN_DATA_DIR to override (default: ./data)."""
    return Path(os.environ.get("PUN_DATA_DIR", "data"))


def available_dates(directory: Path | None = None) -> list[str]:
    """Dates (YYYYMMDD) for which GME files are present in the data directory."""
    directory = directory or data_dir()
    if not directory.is_dir():
        return []
    dates = {match.group(0) for f in directory.iterdir()
             if f.suffix in (".zip", ".xml") and (match := re.search(r"\d{8}", f.name))}
    return sorted(dates)


@lru_cache(maxsize=8)
def load_day(directory: str, date: str) -> GMEImporter:
    """Parse the GME files of one day (~1 s), cached across calls."""
    return GMEImporter(path=directory, date=date)


def summarize(date: str, data: HourlyData, real_prices: dict[str, float],
              solve_time_s: float = 0.0) -> HourResult:
    """Turn a solved hour into a result comparable with the published prices."""
    assert data.pun_price is not None, "hour has not been solved"
    zone_names = {zone.zone_id: zone.name for zone in data.zones.values()}

    zonal = []
    for zone_id, name in zone_names.items():
        if name not in options.PUN_ZONES:
            continue
        model = data.zonal_prices[zone_id]
        real = real_prices.get(name)
        zonal.append(ZonePrice(zone=name, model=round(model, DECIMALS), real=real,
                               difference=None if real is None else _round(model - real)))
    zonal.sort(key=lambda z: options.PUN_ZONES.index(z.zone))

    prices = [z.model for z in zonal]
    split = bool(prices) and max(prices) - min(prices) > SPLIT_TOLERANCE

    pun_real = real_prices.get("PUN")
    return HourResult(
        date=date,
        hour=data.period,
        pun_model=round(data.pun_price, DECIMALS),
        pun_real=pun_real,
        pun_difference=None if pun_real is None else _round(data.pun_price - pun_real),
        market_split=split,
        zonal_prices=zonal if split else [],
        solve_time_s=round(solve_time_s, 2),
        solver_status=data.solver_status,
    )


# Pyomo models are not thread-safe and the cached HourlyData objects are written
# to while solving, so solves are serialised.
_solve_lock = threading.Lock()


def clear_hour(date: str, hour: int, directory: Path | None = None,
               solver_name: str | None = None) -> HourResult:
    """Solve one hour of one day and compare it with the published prices.

    Raises KeyError if the date or hour is not available.
    """
    directory = directory or data_dir()
    if date not in available_dates(directory):
        raise KeyError(f"No GME data for date {date} in {directory}")
    importer = load_day(str(directory), date)
    hourly = importer.get_hourly_data()
    if hour not in hourly:
        raise KeyError(f"No data for hour {hour} on {date} (available: 1-{max(hourly)})")

    with _solve_lock:
        t0 = time.perf_counter()
        data = build_and_solve(hourly[hour], solver_name=solver_name, verbose=False)
        elapsed = time.perf_counter() - t0
    return summarize(date, data, importer.get_real_prices().get(hour, {}), elapsed)
