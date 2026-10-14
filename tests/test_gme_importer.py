"""Check that the GME XML files for 2010-05-04 are parsed as expected."""

import pytest
from lxml import etree

from pun_clearing.conf import options
from pun_clearing.dataio.gme_importer import _text


def test_counts(gme_importer):
    hourly = gme_importer.get_hourly_data()
    assert sorted(hourly) == list(range(1, 25))
    assert len(gme_importer.get_zone_name_to_id()) == 18
    assert len(hourly[1].lines) == 18

    n_pun = sum(len(h.pun_bids) for h in hourly.values())
    n_demand = sum(len(h.demand_bids) for h in hourly.values())
    n_supply = sum(len(h.supply_bids) for h in hourly.values())
    assert (n_pun, n_demand, n_supply) == (10560, 972, 30802)


def test_hour_1_counts(gme_importer):
    hour = gme_importer.get_hourly_data()[1]
    assert (len(hour.pun_bids), len(hour.demand_bids), len(hour.supply_bids)) == (441, 41, 1207)


def test_pun_zones_flagged(gme_importer):
    zones = gme_importer.get_hourly_data()[1].zones.values()
    assert {z.name for z in zones if z.is_pun} == set(options.PUN_ZONES)


def test_pun_bids_only_in_pun_zones(gme_importer):
    hour = gme_importer.get_hourly_data()[1]
    assert all(hour.zones[b.zone_id].is_pun for b in hour.pun_bids)


def test_unpriced_bids_set_to_max_price(gme_importer):
    """GME uses price 0 for bids without a limit; the importer maps them to 3000."""
    hour = gme_importer.get_hourly_data()[1]
    bids = hour.pun_bids + hour.demand_bids
    assert all(b.price > 0 for b in bids)
    assert any(b.price == options.MAX_PRICE for b in bids)


def test_published_prices(gme_importer):
    prices = gme_importer.get_real_prices()
    assert prices[1]["PUN"] == pytest.approx(38.52)
    assert prices[9]["PUN"] == pytest.approx(68.498829)
    assert prices[9]["SICI"] == pytest.approx(154.01)
    assert prices[9]["SARD"] == pytest.approx(93.01)


def test_text_helper_reports_missing_tag():
    el = etree.fromstring("<Prezzi><Ora>1</Ora></Prezzi>")
    assert _text(el, "Ora") == "1"
    with pytest.raises(ValueError, match="Missing <PUN>"):
        _text(el, "PUN")
