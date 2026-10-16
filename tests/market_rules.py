"""
Checks that a solved hour obeys the market rules, independently of GME's prices.

  - PUN bids:        price > π fully accepted, price < π rejected, partial only at π
  - non-PUN bids:    the same rule with the zonal price ζ of their zone
  - offers:          price < ζ fully accepted, price > ζ rejected, partial only at ζ
  - energy balance:  accepted supply = accepted demand (over all zones)
  - PUN definition:  π = Σ ζ_i·d_k / Σ d_k over accepted PUN demand (paper eq. 1)
"""

from collections.abc import Sequence

from pun_clearing.model.data_classes import HourlyData, Order, Solution

PRICE_TOL = 1e-4      # €/MWh
SHARE_TOL = 1e-6      # accepted share
VOLUME_TOL = 1e-3     # MWh


def _check_orders(orders: Sequence[Order], shares: list[float], clearing_price, is_offer: bool,
                  kind: str) -> list[str]:
    errors = []
    for i, (order, share) in enumerate(zip(orders, shares)):
        price = clearing_price(order)
        below, above = order.price < price - PRICE_TOL, order.price > price + PRICE_TOL
        in_the_money, out_of_money = (below, above) if is_offer else (above, below)
        if in_the_money and share < 1 - SHARE_TOL:
            errors.append(f"{kind} {i} at {order.price} should be fully accepted "
                          f"(clearing price {price:.6f}), accepted {share:.6f}")
        if out_of_money and share > SHARE_TOL:
            errors.append(f"{kind} {i} at {order.price} should be rejected "
                          f"(clearing price {price:.6f}), accepted {share:.6f}")
    return errors


def market_rule_violations(data: HourlyData, sol: Solution) -> list[str]:
    zeta = sol.zonal_prices
    errors = (
        _check_orders(data.pun_bids, sol.acceptance.pun, lambda o: sol.pun_price, False, "PUN bid")
        + _check_orders(data.demand_bids, sol.acceptance.demand, lambda o: zeta[o.zone_id],
                        False, "non-PUN bid")
        + _check_orders(data.supply_bids, sol.acceptance.supply, lambda o: zeta[o.zone_id],
                        True, "offer"))

    def accepted(orders, shares):
        return [o.volume * s for o, s in zip(orders, shares)]

    supply = sum(accepted(data.supply_bids, sol.acceptance.supply))
    demand = (sum(accepted(data.demand_bids, sol.acceptance.demand))
              + sum(accepted(data.pun_bids, sol.acceptance.pun)))
    if abs(supply - demand) > VOLUME_TOL:
        errors.append(f"energy balance: supply {supply:.3f} MWh != demand {demand:.3f} MWh")

    pun_demand = accepted(data.pun_bids, sol.acceptance.pun)
    weighted = sum(d * zeta[o.zone_id] for o, d in zip(data.pun_bids, pun_demand))
    pun_from_eq1 = weighted / sum(pun_demand)
    if abs(pun_from_eq1 - sol.pun_price) > PRICE_TOL:
        errors.append(f"eq. 1: weighted zonal price {pun_from_eq1:.6f} != π {sol.pun_price:.6f}")
    return errors
