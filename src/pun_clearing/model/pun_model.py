"""
Savelli et al. (2017) MILP for one hour of the Italian day-ahead market.

The model is the single-level MILP of Appendix A of the paper, obtained in
three steps (Section II):

  1. bilevel model — upper level enforces the UPP (PUN) rule with binaries,
     lower level is the LP that clears the market;
  2. single-level model — the lower level is replaced by primal feasibility,
     dual feasibility and strong duality;
  3. MILP — bilinear products binary × continuous are linearised with big-M
     (M = MAX_PRICE = 3000) and the integer quantity in the PUN definition is
     written with a binary expansion of B bits and c decimals.

Simplifications with respect to openDAM (Savelli et al. 2018), from which this
code was translated: no block orders, no u^w/u^d split (a single u^e for
at-the-money orders), no market-split detection, and each hour is independent.

Notation (code name → paper symbol):
  dk[k]      d_k      executed non-UPP demand
  dwk[k]     —        at-the-money PUN demand cleared by the lower level
  ddk[k]     —        at-the-money PUN demand fixed by the binary expansion
  dkpi[k]    d_k      total executed PUN demand = u^g_k·d^max_k + dwk + ddk
  sp[p]      s_p      executed supply
  F[i,j]     F_ij     flow from zone i to zone j
  zeta[i]    ζ_i      zonal price
  pi         π        uniform purchase price (PUN)
  ug[k]      u^g_k    1 if PUN bid k is in the money (p^d_k > π)
  ue[k]      u^e_k    1 if PUN bid k is at the money (p^d_k = π)
  phi_*      φ        duals of the quantity upper bounds
  delta, eta δ, η     duals of the flow constraints
  bexp[j,i]  b_ji     bit j of the binary expansion in zone i
  y_*        y        auxiliary variables replacing bilinear products

Constraint comments cite [Paper eq X] (Savelli 2017, Appendix A) and
[openDAM LY] (line in openDAM's pun_dam_model.py).
"""

import logging
from collections import defaultdict

from pyomo.environ import (
    Binary, ConcreteModel, Constraint, NonNegativeReals, Objective, Param, RangeSet,
    Reals, Set, SolverFactory, TerminationCondition, Var, maximize, value,
)

from pun_clearing.conf import options
from pun_clearing.model.data_classes import HourlyData

logger = logging.getLogger(__name__)


def build_and_solve(data: HourlyData, solver_name: str | None = None,
                    verbose: bool | None = None) -> HourlyData:
    """Build and solve the MILP for one hour; results are written into `data`."""
    model = build_model(data)
    results = solve_model(model, solver_name or options.SOLVER_NAME,
                          options.VERBOSE if verbose is None else verbose)
    extract_solution(model, data)
    data.solver_status = str(results.solver.termination_condition)
    return data


# ========================================================================
#  MODEL
# ========================================================================

def build_model(data: HourlyData) -> ConcreteModel:
    """Construct the Pyomo MILP for one hour (Savelli 2017, Appendix A)."""
    m = ConcreteModel(name=f"PUN_h{data.period}")
    _add_sets_and_params(m, data)
    _add_variables(m)
    _fix_max_price_bids(m, data)
    _add_objective(m, data)
    _add_upper_level(m, data)
    _add_lower_level_primal(m, data)
    _add_lower_level_dual(m, data)
    _add_strong_duality(m, data)
    _add_binary_expansion(m, data)
    _add_pun_definition(m, data)
    _add_linearizations(m, data)

    logger.info(f"Hour {data.period}: model built — "
                f"{len(data.supply_bids)} supply, {len(data.demand_bids)} demand, "
                f"{len(data.pun_bids)} PUN bids, {len(data.zones)} zones")
    return m


