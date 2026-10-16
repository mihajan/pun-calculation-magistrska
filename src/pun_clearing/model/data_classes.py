"""
Inputs and results of one hourly clearing problem.

  - Order:       an offer (s^max_p, p^s_p) or a non-UPP bid (d^max_k, p^d_k)
  - PunOrder:    a UPP bid (d^max_k, p^d_k, m_k) — the consumer pays the PUN
  - Zone:        market zone i ∈ Z
  - HourlyData:  everything the MILP needs for one hour (input only)
  - Solution:    what the MILP returns for one hour
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    zone_id: int        # i ∈ Z
    volume: float       # maximum quantity (MWh), positive
    price: float        # limit price (€/MWh)


@dataclass(frozen=True)
class PunOrder(Order):
    merit_order: int    # m_k — economic merit order (lower = higher priority)


@dataclass(frozen=True)
class Zone:
    zone_id: int
    name: str           # e.g. "NORD", "SICI", "FRAN"
    is_pun: bool        # True if the zone enforces the UPP rule (i ∈ Z^π)


@dataclass(frozen=True)
class HourlyData:
    period: int
    zones: dict[int, Zone]
    flow_limits: dict[tuple[int, int], float]   # (from, to) → F^max_ij for this hour (MWh)
    supply_bids: list[Order]                    # offers from producers
    demand_bids: list[Order]                    # bids from non-UPP consumers (pay ζ_i)
    pun_bids: list[PunOrder]                    # bids from UPP consumers (pay π)


@dataclass(frozen=True)
class Acceptance:
    """Accepted share (0–1) of each order, index-aligned with the HourlyData lists."""
    supply: list[float]
    demand: list[float]
    pun: list[float]


@dataclass(frozen=True)
class Solution:
    pun_price: float                 # π
    zonal_prices: dict[int, float]   # {zone_id: ζ_i}
    social_welfare: float            # objective value
    status: str                      # solver termination condition, "optimal" if proven
    acceptance: Acceptance
    solve_time_s: float              # building + solving the MILP
