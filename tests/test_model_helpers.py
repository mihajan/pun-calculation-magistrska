"""Unit tests for helpers used while building the MILP."""

from pun_clearing.model.data_classes import PunOrder
from pun_clearing.model.pun_model import _indices_by_zone, _next_lower_price_pairs


def _bids(prices, zone_id=1):
    return [PunOrder(zone_id=zone_id, volume=1.0, price=p, merit_order=n)
            for n, p in enumerate(prices)]


def test_next_lower_price_pairs_skips_ties():
    # indices:     0     1     2     3     4
    bids = _bids([30.0, 50.0, 30.0, 10.0, 3000.0])
    # Descending order: 4 (fixed, skipped), 1, 0, 2, 3
    # 1 → 0 (50 > 30); 0 → 3 and 2 → 3 (the tie 0/2 is skipped); 3 has no lower bid
    assert _next_lower_price_pairs(bids, fixed={4}) == [(1, 0), (0, 3), (2, 3)]


def test_indices_by_zone():
    bids = _bids([1.0]) + _bids([2.0], zone_id=7)
    by_zone = _indices_by_zone(bids)
    assert by_zone[1] == [0]
    assert by_zone[7] == [1]
    assert by_zone[99] == []