def _add_sets_and_params(m: ConcreteModel, data: HourlyData) -> None:
    pun = data.pun_bids

    m.S = Set(initialize=range(len(data.supply_bids)))   # producers p
    m.D = Set(initialize=range(len(data.demand_bids)))   # non-UPP consumers
    m.K = Set(initialize=range(len(pun)))                # UPP consumers k
    m.Z = Set(initialize=list(data.zones))               # all zones i
    m.Zpi = Set(initialize=[z for z, zone in data.zones.items() if zone.is_pun])
    m.B = RangeSet(0, options.BINARY_EXP_BITS - 1)       # bits j

    # PUN bids at the maximum price are always in the money (Paper Sec. III):
    # their binaries are fixed and most constraints are only written for the rest.
    m.K_free = Set(initialize=[k for k in m.K if pun[k].price < options.MAX_PRICE])

    t = data.period
    flow_cap = {(i, j): 0.0 for i in m.Z for j in m.Z}
    for line in data.lines:
        if t in line.capacity:
            flow_cap[line.from_zone_id, line.to_zone_id] = line.capacity[t]
        if t in line.capacity_rev:
            flow_cap[line.to_zone_id, line.from_zone_id] = line.capacity_rev[t]
    m.Fmax = Param(m.Z, m.Z, initialize=flow_cap, default=0.0)


def _add_variables(m: ConcreteModel) -> None:
    # Lower-level primal
    m.dk = Var(m.D, domain=NonNegativeReals)
    m.dwk = Var(m.K, domain=NonNegativeReals)      # [openDAM L130]
    m.ddk = Var(m.K, domain=NonNegativeReals)      # [openDAM L136]
    m.sp = Var(m.S, domain=NonNegativeReals)
    m.F = Var(m.Z, m.Z, domain=Reals)

    # Lower-level dual
    m.phi_w = Var(m.K, domain=NonNegativeReals)    # dual of dwk ≤ ue·dmax
    m.phi_d = Var(m.D, domain=NonNegativeReals)    # dual of dk ≤ dmax
    m.phi_s = Var(m.S, domain=NonNegativeReals)    # dual of sp ≤ smax
    m.delta = Var(m.Z, m.Z, domain=NonNegativeReals)  # dual of F ≤ Fmax
    m.eta = Var(m.Z, m.Z, domain=Reals)            # dual of F_ij + F_ji = 0
    m.zeta = Var(m.Z, domain=Reals)                # zonal prices (dual of balance)

    # Upper level
    m.pi = Var(domain=Reals)
    m.ug = Var(m.K, domain=Binary)
    m.ue = Var(m.K, domain=Binary)
    m.dkpi = Var(m.K, domain=NonNegativeReals)     # [openDAM L135]

    # Linearisation of bilinear products
    m.y_gpi = Var(m.K, domain=Reals)               # ug·π
    m.y_gzeta = Var(m.K, domain=Reals)             # ug·ζ
    m.y_uwphi = Var(m.K, domain=NonNegativeReals)  # ue·φ_w
    m.bexp = Var(m.B, m.Zpi, domain=Binary)        # binary expansion bits
    m.y_bzeta = Var(m.B, m.Zpi, domain=Reals)      # bexp·ζ


def _fix_max_price_bids(m: ConcreteModel, data: HourlyData) -> None:
    """[Paper Sec. III] Bids at the maximum price are always fully executed."""
    for k, bid in enumerate(data.pun_bids):
        if bid.price >= options.MAX_PRICE:
            m.ug[k].fix(1)
            m.ue[k].fix(0)
            m.dwk[k].fix(0)
            m.ddk[k].fix(0)
            m.dkpi[k].fix(bid.volume)


def _add_objective(m: ConcreteModel, data: HourlyData) -> None:
    """Social welfare [openDAM L977-985]: Σ p^d·d (non-UPP) + Σ p^d·d (PUN) − Σ p^s·s."""
    demand, pun, supply = data.demand_bids, data.pun_bids, data.supply_bids
    m.obj = Objective(
        expr=sum(demand[k].price * m.dk[k] for k in m.D)
        + sum(pun[k].price * m.dkpi[k] for k in m.K)
        - sum(supply[p].price * m.sp[p] for p in m.S),
        sense=maximize)


