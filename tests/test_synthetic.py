"""
Solve the MILP on tiny synthetic markets whose results can be checked by hand.

Market setup:
  - Zone 1 (NORD): PUN zone
      supply:      3 offers at 10, 20, 30 €/MWh, 100 MWh each
      PUN demand:  4 bids at 50, 35, 25, 15 €/MWh, 80 MWh each
  - Zone 2 (XFRA): non-PUN zone
      supply:      1 offer at 5 €/MWh, 50 MWh
      demand:      1 bid at 40 €/MWh, 30 MWh
  - NORD <-> XFRA: 50 MWh in each direction

Hand solution: XFRA exports 20 MWh (uncongested), so both zones share one
price. Supply at 5, 10, 20 (250 MWh) meets demand 80 + 80 + 30 = 190 MWh plus
60 MWh of the PUN bid at 25 €/MWh, which is at-the-money and sets π = 25.
"""

from dataclasses import replace

import pytest

from market_rules import market_rule_violations
from pun_clearing.model.data_classes import HourlyData, Order, PunOrder, Solution, Zone
from pun_clearing.model.pun_model import build_and_solve
from pun_clearing.run_hourly import solve_hours

NORD, XFRA = 1, 2


def make_market(period: int = 1) -> HourlyData:
    return HourlyData(
        period=period,
        zones={NORD: Zone(zone_id=NORD, name="NORD", is_pun=True),
               XFRA: Zone(zone_id=XFRA, name="XFRA", is_pun=False)},
        flow_limits={(NORD, XFRA): 50.0, (XFRA, NORD): 50.0},
        supply_bids=[Order(zone_id=NORD, volume=100.0, price=10.0),
                     Order(zone_id=NORD, volume=100.0, price=20.0),
                     Order(zone_id=NORD, volume=100.0, price=30.0),
                     Order(zone_id=XFRA, volume=50.0, price=5.0)],
        demand_bids=[Order(zone_id=XFRA, volume=30.0, price=40.0)],
        pun_bids=[PunOrder(zone_id=NORD, volume=80.0, price=50.0, merit_order=1),
                  PunOrder(zone_id=NORD, volume=80.0, price=35.0, merit_order=2),
                  PunOrder(zone_id=NORD, volume=80.0, price=25.0, merit_order=3),
                  PunOrder(zone_id=NORD, volume=80.0, price=15.0, merit_order=4)],
    )


@pytest.fixture(scope="module")
def market() -> HourlyData:
    return make_market()


@pytest.fixture(scope="module")
def solved(market) -> Solution:
    return build_and_solve(market)


def test_pun_price(solved):
    assert solved.pun_price == pytest.approx(25.0, abs=1e-6)


def test_zonal_prices_equal_when_uncongested(solved):
    assert solved.zonal_prices[NORD] == pytest.approx(25.0, abs=1e-6)
    assert solved.zonal_prices[XFRA] == pytest.approx(25.0, abs=1e-6)


def test_social_welfare(solved):
    assert solved.social_welfare == pytest.approx(6250.0, abs=1e-4)


def test_solver_status(solved):
    assert solved.status == "optimal"


def test_pun_bids_itm_atm_otm(solved):
    """Bids above π fully accepted, the bid at π partially, the bid below rejected."""
    assert solved.acceptance.pun == pytest.approx([1.0, 1.0, 0.75, 0.0], abs=1e-6)


def test_supply_and_non_pun_demand(solved):
    assert solved.acceptance.supply == pytest.approx([1.0, 1.0, 0.0, 1.0], abs=1e-6)
    assert solved.acceptance.demand == pytest.approx([1.0], abs=1e-6)


def test_energy_balance(market, solved):
    def executed(orders, shares):
        return sum(o.volume * a for o, a in zip(orders, shares))

    supplied = executed(market.supply_bids, solved.acceptance.supply)
    consumed = (executed(market.demand_bids, solved.acceptance.demand)
                + executed(market.pun_bids, solved.acceptance.pun))
    assert supplied == pytest.approx(consumed, abs=1e-6)


