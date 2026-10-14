"""
Data classes for the PUN clearing model.

These are simple containers — no optimization logic here.
They map directly to the paper's notation:

  - SupplyBid:  offer order (s^max_p, p^s_p) from producer p
  - DemandBid:  bid order (d^max_k, p^d_k) from non-UPP consumer k ∈ K^ζ ∪ K^p
  - PunBid:     bid order (d^max_k, p^d_k, m_k) from UPP consumer k ∈ K^π
  - Zone:       market zone i ∈ Z
  - Line:       inter-zonal transmission link with capacity F^max_ij
"""


class SupplyBid:
    """A supply (offer) order from a producer.

    Attributes:
        bid_id:   unique identifier
        zone_id:  zone where the producer is located (i ∈ Z)
        period:   hour t ∈ {1,...,24}
        volume:   s^max_p — maximum quantity offered (MWh), positive
        price:    p^s_p — minimum acceptable price (€/MWh)
    """
    def __init__(self, bid_id, zone_id, period, volume, price):
        self.bid_id = bid_id
        self.zone_id = zone_id
        self.period = period
        self.volume = volume    # s^max_p (positive)
        self.price = price      # p^s_p
        self.acceptance = 0.0   # filled after solving: s_p / s^max_p


class DemandBid:
    """A demand (bid) order from a NON-UPP consumer.

    These are consumers in non-UPP zones (K^ζ) or pumping units in UPP
    zones (K^p). They pay the zonal price, not the PUN.

    Attributes:
        bid_id:   unique identifier
        zone_id:  zone (i ∈ Z)
        period:   hour t
        volume:   d^max_k — maximum quantity demanded (MWh), positive
        price:    p^d_k — maximum willingness to pay (€/MWh)
    """
    def __init__(self, bid_id, zone_id, period, volume, price):
        self.bid_id = bid_id
        self.zone_id = zone_id
        self.period = period
        self.volume = volume    # d^max_k (positive, we store absolute value)
        self.price = price      # p^d_k
        self.acceptance = 0.0


class PunBid:
    """A demand (bid) order from a UPP consumer (k ∈ K^π).

    These consumers pay the PUN (π), not the zonal price.
    They are subject to the merit order ranking.

    Attributes:
        bid_id:      unique identifier (from GME data)
        zone_id:     zone i ∈ Z^π (must be a PUN zone)
        period:      hour t
        volume:      d^max_k — maximum quantity demanded (MWh), positive
        price:       p^d_k — maximum willingness to pay (€/MWh)
        merit_order: m_k — economic merit order (lower = higher priority)
    """
    def __init__(self, bid_id, zone_id, period, volume, price, merit_order):
        self.bid_id = bid_id
        self.zone_id = zone_id
        self.period = period
        self.volume = volume        # d^max_k (positive)
        self.price = price          # p^d_k
        self.merit_order = merit_order  # m_k
        self.acceptance = 0.0


class Zone:
    """A market zone.

    Attributes:
        zone_id:  numeric identifier
        name:     string name (e.g. "NORD", "SICI", "XFRA")
        is_pun:   True if this zone enforces the UPP rule (i ∈ Z^π)
    """
    def __init__(self, zone_id, name, is_pun=False):
        self.zone_id = zone_id
        self.name = name
        self.is_pun = is_pun


class Line:
    """An inter-zonal transmission line.

    Attributes:
        line_id:       unique identifier
        from_zone_id:  origin zone
        to_zone_id:    destination zone
        capacity:      dict {period: F^max_ij} — max flow from→to per hour (MWh)
        capacity_rev:  dict {period: F^max_ji} — max flow to→from per hour (MWh)
    """
    def __init__(self, line_id, from_zone_id, to_zone_id, capacity, capacity_rev):
        self.line_id = line_id
        self.from_zone_id = from_zone_id
        self.to_zone_id = to_zone_id
        self.capacity = capacity          # {period: F^max_ij}
        self.capacity_rev = capacity_rev  # {period: F^max_ji}


class HourlyData:
    """All input data for a single hourly clearing problem.

    This bundles everything the MILP model needs for one hour t:
    the supply bids, demand bids, PUN bids, zones, and lines.
    """
    def __init__(self, period, zones, lines, supply_bids, demand_bids, pun_bids):
        self.period = period
        self.zones = zones              # dict {zone_id: Zone}
        self.lines = lines              # list of Line objects
        self.supply_bids = supply_bids  # list of SupplyBid for this hour
        self.demand_bids = demand_bids  # list of DemandBid for this hour
        self.pun_bids = pun_bids        # list of PunBid for this hour

        # These are filled after solving:
        self.pun_price = None           # π — the PUN for this hour
        self.zonal_prices = {}          # {zone_id: ζ_i}
        self.social_welfare = None      # objective value
