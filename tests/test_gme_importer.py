"""
Check the importer against the raw GME files for 2010-05-04.

The raw files are read here directly from the zips with plain XPath, independently
of the importer, and compared with what load_day() produced.
"""

import shutil
import zipfile
from collections import Counter, defaultdict

import pytest
from lxml import etree

from pun_clearing.conf import options
from pun_clearing.dataio.gme_importer import _text, load_day

from conftest import DATA_DIR, GME_DATE

INVALID_STATUS = ("INC", "REP", "REV")


def _raw(pattern: str) -> etree._Element:
    """Root of the raw XML <date><pattern>.xml, read from whichever zip contains it."""
    for zip_path in DATA_DIR.glob("*.zip"):
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if name == f"{GME_DATE}{pattern}.xml":
                    return etree.fromstring(zf.read(name))
    raise FileNotFoundError(pattern)


@pytest.fixture(scope="module")
def raw_orders():
    """Valid raw orders as (hour, purpose, zone, unit, price, volume)."""
    rows = []
    for el in _raw("MGPOffertePubbliche").iter("OfferteOperatori"):
        status = el.findtext("STATUS_CD")
        if status is None or status in INVALID_STATUS:
            continue
        rows.append((int(el.findtext("INTERVAL_NO", "")),
                     el.findtext("PURPOSE_CD", ""),
                     el.findtext("ZONE_CD", ""),
                     el.findtext("UNIT_REFERENCE_NO", ""),
                     float(el.findtext("ENERGY_PRICE_NO", "")),
                     float(el.findtext("ADJ_QUANTITY_NO", ""))))
    return rows


def _is_pun_bid(row) -> bool:
    _, purpose, zone, unit, _, _ = row
    return purpose == "BID" and zone in options.PUN_ZONES and not unit.startswith("UP_")


def test_order_counts_per_hour_match_raw_file(gme_day, raw_orders):
    raw: Counter[tuple[int, str]] = Counter()
    for row in raw_orders:
        hour, purpose = row[0], row[1]
        kind = "supply" if purpose == "OFF" else "pun" if _is_pun_bid(row) else "demand"
        raw[hour, kind] += 1
    for hour, data in gme_day.hours.items():
        assert len(data.supply_bids) == raw[hour, "supply"], hour
        assert len(data.pun_bids) == raw[hour, "pun"], hour
        assert len(data.demand_bids) == raw[hour, "demand"], hour


def test_volumes_match_raw_file(gme_day, raw_orders):
    raw: defaultdict[tuple[int, str], float] = defaultdict(float)
    for hour, purpose, *_, volume in raw_orders:
        raw[hour, purpose] += volume
    for hour, data in gme_day.hours.items():
        offered = sum(o.volume for o in data.supply_bids)
        bid = sum(o.volume for o in data.demand_bids + data.pun_bids)
        assert offered == pytest.approx(raw[hour, "OFF"]), hour
        assert bid == pytest.approx(raw[hour, "BID"]), hour


def test_unpriced_bids_set_to_max_price(gme_day, raw_orders):
    """GME writes price 0 for bids without a limit; the importer maps them to 3000."""
    raw_unpriced = sum(1 for row in raw_orders if row[1] == "BID" and row[4] in (0.0, 3000.0))
    imported = sum(1 for d in gme_day.hours.values()
                   for o in d.pun_bids + d.demand_bids if o.price == options.MAX_PRICE)
    assert imported == raw_unpriced
    assert all(o.price > 0 for d in gme_day.hours.values() for o in d.pun_bids + d.demand_bids)


def test_pun_bids_only_in_pun_zones(gme_day):
    for data in gme_day.hours.values():
        assert all(data.zones[o.zone_id].is_pun for o in data.pun_bids)
    zones = gme_day.hours[1].zones.values()
    assert {z.name for z in zones if z.is_pun} == set(options.PUN_ZONES)


def test_flow_limits_match_raw_file(gme_day):
    rows = list(_raw("MGPLimitiTransito").iter("LimitiTransito"))
    for el in rows:
        hour = int(el.findtext("Ora", ""))
        link = (gme_day.zone_ids[el.findtext("Da", "")], gme_day.zone_ids[el.findtext("A", "")])
        limit = float(el.findtext("Limite", "").replace(",", "."))
        assert gme_day.hours[hour].flow_limits[link] == limit
    assert sum(len(d.flow_limits) for d in gme_day.hours.values()) == len(rows)


def test_published_prices_match_raw_file(gme_day):
    for el in _raw("MGPPrezzi").iter("Prezzi"):
        hour = int(el.findtext("Ora", ""))
        for name in ["PUN", *options.PUN_ZONES]:
            raw = float(el.findtext(name, "").replace(",", "."))
            assert gme_day.real_prices[hour][name] == raw, (hour, name)


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
