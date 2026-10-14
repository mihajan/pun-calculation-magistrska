"""
Run the PUN clearing model on real GME data.

Usage (after `pip install -e .`):
    pun-clearing --path data --date 20100504 --quiet
    pun-clearing --path data --date 20100504 --hours 8 9 13
    pun-clearing --path data --date 20100504 --jobs 8      # 8 hours in parallel
    pun-clearing --path data --date 20100504 --hours 9 --quiet --json

This will:
  1. Read the GME XML files for the given date
  2. Solve the independent hourly MILP problems (optionally in parallel)
  3. Compare the computed PUN and zonal prices with published values
  4. Print a summary table

Prerequisites:
  - Download the 4 GME XML files for the date into --path
  - Pyomo and a MILP solver (default: HiGHS via highspy, installed with the package)
"""

import argparse
import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict

from pun_clearing.conf import options
from pun_clearing.dataio.gme_importer import GMEImporter
from pun_clearing.model.data_classes import HourlyData
from pun_clearing.model.pun_model import build_and_solve
from pun_clearing.service import HourResult, summarize

logger = logging.getLogger(__name__)


def solve_hour(data: HourlyData, solver_name: str,
               verbose: bool) -> tuple[HourlyData, float]:
    """Solve one hour and return the filled-in data with the wall-clock time."""
    t0 = time.perf_counter()
    build_and_solve(data, solver_name=solver_name, verbose=verbose)
    return data, time.perf_counter() - t0


def _init_worker() -> None:
    """Run each parallel solve on one thread so N workers use N cores."""
    logging.basicConfig(level=logging.WARNING)
    options.HIGHS_OPTIONS["threads"] = 1


def solve_hours(hourly_data: dict[int, HourlyData], hours: list[int], solver_name: str,
                verbose: bool, jobs: int) -> dict[int, tuple[HourlyData, float]]:
    """Solve the given hours, sequentially (jobs=1) or in a process pool."""
    results: dict[int, tuple[HourlyData, float]] = {}

    if jobs == 1:
        for hour in hours:
            data = hourly_data[hour]
            logger.info(f"--- Hour {hour}: {len(data.pun_bids)} PUN bids, "
                        f"{len(data.demand_bids)} demand, {len(data.supply_bids)} supply ---")
            try:
                results[hour] = solve_hour(data, solver_name, verbose)
                logger.info(f"Hour {hour}: solved in {results[hour][1]:.2f}s, "
                            f"PUN = {data.pun_price:.6f}")
            except Exception as e:
                logger.error(f"Hour {hour}: FAILED — {e}")
        return results

    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker) as pool:
        futures = {pool.submit(solve_hour, hourly_data[h], solver_name, False): h
                   for h in hours}
        for future in as_completed(futures):
            hour = futures[future]
            try:
                results[hour] = future.result()
                logger.info(f"Hour {hour}: solved in {results[hour][1]:.2f}s, "
                            f"PUN = {results[hour][0].pun_price:.6f}")
            except Exception as e:
                logger.error(f"Hour {hour}: FAILED — {e}")
    return results


def print_report(date: str, results: list[HourResult], wall_time: float) -> None:
    print("\n" + "=" * 80)
    print(f"  RESULTS SUMMARY - {date}")
    print("=" * 80)
    print(f"\n  {'Hour':>4}  {'PUN (Model)':>14}  {'PUN (Real)':>14}  "
          f"{'Difference':>12}  {'Split':>5}  {'Time (s)':>10}")
    print(f"  {'-'*4}  {'-'*14}  {'-'*14}  {'-'*12}  {'-'*5}  {'-'*10}")
    for r in results:
        real = f"{r.pun_real:>14.6f}" if r.pun_real is not None else f"{'N/A':>14}"
        diff = f"{r.pun_difference:>12.6f}" if r.pun_difference is not None else f"{'N/A':>12}"
        split = "yes" if r.market_split else "no"
        print(f"  {r.hour:>4}  {r.pun_model:>14.6f}  {real}  {diff}  {split:>5}  "
              f"{r.solve_time_s:>10.2f}")
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
                real = f"{z.real:>12.6f}" if z.real is not None else f"{'N/A':>12}"
                diff = f"{z.difference:>12.6f}" if z.difference is not None else f"{'N/A':>12}"
                print(f"  {r.hour:>4}  {z.zone:>6}  {z.model:>12.6f}  {real}  {diff}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description='PUN Market Clearing - Savelli et al. (2017)')
    parser.add_argument('--path', required=True,
                        help='Directory containing GME XML files')
    parser.add_argument('--date', required=True,
                        help='Date in YYYYMMDD format (e.g. 20100504)')
    parser.add_argument('--solver', default=options.SOLVER_NAME,
                        help=f'Solver name (default: {options.SOLVER_NAME})')
    parser.add_argument('--hours', type=int, nargs='*', default=None,
                        help='Specific hours to solve (default: all)')
    parser.add_argument('--jobs', type=int, default=1,
                        help='Number of hours solved in parallel (default: 1)')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress solver output')
    parser.add_argument('--json', action='store_true',
                        help='Print the results as JSON instead of a table')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s',
                        datefmt='%H:%M:%S')

    logger.info(f"Loading GME data for {args.date} from {args.path}")
    importer = GMEImporter(path=args.path, date=args.date)
    hourly_data = importer.get_hourly_data()

    hours = args.hours or sorted(hourly_data)
    missing = [h for h in hours if h not in hourly_data]
    for hour in missing:
        logger.warning(f"No data for hour {hour}, skipping")
    hours = [h for h in hours if h in hourly_data]

    t0 = time.perf_counter()
    results = solve_hours(hourly_data, hours, args.solver,
                          verbose=not (args.quiet or args.json), jobs=max(1, args.jobs))
    wall_time = time.perf_counter() - t0

    real_prices = importer.get_real_prices()
    summaries = [summarize(args.date, data, real_prices.get(hour, {}), elapsed)
                 for hour, (data, elapsed) in sorted(results.items())]
    if args.json:
        print(json.dumps([asdict(r) for r in summaries], indent=2))
    else:
        print_report(args.date, summaries, wall_time)


if __name__ == "__main__":
    main()
