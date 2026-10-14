"""
Run the PUN clearing model on real GME data.

Usage (after `pip install -e .`):
    pun-clearing --path data --date 20100504 --quiet
    pun-clearing --path data --date 20100504 --hours 8 9 13

This will:
  1. Read the GME XML files for the given date
  2. Solve 24 independent hourly MILP problems
  3. Compare the computed PUN and zonal prices with published values
  4. Print a summary table

Prerequisites:
  - Download the 4 GME XML files for the date into --path
  - Pyomo and a MILP solver (default: HiGHS via highspy, installed with the package)
"""

import argparse
import logging
import time

from pun_clearing.dataio.gme_importer import GMEImporter
from pun_clearing.model.pun_model import build_and_solve
from pun_clearing.conf import options

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='PUN Market Clearing — Savelli et al. (2017)')
    parser.add_argument('--path', required=True,
                        help='Directory containing GME XML files')
    parser.add_argument('--date', required=True,
                        help='Date in YYYYMMDD format (e.g. 20100504)')
    parser.add_argument('--solver', default=options.SOLVER_NAME,
                        help=f'Solver name (default: {options.SOLVER_NAME})')
    parser.add_argument('--hours', type=int, nargs='*', default=None,
                        help='Specific hours to solve (default: all 1-24)')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress solver output')
    args = parser.parse_args()

    if args.quiet:
        options.VERBOSE = False

    # ---- Step 1: Load data ----
    logger.info(f"Loading GME data for {args.date} from {args.path}")
    importer = GMEImporter(path=args.path, date=args.date)
    hourly_data = importer.get_hourly_data()
    real_prices = importer.get_real_prices()
    zone_map = importer.get_zone_name_to_id()

    hours_to_solve = args.hours or sorted(hourly_data.keys())

    # ---- Step 2: Solve each hour ----
    results = {}
    total_time = 0.0

    for hour in hours_to_solve:
        if hour not in hourly_data:
            logger.warning(f"No data for hour {hour}, skipping")
            continue

        data = hourly_data[hour]
        logger.info(f"--- Hour {hour}: {len(data.pun_bids)} PUN bids, "
                    f"{len(data.demand_bids)} demand, {len(data.supply_bids)} supply ---")

        t0 = time.time()
        try:
            build_and_solve(data, solver_name=args.solver)
            elapsed = time.time() - t0
            total_time += elapsed
            results[hour] = data
            logger.info(f"Hour {hour}: solved in {elapsed:.2f}s, "
                        f"PUN = {data.pun_price:.6f}")
        except Exception as e:
            elapsed = time.time() - t0
            logger.error(f"Hour {hour}: FAILED after {elapsed:.2f}s — {e}")

    # ---- Step 3: Print comparison table ----
    print("\n" + "=" * 80)
    print(f"  RESULTS SUMMARY — {args.date}")
    print("=" * 80)
    print(f"\n  {'Hour':>4}  {'PUN (Model)':>14}  {'PUN (Real)':>14}  "
          f"{'Difference':>12}  {'Time (s)':>10}")
    print(f"  {'—'*4}  {'—'*14}  {'—'*14}  {'—'*12}  {'—'*10}")

    for hour in sorted(results.keys()):
        data = results[hour]
        model_pun = data.pun_price

        # Get real PUN if available
        real_pun = None
        if hour in real_prices and 'PUN' in real_prices[hour]:
            real_pun = real_prices[hour]['PUN']

        if real_pun is not None:
            diff = model_pun - real_pun
            print(f"  {hour:>4}  {model_pun:>14.6f}  {real_pun:>14.6f}  "
                  f"{diff:>12.6f}  {0:>10.2f}")
        else:
            print(f"  {hour:>4}  {model_pun:>14.6f}  {'N/A':>14}  "
                  f"{'N/A':>12}  {0:>10.2f}")

    print(f"\n  Total solve time: {total_time:.2f}s")
    print(f"  Average per hour: {total_time / len(results):.2f}s" if results else "")

    # ---- Zonal price comparison for hours with market split ----
    print(f"\n  {'Hour':>4}  {'Zone':>6}  {'zeta (Model)':>12}  {'zeta (Real)':>12}  "
          f"{'Diff':>10}")
    print(f"  {'—'*4}  {'—'*6}  {'—'*12}  {'—'*12}  {'—'*10}")

    for hour in sorted(results.keys()):
        data = results[hour]
        if hour not in real_prices:
            continue

        for zone_name, zone_id in sorted(zone_map.items()):
            if zone_name in ('PUN', 'NAT'):
                continue
            model_price = data.zonal_prices.get(zone_id)
            real_price = real_prices[hour].get(zone_name)

            if model_price is not None and real_price is not None:
                diff = model_price - real_price
                if abs(diff) > 0.01:  # only show if there's a meaningful difference
                    print(f"  {hour:>4}  {zone_name:>6}  {model_price:>12.6f}  "
                          f"{real_price:>12.6f}  {diff:>10.6f}")


if __name__ == "__main__":
    main()
