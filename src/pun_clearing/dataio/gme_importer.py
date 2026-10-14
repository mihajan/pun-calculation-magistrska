"""
GME data importer for the Italian day-ahead market (MGP).

Reads XML files downloaded from the GME website:
  https://www.mercatoelettrico.org/en-us/Home/Results/Electricity/MGP/Download/Download

Required files for a given date YYYYMMDD (place all in one folder):
  1. 20100504MGPOffertePubbliche.xml (or inside a .zip)  — all bid/offer orders
  2. 20100504MGPLimitiTransito.xml (or inside a .zip)    — transmission limits
  3. 20100504MGPPrezzi.xml (or inside a .zip)             — published prices (validation)

Optional:
  4. 20100504MGPTransiti.xml  — actual flows (for validation, not used by model)

The importer converts these into HourlyData objects (one per hour),
which can be fed directly to pun_model.build_and_solve().

Usage:
    from pun_clearing.dataio.gme_importer import GMEImporter
    importer = GMEImporter(path='./gme_data', date='20100504')
    hourly_problems = importer.get_hourly_data()  # dict {hour: HourlyData}
    real_prices = importer.get_real_prices()       # for validation
"""

import os
import glob
import zipfile
import logging
from collections import defaultdict

from lxml import etree

from pun_clearing.model.data_classes import (
    SupplyBid, DemandBid, PunBid, Zone, Line, HourlyData
)
from pun_clearing.conf import options

logger = logging.getLogger(__name__)

PUN_ZONE_NAMES = set(options.PUN_ZONES)


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


def _find_xml(path, date, pattern):
    """Find an XML file matching a pattern, extracting from zip if needed."""
    # Try direct XML first
    xml_name = f"{date}{pattern}.xml"
    xml_path = os.path.join(path, xml_name)
    if os.path.exists(xml_path):
        return xml_path

    # Search in zip files
    for zf_path in glob.glob(os.path.join(path, "*.zip")):
        try:
            with zipfile.ZipFile(zf_path, 'r') as zf:
                for name in zf.namelist():
                    if pattern in name and name.endswith('.xml'):
                        zf.extract(name, path)
                        return os.path.join(path, name)
        except zipfile.BadZipFile:
            continue

    return None