def _add_upper_level(m: ConcreteModel, data: HourlyData) -> None:
    pun = data.pun_bids
    M = options.MAX_PRICE
    eps = options.EPSILON

    # [Paper eq 7-8 / openDAM L207-223] in the money: ug = 1  ⇔  p^d − π > 0
    m.c_itm_le = Constraint(m.K_free, rule=lambda m, k: pun[k].price - m.pi <= M * m.ug[k])
    m.c_itm_ge = Constraint(
        m.K_free, rule=lambda m, k: pun[k].price - m.pi >= eps - M * (1 - m.ug[k]))

    # [Paper eq 8-9 / openDAM L225-241] at the money: ue = 1  ⇒  p^d = π
    m.c_atm_le = Constraint(
        m.K_free, rule=lambda m, k: pun[k].price - m.pi <= M * (1 - m.ue[k]))
    m.c_atm_ge = Constraint(
        m.K_free, rule=lambda m, k: pun[k].price - m.pi >= -M * (1 - m.ue[k]))

    # [openDAM L267-274] ddk ≤ dmax·ue
    m.c_ddk_ub = Constraint(m.K_free, rule=lambda m, k: m.ddk[k] <= pun[k].volume * m.ue[k])

    # [Paper eq 11 / openDAM L641-648] dkpi = ug·dmax + dwk + ddk
    m.c_dkpi = Constraint(
        m.K_free,
        rule=lambda m, k: m.dkpi[k] == m.ug[k] * pun[k].volume + m.dwk[k] + m.ddk[k])

    # [Paper eq 38 / openDAM L278-294] merit order: ug_h ≥ ug_k for consecutive m_h < m_k
    by_merit = sorted(m.K, key=lambda k: pun[k].merit_order)
    merit_pairs = [(h, k) for h, k in zip(by_merit, by_merit[1:])
                   if pun[h].price < options.MAX_PRICE]
    m.mo_set = Set(initialize=range(len(merit_pairs)))
    m.c_merit = Constraint(
        m.mo_set, rule=lambda m, n: m.ug[merit_pairs[n][0]] >= m.ug[merit_pairs[n][1]])

    # [Paper eq 39 / openDAM L302-329] price order: ue_k ≤ ug_h − ug_k, where k is
    # the first bid (in descending price order) with a strictly lower price than h
    price_pairs = _next_lower_price_pairs(data)
    m.po_set = Set(initialize=range(len(price_pairs)))
    m.c_price_order = Constraint(
        m.po_set,
        rule=lambda m, n: m.ue[price_pairs[n][1]]
        <= m.ug[price_pairs[n][0]] - m.ug[price_pairs[n][1]])


def _next_lower_price_pairs(data: HourlyData) -> list[tuple[int, int]]:
    """Pairs (h, k) where k is the next PUN bid below h in descending price order."""
    pun = data.pun_bids
    by_price = sorted(range(len(pun)), key=lambda k: -pun[k].price)
    pairs = []
    for pos, h in enumerate(by_price):
        if pun[h].price >= options.MAX_PRICE:
            continue
        k = next((k for k in by_price[pos + 1:] if pun[k].price < pun[h].price), None)
        if k is not None:
            pairs.append((h, k))
    return pairs


