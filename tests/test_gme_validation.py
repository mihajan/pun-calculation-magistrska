"""
Validate the MILP against the prices GME published for 2010-05-04.

These are the reference results: any change to the code (refactoring,
speed-ups) must keep them passing. Each hour takes ~20-60 s to solve.

  hour 2   no market split (one price everywhere)
  hour 8   2 price zones (SICI separate)
  hour 9   3 price zones (SICI, SARD separate)
  hour 13  4 price zones (NORD, SICI, SARD separate)
"""

import pytest

from pun_clearing.conf import options
from pun_clearing.model.pun_model import build_and_solve

pytestmark = pytest.mark.slow

# GME publishes the PUN with 6 decimals and zonal prices with 2.
PUN_TOL = 1e-5
ZONAL_TOL = 1e-3


@pytest.mark.parametrize("hour", [2, 8, 9, 13])
def test_matches_published_prices(gme_importer, hour):
    data = gme_importer.get_hourly_data()[hour]
    real = gme_importer.get_real_prices()[hour]
    zone_ids = gme_importer.get_zone_name_to_id()

    build_and_solve(data, solver_name="appsi_highs")

    assert data.pun_price == pytest.approx(real["PUN"], abs=PUN_TOL)
    for zone in options.PUN_ZONES:
        assert data.zonal_prices[zone_ids[zone]] == pytest.approx(real[zone], abs=ZONAL_TOL), zone
