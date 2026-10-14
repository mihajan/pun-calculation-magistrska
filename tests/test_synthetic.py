"""
Solve the MILP on a tiny synthetic market whose result can be checked by hand.

Market setup (hour 1):
  - Zone 1 (NORD): PUN zone
      supply:      3 offers at 10, 20, 30 €/MWh, 100 MWh each
      PUN demand:  4 bids at 50, 35, 25, 15 €/MWh, 80 MWh each
  - Zone 2 (XFRA): non-PUN zone
      supply:      1 offer at 5 €/MWh, 50 MWh
      demand:      1 bid at 40 €/MWh, 30 MWh
  - Line NORD <-> XFRA, 50 MWh in each direction

Hand solution: XFRA exports 20 MWh (uncongested), so both zones share one
price. Supply at 5, 10, 20 (250 MWh) meets demand 80 + 80 + 30 = 190 MWh plus
60 MWh of the PUN bid at 25 €/MWh, which is at-the-money and sets π = 25.
"""

import pytest

from pun_clearing.model.data_classes import (
    DemandBid, HourlyData, Line, PunBid, SupplyBid, Zone,
)
from pun_clearing.model.pun_model import build_and_solve

NORD, XFRA = 1, 2


@pytest.fixture(scope="module")
def solved() -> HourlyData:
    zones = {
        NORD: Zone(zone_id=NORD, name="NORD", is_pun=True),
        XFRA: Zone(zone_id=XFRA, name="XFRA", is_pun=False),
    }
    lines = [Line(line_id=1, from_zone_id=NORD, to_zone_id=XFRA,
                  capacity={1: 50.0}, capacity_rev={1: 50.0})]
    supply = [
        SupplyBid(bid_id=1, zone_id=NORD, period=1, volume=100.0, price=10.0),
        SupplyBid(bid_id=2, zone_id=NORD, period=1, volume=100.0, price=20.0),
        SupplyBid(bid_id=3, zone_id=NORD, period=1, volume=100.0, price=30.0),
        SupplyBid(bid_id=4, zone_id=XFRA, period=1, volume=50.0, price=5.0),
    ]
    demand = [DemandBid(bid_id=5, zone_id=XFRA, period=1, volume=30.0, price=40.0)]
    pun = [
        PunBid(bid_id=6, zone_id=NORD, period=1, volume=80.0, price=50.0, merit_order=1),
        PunBid(bid_id=7, zone_id=NORD, period=1, volume=80.0, price=35.0, merit_order=2),
        PunBid(bid_id=8, zone_id=NORD, period=1, volume=80.0, price=25.0, merit_order=3),
        PunBid(bid_id=9, zone_id=NORD, period=1, volume=80.0, price=15.0, merit_order=4),
    ]
    data = HourlyData(period=1, zones=zones, lines=lines, supply_bids=supply,
                      demand_bids=demand, pun_bids=pun)
    return build_and_solve(data, solver_name="appsi_highs")


def test_pun_price(solved):
    assert solved.pun_price == pytest.approx(25.0, abs=1e-6)


def test_zonal_prices_equal_when_uncongested(solved):
    assert solved.zonal_prices[NORD] == pytest.approx(25.0, abs=1e-6)
    assert solved.zonal_prices[XFRA] == pytest.approx(25.0, abs=1e-6)


def test_social_welfare(solved):
    assert solved.social_welfare == pytest.approx(6250.0, abs=1e-4)


def test_pun_bids_itm_atm_otm(solved):
    """Bids above π fully accepted, the bid at π partially, the bid below rejected."""
    acceptance = [bid.acceptance for bid in solved.pun_bids]
    assert acceptance == pytest.approx([1.0, 1.0, 0.75, 0.0], abs=1e-6)


def test_supply_and_non_pun_demand(solved):
    assert [b.acceptance for b in solved.supply_bids] == pytest.approx(
        [1.0, 1.0, 0.0, 1.0], abs=1e-6)
    assert [b.acceptance for b in solved.demand_bids] == pytest.approx([1.0], abs=1e-6)


def test_energy_balance(solved):
    supplied = sum(b.acceptance * b.volume for b in solved.supply_bids)
    consumed = sum(b.acceptance * b.volume
                   for b in solved.demand_bids + solved.pun_bids)
    assert supplied == pytest.approx(consumed, abs=1e-6)


def test_pun_is_weighted_average_of_zonal_prices(solved):
    """Paper eq. (1): π = Σ ζ_i d_k / Σ d_k over executed PUN demand."""
    executed = [(b.acceptance * b.volume, solved.zonal_prices[b.zone_id])
                for b in solved.pun_bids]
    total = sum(d for d, _ in executed)
    weighted = sum(d * zeta for d, zeta in executed)
    assert solved.pun_price == pytest.approx(weighted / total, abs=1e-6)