def _add_lower_level_primal(m: ConcreteModel, data: HourlyData) -> None:
    demand, pun, supply = data.demand_bids, data.pun_bids, data.supply_bids

    # [Paper eq 19 / openDAM L692-697] dwk ≤ ue·dmax
    m.c_dwk_ub = Constraint(m.K_free, rule=lambda m, k: m.dwk[k] <= m.ue[k] * pun[k].volume)
    # [Paper eq 45 / openDAM L699-702] dk ≤ dmax
    m.c_dk_ub = Constraint(m.D, rule=lambda m, k: m.dk[k] <= demand[k].volume)
    # [Paper eq 46 / openDAM L704-707] sp ≤ smax
    m.c_sp_ub = Constraint(m.S, rule=lambda m, p: m.sp[p] <= supply[p].volume)
    # [Paper eq 47 / openDAM L709-712] F ≤ Fmax
    m.c_fmax = Constraint(m.Z, m.Z, rule=lambda m, i, j: m.F[i, j] <= m.Fmax[i, j])
    # [Paper eq 48 / openDAM L714-717] F_ij + F_ji = 0
    m.c_fantisym = Constraint(m.Z, m.Z, rule=lambda m, i, j: m.F[i, j] + m.F[j, i] == 0)

    # [Paper eq 49-50 / openDAM L730-747] power balance in every zone:
    # demand (non-UPP + PUN) + net outflow = supply
    demand_in = _indices_by_zone(demand)
    pun_in = _indices_by_zone(pun)
    supply_in = _indices_by_zone(supply)

    def balance_rule(m, i):
        consumed = sum(m.dk[k] for k in demand_in[i])
        consumed += sum(m.dwk[k] for k in pun_in[i])
        consumed += sum(m.ug[k] * pun[k].volume + m.ddk[k] for k in pun_in[i])
        produced = sum(m.sp[p] for p in supply_in[i])
        outflow = sum(m.F[i, j] for j in m.Z)
        return consumed + outflow == produced
    m.c_balance = Constraint(m.Z, rule=balance_rule)


def _add_lower_level_dual(m: ConcreteModel, data: HourlyData) -> None:
    demand, pun, supply = data.demand_bids, data.pun_bids, data.supply_bids

    # [Paper eq 42 / openDAM L753-763] demand: φ + ζ ≥ p^d
    m.c_dual_pun = Constraint(
        m.K, rule=lambda m, k: m.phi_w[k] + m.zeta[pun[k].zone_id] >= pun[k].price)
    m.c_dual_dem = Constraint(
        m.D, rule=lambda m, k: m.phi_d[k] + m.zeta[demand[k].zone_id] >= demand[k].price)
    # [Paper eq 43 / openDAM L765-769] supply: φ − ζ ≥ −p^s
    m.c_dual_sup = Constraint(
        m.S, rule=lambda m, p: m.phi_s[p] - m.zeta[supply[p].zone_id] >= -supply[p].price)
    # [Paper eq 44 / openDAM L771-775] flows: δ_ij + η_ij + η_ji + ζ_i = 0
    m.c_dual_flow = Constraint(
        m.Z, m.Z,
        rule=lambda m, i, j: m.delta[i, j] + m.eta[i, j] + m.eta[j, i] + m.zeta[i] == 0)


def _binary_expansion_value(m: ConcreteModel):
    """10^-c · Σ_i Σ_j 2^j · y_bzeta[j,i] — the PUN-weighted ATM quantity term."""
    return sum((2**j) * m.y_bzeta[j, i] / (10**options.BINARY_EXP_DECIMALS)
               for i in m.Zpi for j in m.B)


def _add_strong_duality(m: ConcreteModel, data: HourlyData) -> None:
    """[Paper eq 41 / openDAM L938-972] lower-level primal objective = dual objective."""
    demand, pun, supply = data.demand_bids, data.pun_bids, data.supply_bids

    primal = (sum(demand[k].price * m.dk[k] for k in m.D)
              + sum(pun[k].price * m.dwk[k] for k in m.K)
              - sum(supply[p].price * m.sp[p] for p in m.S))

    dual = (sum(pun[k].volume * (-m.y_gzeta[k] + m.y_uwphi[k]) for k in m.K)
            + sum(demand[k].volume * m.phi_d[k] for k in m.D)
            + sum(supply[p].volume * m.phi_s[p] for p in m.S)
            + sum(m.delta[i, j] * m.Fmax[i, j] for i in m.Z for j in m.Z)
            - _binary_expansion_value(m))

    m.c_strong_duality = Constraint(expr=primal == dual)


