"""Check that the GME files for 2010-05-04 are parsed as expected."""

import shutil

import pytest
from lxml import etree

from pun_clearing.conf import options
from pun_clearing.dataio.gme_importer import _text, load_day

from conftest import DATA_DIR, GME_DATE


def test_counts(gme_day):
    assert sorted(gme_day.hours) == list(range(1, 25))
    assert len(gme_day.zone_ids) == 18
    # 18 interconnections, each with a limit in both directions
    assert len(gme_day.hours[1].flow_limits) == 36

    hours = gme_day.hours.values()
    n_pun = sum(len(h.pun_bids) for h in hours)
    n_demand = sum(len(h.demand_bids) for h in hours)
    n_supply = sum(len(h.supply_bids) for h in hours)
    assert (n_pun, n_demand, n_supply) == (10560, 972, 30802)


def test_hour_1_counts(gme_day):
    hour = gme_day.hours[1]
    assert (len(hour.pun_bids), len(hour.demand_bids), len(hour.supply_bids)) == (441, 41, 1207)


def test_pun_zones_flagged(gme_day):
    zones = gme_day.hours[1].zones.values()
    assert {z.name for z in zones if z.is_pun} == set(options.PUN_ZONES)


def test_pun_bids_only_in_pun_zones(gme_day):
    hour = gme_day.hours[1]
    assert all(hour.zones[b.zone_id].is_pun for b in hour.pun_bids)


def test_unpriced_bids_set_to_max_price(gme_day):
    """GME uses price 0 for bids without a limit; the importer maps them to 3000."""
    hour = gme_day.hours[1]
    bids = hour.pun_bids + hour.demand_bids
    assert all(b.price > 0 for b in bids)
    assert any(b.price == options.MAX_PRICE for b in bids)


def test_published_prices(gme_day):
    prices = gme_day.real_prices
    assert prices[1]["PUN"] == pytest.approx(38.52)
    assert prices[9]["PUN"] == pytest.approx(68.498829)
    assert prices[9]["SICI"] == pytest.approx(154.01)
    assert prices[9]["SARD"] == pytest.approx(93.01)


@pytest.fixture(scope="module")
def zips_only(tmp_path_factory):
    """A folder with only the zipped GME files (as in the repository)."""
    folder = tmp_path_factory.mktemp("gme_zips")
    for zip_path in DATA_DIR.glob("*.zip"):
        shutil.copy(zip_path, folder)
    return folder


def test_reads_from_zips_without_extracting(zips_only, gme_day):
    before = sorted(p.name for p in zips_only.iterdir())
    day = load_day(zips_only, GME_DATE)
    assert sorted(p.name for p in zips_only.iterdir()) == before   # nothing written
    assert day.hours[9] == gme_day.hours[9]
    assert day.real_prices == gme_day.real_prices


def test_other_date_is_not_loaded(zips_only):
    """Zips of 2010-05-04 must not be used when another date is asked for."""
    with pytest.raises(FileNotFoundError, match="20100505MGPLimitiTransito"):
        load_day(zips_only, "20100505")


def test_text_helper_reports_missing_tag():
    el = etree.fromstring("<Prezzi><Ora>1</Ora></Prezzi>")
    assert _text(el, "Ora") == "1"
    with pytest.raises(ValueError, match="Missing <PUN>"):
        _text(el, "PUN")
