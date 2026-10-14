"""Unit tests for helpers used while building the MILP."""

from pun_clearing.conf import options
from pun_clearing.model.data_classes import HourlyData, PunBid
from pun_clearing.model.pun_model import _indices_by_zone, _next_lower_price_pairs


def _hour_with_prices(prices):
    bids = [PunBid(bid_id=n, zone_id=1, period=1, volume=1.0, price=p, merit_order=n)
            for n, p in enumerate(prices)]
    return HourlyData(period=1, zones={}, lines=[], supply_bids=[], demand_bids=[],
                      pun_bids=bids)


def test_next_lower_price_pairs_skips_ties():
    # indices:          0     1     2     3     4
    data = _hour_with_prices([30.0, 50.0, 30.0, 10.0, options.MAX_PRICE])
    # Descending order: 4 (max, skipped), 1, 0, 2, 3
    # 1 → 0 (50 > 30); 0 → 3 and 2 → 3 (the tie 0/2 is skipped); 3 has no lower bid
    assert _next_lower_price_pairs(data) == [(1, 0), (0, 3), (2, 3)]


def test_identical_bids_are_distinct_objects():
    """eq=False keeps identical orders apart (the model indexes bids by position)."""
    a = PunBid(bid_id=1, zone_id=1, period=1, volume=5.0, price=20.0, merit_order=1)
    b = PunBid(bid_id=1, zone_id=1, period=1, volume=5.0, price=20.0, merit_order=1)
    assert a != b
    assert [a, b].index(b) == 1


def test_indices_by_zone():
    data = _hour_with_prices([1.0, 2.0])
    data.pun_bids[1].zone_id = 7
    by_zone = _indices_by_zone(data.pun_bids)
    assert by_zone[1] == [0]
    assert by_zone[7] == [1]
    assert by_zone[99] == []