class GMEImporter:
    def __init__(self, path, date):
        self.path = path
        self.date = date
        self._zones: dict[str, int] = {}
        self._zone_id_counter = 0
        self._lines: list[Line] = []
        self._supply: defaultdict[int, list[SupplyBid]] = defaultdict(list)
        self._demand: defaultdict[int, list[DemandBid]] = defaultdict(list)
        self._pun: defaultdict[int, list[PunBid]] = defaultdict(list)
        self._real_prices: dict[int, dict[str, float]] = {}
        self._all_periods: set[int] = set()
        self._bid_id_counter = 0

        self._read_network()
        self._read_bids()
        self._read_prices()

        logger.info(f"Loaded data for {date}: {len(self._all_periods)} hours, "
                    f"{len(self._zones)} zones, {len(self._lines)} lines")

    def _next_bid_id(self):
        self._bid_id_counter += 1
        return self._bid_id_counter

    def _get_zone_id(self, zone_name):
        if zone_name not in self._zones:
            self._zone_id_counter += 1
            self._zones[zone_name] = self._zone_id_counter
        return self._zones[zone_name]

    def _read_network(self):
        xml_path = _find_xml(self.path, self.date, 'MGPLimitiTransito')
        if xml_path is None:
            raise FileNotFoundError(
                f"Cannot find MGPLimitiTransito XML for {self.date} in {self.path}")

        tree = etree.parse(xml_path)
        raw_limits: defaultdict[tuple[str, str], dict[int, float]] = defaultdict(dict)

        for el in tree.getroot().findall('LimitiTransito'):
            period = int(_text(el, 'Ora'))
            self._all_periods.add(period)
            from_zone = _text(el, 'Da')
            to_zone = _text(el, 'A')
            limit = float(_text(el, 'Limite').replace(',', '.'))
            self._get_zone_id(from_zone)
            self._get_zone_id(to_zone)
            raw_limits[(from_zone, to_zone)][period] = limit

        seen_pairs = set()
        line_id = 0
        for (from_z, to_z) in raw_limits:
            pair = tuple(sorted([from_z, to_z]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            fwd_caps = raw_limits.get((pair[0], pair[1]), {})
            rev_caps = raw_limits.get((pair[1], pair[0]), {})
            line_id += 1
            self._lines.append(Line(
                line_id=line_id,
                from_zone_id=self._get_zone_id(pair[0]),
                to_zone_id=self._get_zone_id(pair[1]),
                capacity=fwd_caps,
                capacity_rev=rev_caps,
            ))

        logger.info(f"Read {len(self._lines)} transmission lines")

    def _read_bids(self):
        xml_path = _find_xml(self.path, self.date, 'MGPOffertePubbliche')
        if xml_path is None:
            raise FileNotFoundError(
                f"Cannot find MGPOffertePubbliche XML for {self.date} in {self.path}")

        tree = etree.parse(xml_path)
        n_skip = n_pun = n_demand = n_supply = 0

        for el in tree.getroot().findall('OfferteOperatori'):
            status = _optional_text(el, 'STATUS_CD')
            if status is None:
                continue
            if status in ('INC', 'REP', 'REV'):
                n_skip += 1
                continue

            purpose = _text(el, 'PURPOSE_CD')
            period = int(_text(el, 'INTERVAL_NO'))
            price = float(_text(el, 'ENERGY_PRICE_NO'))
            volume = float(_text(el, 'ADJ_QUANTITY_NO'))
            zone_name = _text(el, 'ZONE_CD')
            merit_order_text = _optional_text(el, 'MERIT_ORDER_NO')
            merit_order = int(merit_order_text) if merit_order_text is not None else 0
            unit_name = _optional_text(el, 'UNIT_REFERENCE_NO') or ''

            self._all_periods.add(period)
            zone_id = self._get_zone_id(zone_name)

            if purpose == 'BID':
                # Price=0 means "no price limit" in GME convention.
                # These bids are willing to pay ANY price, so we set them
                # to MAX_PRICE (3000 €/MWh) regardless of acceptance status.
                # The model decides whether they get accepted, not us.
                if price == 0.0:
                    price = options.MAX_PRICE
                is_pun_zone = zone_name in PUN_ZONE_NAMES
                is_pumping = unit_name.startswith('UP_') if unit_name else False

                if is_pun_zone and not is_pumping:
                    self._pun[period].append(PunBid(
                        bid_id=self._next_bid_id(),
                        zone_id=zone_id, period=period,
                        volume=volume, price=price,
                        merit_order=merit_order,
                    ))
                    n_pun += 1
                else:
                    self._demand[period].append(DemandBid(
                        bid_id=self._next_bid_id(),
                        zone_id=zone_id, period=period,
                        volume=volume, price=price,
                    ))
                    n_demand += 1

            elif purpose == 'OFF':
                self._supply[period].append(SupplyBid(
                    bid_id=self._next_bid_id(),
                    zone_id=zone_id, period=period,
                    volume=volume, price=price,
                ))
                n_supply += 1

        logger.info(f"Read {n_pun + n_demand + n_supply} active orders "
                    f"({n_pun} PUN, {n_demand} non-PUN demand, {n_supply} supply), "
                    f"skipped {n_skip}")

    def _read_prices(self):
        xml_path = _find_xml(self.path, self.date, 'MGPPrezzi')
        if xml_path is None:
            logger.warning("Cannot find MGPPrezzi XML — skipping price validation")
            return

        tree = etree.parse(xml_path)
        for el in tree.getroot().findall('Prezzi'):
            period = int(_text(el, 'Ora'))
            self._real_prices[period] = {}
            for name in ['PUN', *self._zones]:
                price_text = _optional_text(el, name)
                if price_text:
                    self._real_prices[period][name] = float(price_text.replace(',', '.'))

        logger.info(f"Read published prices for {len(self._real_prices)} hours")

    def get_hourly_data(self):
        zone_objects = {}
        for name, z_id in self._zones.items():
            zone_objects[z_id] = Zone(
                zone_id=z_id, name=name, is_pun=(name in PUN_ZONE_NAMES))
        result = {}
        for period in sorted(self._all_periods):
            result[period] = HourlyData(
                period=period, zones=zone_objects, lines=self._lines,
                supply_bids=self._supply.get(period, []),
                demand_bids=self._demand.get(period, []),
                pun_bids=self._pun.get(period, []),
            )
        return result

    def get_real_prices(self):
        return self._real_prices

    def get_zone_name_to_id(self):
        return dict(self._zones)
