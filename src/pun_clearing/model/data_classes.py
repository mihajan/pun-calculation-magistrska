"""
Data classes for the PUN clearing model — plain containers, no optimisation logic.

They map to the paper's notation:

  - SupplyBid:  offer order (s^max_p, p^s_p) from producer p
  - DemandBid:  bid order (d^max_k, p^d_k) from non-UPP consumer k ∈ K^ζ ∪ K^p
  - PunBid:     bid order (d^max_k, p^d_k, m_k) from UPP consumer k ∈ K^π
  - Zone:       market zone i ∈ Z
  - Line:       inter-zonal transmission link with capacity F^max_ij

Bids use eq=False: two bids with identical values are still different orders.
"""

from dataclasses import dataclass, field


@dataclass(eq=False)
class SupplyBid:
    """A supply (offer) order from a producer."""
    bid_id: int
    zone_id: int        # i ∈ Z
    period: int         # hour t ∈ {1, ..., 24}
    volume: float       # s^max_p — maximum quantity offered (MWh), positive
    price: float        # p^s_p — minimum acceptable price (€/MWh)
    acceptance: float = 0.0   # filled after solving: s_p / s^max_p


@dataclass(eq=False)
class DemandBid:
    """A demand order from a NON-UPP consumer (K^ζ, or pumping units K^p).

    These consumers pay the zonal price, not the PUN.
    """
    bid_id: int
    zone_id: int
    period: int
    volume: float       # d^max_k — maximum quantity demanded (MWh), positive
    price: float        # p^d_k — maximum willingness to pay (€/MWh)
    acceptance: float = 0.0


@dataclass(eq=False)
class PunBid:
    """A demand order from a UPP consumer (k ∈ K^π), who pays the PUN π."""
    bid_id: int
    zone_id: int        # i ∈ Z^π (a PUN zone)
    period: int
    volume: float       # d^max_k (MWh), positive
    price: float        # p^d_k (€/MWh)
    merit_order: int    # m_k — economic merit order (lower = higher priority)
    acceptance: float = 0.0


@dataclass
class Zone:
    """A market zone; is_pun marks zones enforcing the UPP rule (i ∈ Z^π)."""
    zone_id: int
    name: str           # e.g. "NORD", "SICI", "FRAN"
    is_pun: bool = False


@dataclass
class Line:
    """An inter-zonal transmission line with hourly capacities (MWh)."""
    line_id: int
    from_zone_id: int
    to_zone_id: int
    capacity: dict[int, float]       # {period: F^max_ij}, from → to
    capacity_rev: dict[int, float]   # {period: F^max_ji}, to → from


@dataclass
class HourlyData:
    """All input data for one hourly clearing problem, plus its results."""
    period: int
    zones: dict[int, Zone]
    lines: list[Line]
    supply_bids: list[SupplyBid]
    demand_bids: list[DemandBid]
    pun_bids: list[PunBid]

    # Filled after solving
    pun_price: float | None = None                              # π
    zonal_prices: dict[int, float] = field(default_factory=dict)  # {zone_id: ζ_i}
    social_welfare: float | None = None                         # objective value
    solver_status: str | None = None                            # "optimal" if proven