def _add_binary_expansion(m: ConcreteModel, data: HourlyData) -> None:
    """[Paper eq 53 / openDAM L650-660] 10^c · Σ_{k in zone i} ddk_k = Σ_j 2^j · b_ji."""
    pun_in = _indices_by_zone(data.pun_bids)
    m.c_bexp = Constraint(
        m.Zpi,
        rule=lambda m, i: (10**options.BINARY_EXP_DECIMALS) * sum(m.ddk[k] for k in pun_in[i])
        == sum((2**j) * m.bexp[j, i] for j in m.B))


def _add_pun_definition(m: ConcreteModel, data: HourlyData) -> None:
    """[Paper eq 35 / openDAM L662-686] linearised UPP definition (eq. 1):

    Σ d^max_k·y_gpi_k + Σ p^d_k·ddk_k
        = Σ d^max_k·y_gzeta_k − Σ d^max_k·y_uwphi_k + 10^-c·Σ 2^j·y_bzeta_ji
    """
    pun = data.pun_bids
    lhs = sum(pun[k].volume * m.y_gpi[k] + pun[k].price * m.ddk[k] for k in m.K)
    rhs = (sum(pun[k].volume * m.y_gzeta[k] - pun[k].volume * m.y_uwphi[k] for k in m.K)
           + _binary_expansion_value(m))
    m.c_pun_def = Constraint(expr=lhs == rhs)


def _add_linearizations(m: ConcreteModel, data: HourlyData) -> None:
    """Big-M linearisation of y = u·x for binary u and |x| ≤ M (M = MAX_PRICE)."""
    pun = data.pun_bids
    M = options.MAX_PRICE

    # y_gpi = ug·π  [Paper eq 58-59 / openDAM L789-822]
    m.c_ygpi_lb = Constraint(m.K_free, rule=lambda m, k: -M * m.ug[k] <= m.y_gpi[k])
    m.c_ygpi_ub = Constraint(m.K_free, rule=lambda m, k: m.y_gpi[k] <= M * m.ug[k])

    def ygpi_llb(m, k):
        if k not in m.K_free:
            return m.y_gpi[k] == m.pi            # ug fixed to 1 → y_gpi = π
        return -M * (1 - m.ug[k]) <= m.pi - m.y_gpi[k]
    m.c_ygpi_llb = Constraint(m.K, rule=ygpi_llb)
    m.c_ygpi_lub = Constraint(
        m.K_free, rule=lambda m, k: m.pi - m.y_gpi[k] <= M * (1 - m.ug[k]))

    # y_gzeta = ug·ζ  [Paper eq 60-61 / openDAM L824-857]
    m.c_ygz_lb = Constraint(m.K_free, rule=lambda m, k: -M * m.ug[k] <= m.y_gzeta[k])
    m.c_ygz_ub = Constraint(m.K_free, rule=lambda m, k: m.y_gzeta[k] <= M * m.ug[k])

    def ygz_llb(m, k):
        zeta = m.zeta[pun[k].zone_id]
        if k not in m.K_free:
            return m.y_gzeta[k] == zeta          # ug fixed to 1 → y_gzeta = ζ
        return -M * (1 - m.ug[k]) <= zeta - m.y_gzeta[k]
    m.c_ygz_llb = Constraint(m.K, rule=ygz_llb)
    m.c_ygz_lub = Constraint(
        m.K_free, rule=lambda m, k: m.zeta[pun[k].zone_id] - m.y_gzeta[k] <= M * (1 - m.ug[k]))

    # y_uwphi = ue·φ_w  [Paper eq 64-65 / openDAM L859-876]
    # (y^eφ = u^e·φ^d in Savelli 2017; u^w of openDAM is collapsed into u^e)
    m.c_yuwp_ub = Constraint(m.K_free, rule=lambda m, k: m.y_uwphi[k] <= M * m.ue[k])
    m.c_yuwp_llb = Constraint(m.K_free, rule=lambda m, k: 0 <= m.phi_w[k] - m.y_uwphi[k])
    m.c_yuwp_lub = Constraint(
        m.K_free, rule=lambda m, k: m.phi_w[k] - m.y_uwphi[k] <= M * (1 - m.ue[k]))

    # y_bzeta = bexp·ζ  [Paper eq 55-57 / openDAM L878-905]
    m.c_ybz_lb = Constraint(m.B, m.Zpi, rule=lambda m, j, i: -M * m.bexp[j, i] <= m.y_bzeta[j, i])
    m.c_ybz_ub = Constraint(m.B, m.Zpi, rule=lambda m, j, i: m.y_bzeta[j, i] <= M * m.bexp[j, i])
    m.c_ybz_llb = Constraint(
        m.B, m.Zpi,
        rule=lambda m, j, i: -M * (1 - m.bexp[j, i]) <= m.zeta[i] - m.y_bzeta[j, i])
    m.c_ybz_lub = Constraint(
        m.B, m.Zpi,
        rule=lambda m, j, i: m.zeta[i] - m.y_bzeta[j, i] <= M * (1 - m.bexp[j, i]))

    # Paper eq 37 (u^e_k·p^d_k = y^eπ_k) needs no separate variable: together with
    # y^eπ = u^e·π it says p^d_k = π when u^e_k = 1, which c_atm_le/c_atm_ge enforce.