def test_pun_is_weighted_average_of_zonal_prices(market, solved):
    """Paper eq. (1): π = Σ ζ_i d_k / Σ d_k over executed PUN demand."""
    executed = [(o.volume * a, solved.zonal_prices[o.zone_id])
                for o, a in zip(market.pun_bids, solved.acceptance.pun)]
    total = sum(d for d, _ in executed)
    weighted = sum(d * zeta for d, zeta in executed)
    assert solved.pun_price == pytest.approx(weighted / total, abs=1e-6)


def test_input_is_not_modified(market, solved):
    """Solving returns a Solution and leaves the input unchanged."""
    assert market == make_market()


def test_parallel_solve_matches_sequential(solved):
    """Solving in worker processes (--jobs) gives the same result."""
    results, failed = solve_hours([make_market(1), make_market(2)], jobs=2)
    assert failed == {}
    assert sorted(results) == [1, 2]
    for solution in results.values():
        assert solution.pun_price == pytest.approx(solved.pun_price, abs=1e-6)
        assert solution.zonal_prices == pytest.approx(solved.zonal_prices, abs=1e-6)


def test_failed_hour_is_reported():
    """An hour that cannot be solved ends up in `failed`, not silently dropped."""
    results, failed = solve_hours([make_market(1)], solver="no_such_solver")
    assert results == {}
    assert "not available" in failed[1]


def test_market_rules_hold(market, solved):
    assert market_rule_violations(market, solved) == []


def test_market_rule_check_catches_a_wrong_solution(market, solved):
    """The checker itself must flag a solution that breaks the rules."""
    wrong = replace(solved, acceptance=replace(solved.acceptance, pun=[1.0, 0.0, 0.75, 0.0]))
    assert any("PUN bid 1" in e for e in market_rule_violations(market, wrong))


# ------------------------------------------------------------------------
#  Congested market: two PUN zones, link of 10 MWh
#
#  Zone A: offer 100 MWh at 10; PUN bids 40 MWh (no limit) and 10 MWh at 15
#  Zone B: offer 100 MWh at 30; PUN bids 40 MWh (no limit) and 10 MWh at 25
#
#  B imports only 10 MWh, so ζ_A = 10 and ζ_B = 30.
#  The bid at 25 must be accepted: rejecting it would give π = 20 < 25.
#  With it, π = (40·10 + 50·30) / 90 = 21.111, so the bid at 15 is rejected,
#  although 15 > ζ_A — the PUN, not the zonal price, decides.
#  Welfare = 3000·80 + 25·10 − (10·50 + 30·40) = 238 550.
# ------------------------------------------------------------------------

A, B = 1, 2


@pytest.fixture(scope="module")
def congested() -> HourlyData:
    return HourlyData(
        period=1,
        zones={A: Zone(zone_id=A, name="NORD", is_pun=True),
               B: Zone(zone_id=B, name="SICI", is_pun=True)},
        flow_limits={(A, B): 10.0, (B, A): 10.0},
        supply_bids=[Order(zone_id=A, volume=100.0, price=10.0),
                     Order(zone_id=B, volume=100.0, price=30.0)],
        demand_bids=[],
        pun_bids=[PunOrder(zone_id=A, volume=40.0, price=3000.0, merit_order=1),
                  PunOrder(zone_id=B, volume=40.0, price=3000.0, merit_order=2),
                  PunOrder(zone_id=B, volume=10.0, price=25.0, merit_order=3),
                  PunOrder(zone_id=A, volume=10.0, price=15.0, merit_order=4)],
    )


@pytest.fixture(scope="module")
def congested_solved(congested) -> Solution:
    return build_and_solve(congested)


def test_congestion_splits_zonal_prices(congested_solved):
    assert congested_solved.zonal_prices[A] == pytest.approx(10.0, abs=1e-6)
    assert congested_solved.zonal_prices[B] == pytest.approx(30.0, abs=1e-6)


def test_pun_rule_decides_acceptance(congested_solved):
    assert congested_solved.pun_price == pytest.approx(190 / 9, abs=1e-6)
    assert congested_solved.acceptance.pun == pytest.approx([1.0, 1.0, 1.0, 0.0], abs=1e-6)
    assert congested_solved.acceptance.supply == pytest.approx([0.5, 0.4], abs=1e-6)


def test_congested_welfare(congested_solved):
    assert congested_solved.social_welfare == pytest.approx(238550.0, abs=1e-4)


def test_congested_market_rules(congested, congested_solved):
    assert market_rule_violations(congested, congested_solved) == []
