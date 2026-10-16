"""
Run the PUN clearing model on real GME data.

Usage (after `pip install -e .`):
    pun-clearing --path data --date 20100504 --quiet
    pun-clearing --path data --date 20100504 --hours 8 9 13
    pun-clearing --path data --date 20100504 --jobs 8      # 8 hours in parallel
    pun-clearing --path data --date 20100504 --hours 9 --json

Reads the GME files of the date, solves each hour as its own MILP and compares
the PUN and zonal prices with the published ones.
"""

import argparse
import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field

from pun_clearing.conf import options
from pun_clearing.dataio.gme_importer import load_day
from pun_clearing.model.data_classes import HourlyData, Solution
from pun_clearing.model.pun_model import build_and_solve

logger = logging.getLogger(__name__)

# Two prices are "different" (market split) if they differ by more than this.
SPLIT_TOLERANCE = 1e-6
# Reported prices are rounded to this many decimals (GME publishes the PUN with 6).
DECIMALS = 6


def _round(x: float) -> float:
    return round(x, DECIMALS) + 0.0   # + 0.0 turns -0.0 into 0.0


# ========================================================================
#  SOLVING
# ========================================================================

def _solve_job(job: tuple[HourlyData, str, bool, int | None]) -> Solution | str:
    """Solve one hour; return the solution, or the error message if it failed."""
    data, solver, verbose, threads = job
    try:
        return build_and_solve(data, solver=solver, verbose=verbose, threads=threads)
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def solve_hours(hours: list[HourlyData], solver: str = options.SOLVER_NAME,
                verbose: bool = False, jobs: int = 1
                ) -> tuple[dict[int, Solution], dict[int, str]]:
    """Solve the hours, one at a time or `jobs` at once (one solver thread each).

    Returns the solutions and the error messages of the hours that failed.
    """
    threads = 1 if jobs > 1 else None
    job_args = [(data, solver, verbose, threads) for data in hours]
    solved: dict[int, Solution] = {}
    failed: dict[int, str] = {}

    with ProcessPoolExecutor(max_workers=jobs) if jobs > 1 else nullcontext() as pool:
        run = pool.map if pool is not None else map
        for data, outcome in zip(hours, run(_solve_job, job_args)):
            if isinstance(outcome, str):
                failed[data.period] = outcome
                logger.error(f"Hour {data.period}: FAILED — {outcome}")
            else:
                solved[data.period] = outcome
                logger.info(f"Hour {data.period}: solved in {outcome.solve_time_s:.2f}s, "
                            f"PUN = {outcome.pun_price:.6f}")
    return solved, failed


# ========================================================================
#  COMPARISON WITH GME
# ========================================================================

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
    solver_status: str = ""


def summarize(date: str, hour: int, solution: Solution, real_prices: dict[str, float],
              zone_ids: dict[str, int]) -> HourResult:
    """Compare a solved hour with the published prices."""
    zonal = []
    for name in options.PUN_ZONES:
        model = solution.zonal_prices[zone_ids[name]]
        real = real_prices.get(name)
        zonal.append(ZonePrice(zone=name, model=_round(model), real=real,
                               difference=None if real is None else _round(model - real)))

    prices = [z.model for z in zonal]
    split = max(prices) - min(prices) > SPLIT_TOLERANCE

    pun_real = real_prices.get("PUN")
    return HourResult(
        date=date,
        hour=hour,
        pun_model=_round(solution.pun_price),
        pun_real=pun_real,
        pun_difference=None if pun_real is None else _round(solution.pun_price - pun_real),
        market_split=split,
        zonal_prices=zonal if split else [],
        solve_time_s=round(solution.solve_time_s, 2),
        solver_status=solution.status,
    )


def _fmt(x: float | None, width: int) -> str:
    return f"{x:>{width}.6f}" if x is not None else f"{'N/A':>{width}}"


def print_report(date: str, results: list[HourResult], failed: dict[int, str],
                 wall_time: float) -> None:
    print("\n" + "=" * 80)
    print(f"  RESULTS SUMMARY - {date}")
    print("=" * 80)
    print(f"\n  {'Hour':>4}  {'PUN (Model)':>14}  {'PUN (Real)':>14}  "
          f"{'Difference':>12}  {'Split':>5}  {'Time (s)':>10}")
    print(f"  {'-'*4}  {'-'*14}  {'-'*14}  {'-'*12}  {'-'*5}  {'-'*10}")
    for r in results:
        split = "yes" if r.market_split else "no"
        print(f"  {r.hour:>4}  {_fmt(r.pun_model, 14)}  {_fmt(r.pun_real, 14)}  "
              f"{_fmt(r.pun_difference, 12)}  {split:>5}  {r.solve_time_s:>10.2f}")
        if r.solver_status != "optimal":
            print(f"        warning: solver status '{r.solver_status}' (not proven optimal)")

    if results:
        total = sum(r.solve_time_s for r in results)
        print(f"\n  Total solve time: {total:.2f}s  (average per hour {total / len(results):.2f}s)")
        print(f"  Wall-clock time:  {wall_time:.2f}s")

    split_hours = [r for r in results if r.market_split]
    if split_hours:
        print("\n  Zonal prices in hours with a market split:")
        print(f"  {'Hour':>4}  {'Zone':>6}  {'Model':>12}  {'Real':>12}  {'Difference':>12}")
        print(f"  {'-'*4}  {'-'*6}  {'-'*12}  {'-'*12}  {'-'*12}")
        for r in split_hours:
            for z in r.zonal_prices:
                print(f"  {r.hour:>4}  {z.zone:>6}  {_fmt(z.model, 12)}  {_fmt(z.real, 12)}  "
                      f"{_fmt(z.difference, 12)}")

    if failed:
        print("\n  FAILED hours (not in the tables above):")
        for hour, error in sorted(failed.items()):
            print(f"  {hour:>4}  {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PUN market clearing (Savelli et al. 2017)")
    parser.add_argument("--path", required=True, help="Directory with the GME files")
    parser.add_argument("--date", required=True, help="Date as YYYYMMDD (e.g. 20100504)")
    parser.add_argument("--solver", default=options.SOLVER_NAME,
                        help=f"Solver name (default: {options.SOLVER_NAME})")
    parser.add_argument("--hours", type=int, nargs="*", default=None,
                        help="Hours to solve (default: all)")
    parser.add_argument("--jobs", type=int, default=1,
                        help="Number of hours solved in parallel (default: 1)")
    parser.add_argument("--quiet", action="store_true", help="Suppress solver output")
    parser.add_argument("--json", action="store_true",
                        help="Print the results as JSON instead of a table")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")

    day = load_day(args.path, args.date)
    hours = args.hours or sorted(day.hours)
    for hour in hours:
        if hour not in day.hours:
            logger.warning(f"No data for hour {hour}, skipping")

    t0 = time.perf_counter()
    solved, failed = solve_hours([day.hours[h] for h in hours if h in day.hours], args.solver,
                                 verbose=not (args.quiet or args.json), jobs=max(1, args.jobs))
    wall_time = time.perf_counter() - t0

    results = [summarize(args.date, hour, solution, day.real_prices.get(hour, {}), day.zone_ids)
               for hour, solution in sorted(solved.items())]
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_report(args.date, results, failed, wall_time)


if __name__ == "__main__":
    main()