def _indices_by_zone(bids) -> defaultdict[int, list[int]]:
    """Map zone_id → indices of the bids located in that zone."""
    by_zone: defaultdict[int, list[int]] = defaultdict(list)
    for idx, bid in enumerate(bids):
        by_zone[bid.zone_id].append(idx)
    return by_zone


# ========================================================================
#  SOLVER
# ========================================================================

def solve_model(model: ConcreteModel, solver_name: str, verbose: bool = False):
    solver = SolverFactory(solver_name)
    if solver is None:
        raise RuntimeError(f"Solver '{solver_name}' not available.")

    if solver_name == 'cplex':
        solver.options["timelimit"] = options.SOLVER_TIME_LIMIT
        solver.options["mip tolerances mipgap"] = options.MIP_GAP
        solver.options["mip tolerances integrality"] = 0
        solver.options["simplex tolerances optimality"] = 1e-9
        solver.options["simplex tolerances feasibility"] = 1e-9
    elif solver_name == 'gurobi':
        solver.options["TimeLimit"] = options.SOLVER_TIME_LIMIT
        solver.options["MIPGap"] = options.MIP_GAP
    elif solver_name in ('appsi_highs', 'highs'):
        solver.options["time_limit"] = float(options.SOLVER_TIME_LIMIT)
        solver.options["mip_rel_gap"] = float(options.MIP_GAP)
        solver.options["output_flag"] = verbose
        for key, val in options.HIGHS_OPTIONS.items():
            solver.options[key] = val

    results = solver.solve(model, tee=verbose)

    condition = results.solver.termination_condition
    if condition == TerminationCondition.infeasible:
        raise RuntimeError("Model is infeasible!")
    if condition != TerminationCondition.optimal:
        logger.warning(f"Solver: {condition}")
    return results


# ========================================================================
#  SOLUTION EXTRACTION
# ========================================================================

def extract_solution(model: ConcreteModel, data: HourlyData) -> None:
    data.pun_price = value(model.pi)
    data.zonal_prices = {z: value(model.zeta[z]) for z in model.Z}
    data.social_welfare = value(model.obj)

    def acceptance(executed, volume):
        return executed / volume if volume > 0 else 0

    for p, supply in enumerate(data.supply_bids):
        supply.acceptance = acceptance(value(model.sp[p]), supply.volume)
    for k, demand in enumerate(data.demand_bids):
        demand.acceptance = acceptance(value(model.dk[k]), demand.volume)
    for k, pun in enumerate(data.pun_bids):
        pun.acceptance = acceptance(value(model.dkpi[k]), pun.volume)

    logger.info(f"Hour {data.period}: PUN = {data.pun_price:.6f} €/MWh, "
                f"welfare = {data.social_welfare:.2f}")
