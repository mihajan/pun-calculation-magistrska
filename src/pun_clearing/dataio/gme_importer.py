"""
Read the GME files of one day of the Italian day-ahead market (MGP).

Files for a date YYYYMMDD, as .xml or inside a .zip, all in one folder
(download: https://www.mercatoelettrico.org/en-us/Home/Results/Electricity/MGP/Download/Download):

  YYYYMMDDMGPOffertePubbliche   all orders                       (required)
  YYYYMMDDMGPLimitiTransito     transmission limits              (required)
  YYYYMMDDMGPPrezzi             published prices, for validation (optional)

Usage:
    day = load_day("data", "20100504")
    day.hours[9]          # HourlyData for hour 9
    day.real_prices[9]    # {"PUN": ..., "NORD": ..., ...}
"""

import logging
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from pun_clearing.conf import options
from pun_clearing.model.data_classes import HourlyData, Order, PunOrder, Zone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GMEDay:
    date: str
    hours: dict[int, HourlyData]
    real_prices: dict[int, dict[str, float]]   # hour → {"PUN" or zone name: price}
    zone_ids: dict[str, int]                   # zone name → zone_id


def load_day(path: str | Path, date: str) -> GMEDay:
    """Parse the GME files of one day into one HourlyData per hour."""
    path = Path(path)
    zone_ids: dict[str, int] = {}

    def zone_id(name: str) -> int:
        return zone_ids.setdefault(name, len(zone_ids) + 1)

    limits_xml = _open_xml(path, date, "MGPLimitiTransito")
    orders_xml = _open_xml(path, date, "MGPOffertePubbliche")
    prices_xml = _open_xml(path, date, "MGPPrezzi", required=False)
    assert limits_xml is not None and orders_xml is not None

    flow_limits = _read_flow_limits(limits_xml, zone_id)
    supply, demand, pun = _read_orders(orders_xml, zone_id)
    real_prices = _read_prices(prices_xml, zone_ids) if prices_xml is not None else {}

    zones = {i: Zone(zone_id=i, name=name, is_pun=name in options.PUN_ZONES)
             for name, i in zone_ids.items()}
    periods = sorted(set(flow_limits) | set(supply) | set(demand) | set(pun))
    hours = {t: HourlyData(period=t, zones=zones, flow_limits=flow_limits.get(t, {}),
                           supply_bids=supply.get(t, []), demand_bids=demand.get(t, []),
                           pun_bids=pun.get(t, []))
             for t in periods}

    logger.info(f"Loaded {date}: {len(hours)} hours, {len(zones)} zones, "
                f"{sum(len(h.pun_bids) for h in hours.values())} PUN bids, "
                f"{sum(len(h.demand_bids) for h in hours.values())} non-PUN bids, "
                f"{sum(len(h.supply_bids) for h in hours.values())} offers")
    return GMEDay(date=date, hours=hours, real_prices=real_prices, zone_ids=dict(zone_ids))


def _read_flow_limits(tree, zone_id) -> dict[int, dict[tuple[int, int], float]]:
    """hour → {(from_zone_id, to_zone_id): limit in MWh}."""
    limits: defaultdict[int, dict[tuple[int, int], float]] = defaultdict(dict)
    for el in tree.getroot().findall("LimitiTransito"):
        hour = int(_text(el, "Ora"))
        link = (zone_id(_text(el, "Da")), zone_id(_text(el, "A")))
        limits[hour][link] = float(_text(el, "Limite").replace(",", "."))
    return limits


def _read_orders(tree, zone_id):
    """Valid orders per hour, split into offers, non-PUN bids and PUN bids."""
    supply: defaultdict[int, list[Order]] = defaultdict(list)
    demand: defaultdict[int, list[Order]] = defaultdict(list)
    pun: defaultdict[int, list[PunOrder]] = defaultdict(list)

    for el in tree.getroot().findall("OfferteOperatori"):
        status = _optional_text(el, "STATUS_CD")
        if status is None or status in ("INC", "REP", "REV"):
            continue

        hour = int(_text(el, "INTERVAL_NO"))
        zone_name = _text(el, "ZONE_CD")
        zone = zone_id(zone_name)
        volume = float(_text(el, "ADJ_QUANTITY_NO"))
        price = float(_text(el, "ENERGY_PRICE_NO"))
        purpose = _text(el, "PURPOSE_CD")

        if purpose == "OFF":
            supply[hour].append(Order(zone_id=zone, volume=volume, price=price))
        elif purpose == "BID":
            # GME writes price 0 for bids without a price limit: they buy at any price.
            if price == 0.0:
                price = options.MAX_PRICE
            is_pumping = (_optional_text(el, "UNIT_REFERENCE_NO") or "").startswith("UP_")
            if zone_name in options.PUN_ZONES and not is_pumping:
                merit = _optional_text(el, "MERIT_ORDER_NO")
                pun[hour].append(PunOrder(zone_id=zone, volume=volume, price=price,
                                          merit_order=int(merit) if merit else 0))
            else:
                demand[hour].append(Order(zone_id=zone, volume=volume, price=price))

    return supply, demand, pun


def _read_prices(tree, zone_ids: dict[str, int]) -> dict[int, dict[str, float]]:
    prices: dict[int, dict[str, float]] = {}
    for el in tree.getroot().findall("Prezzi"):
        hour = int(_text(el, "Ora"))
        prices[hour] = {}
        for name in ["PUN", *zone_ids]:
            text = _optional_text(el, name)
            if text:
                prices[hour][name] = float(text.replace(",", "."))
    return prices


def _open_xml(path: Path, date: str, pattern: str, required: bool = True):
    """Parse YYYYMMDD<pattern>.xml from `path`, directly or from inside a .zip there.

    Nothing is extracted to disk. Raises FileNotFoundError if a required file is missing.
    """
    name = f"{date}{pattern}.xml"
    if (path / name).exists():
        return etree.parse(str(path / name))
    for zip_path in sorted(path.glob("*.zip")):
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.namelist():
                    if member.endswith(name):
                        with zf.open(member) as f:
                            return etree.parse(f)
        except zipfile.BadZipFile:
            continue
    if required:
        raise FileNotFoundError(f"{name} not found in {path} (neither as .xml nor in a .zip)")
    logger.warning(f"{name} not found in {path}; published prices will be missing")
    return None


def _text(el: etree._Element, tag: str) -> str:
    """Return the text of a required child tag, or fail with a clear message."""
    child = el.find(tag)
    if child is None or child.text is None:
        raise ValueError(f"Missing <{tag}> in <{el.tag}> element")
    return child.text


def _optional_text(el: etree._Element, tag: str) -> str | None:
    """Return the text of an optional child tag, or None if absent/empty."""
    child = el.find(tag)
    return child.text if child is not None else None
