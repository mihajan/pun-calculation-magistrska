"""
Savelli et al. (2017) MILP — rewritten from the working openDAM code.

This is a DIRECT TRANSLATION of openDAM/model/pun_dam_model.py with:
  - Block orders removed (no rp, ubp, vphibMax, vphibMin)
  - u^w / u^d split collapsed into single u^e (Savelli 2017 formulation)
  - Multi-period coupling removed (each hour is independent)
  - Market split detection (uf) removed for simplicity

Every constraint is annotated with:
  [Paper eq X]  = equation number in Savelli 2017 Appendix A
  [openDAM LY]  = line number in openDAM pun_dam_model.py
"""

import logging
from pyomo.environ import (
    ConcreteModel, Set, Var, Param, Objective, Constraint,
    Binary, NonNegativeReals, Reals, maximize, value, RangeSet,
    SolverFactory, TerminationCondition
)
from pun_clearing.conf import options

logger = logging.getLogger(__name__)

# končna funkcija, ki gradi in rešuje MILP model za eno uro


def build_and_solve(hourly_data, solver_name=None):
    """Build and solve the MILP for one hour. Returns hourly_data with results."""
    model = _build_model(hourly_data)
    _solve_model(model, solver_name or options.SOLVER_NAME)
    _extract_solution(model, hourly_data)
    return hourly_data


def _build_model(data):
    """Construct the Pyomo MILP.

    Follows the openDAM structure exactly, with positive volume convention
    for ALL bids (PUN demand, non-PUN demand, supply).

    Key variable mapping (our name → openDAM name → paper notation):
      dk[k]     → dk[b]      → d_k   (non-UPP demand executed, ≥ 0)
      dwk[k]    → dwk[b]     → d_k for ATM PUN  (welfare-cleared part, ≥ 0)
      ddk[k]    → ddk[b]     → d_k for ATM PUN  (dispatch-cleared part, ≥ 0)
      sp[p]     → sp[b]      → s_p   (supply executed, ≥ 0)
      F[i,j]    → f[l,f,p]   → F_ij  (flow, free)
      zeta[i]   → pZi[l,p]   → ζ_i   (zonal price, free)
      pi        → pi[p]      → π     (UPP price, free)
      ug[k]     → ugk[b]     → u^g_k (ITM binary)
      ue[k]     → uek[b]     → u^e_k (ATM binary)
    """
    t = data.period
    m = ConcreteModel(name=f"PUN_h{t}")

    supply = data.supply_bids
    demand = data.demand_bids
    pun = data.pun_bids
    zones = data.zones
    lines = data.lines

    MAX_PRICE = options.MAX_PRICE
    EPS_PUN = options.EPSILON

    # ===== SETS =====
    m.S = Set(initialize=range(len(supply)))
    m.D = Set(initialize=range(len(demand)))
    m.K = Set(initialize=range(len(pun)))
    m.Z = Set(initialize=list(zones.keys()))
    m.Zpi = Set(initialize=[z for z, obj in zones.items() if obj.is_pun])
    m.Zzeta = Set(initialize=[z for z, obj in zones.items() if not obj.is_pun])
    m.B = RangeSet(0, options.BINARY_EXP_BITS - 1)

    # ===== FLOW CAPACITIES =====
    flow_cap = {(i, j): 0.0 for i in m.Z for j in m.Z}
    for line in lines:
        if t in line.capacity:
            flow_cap[line.from_zone_id, line.to_zone_id] = line.capacity[t]
        if t in line.capacity_rev:
            flow_cap[line.to_zone_id, line.from_zone_id] = line.capacity_rev[t]
    m.Fmax = Param(m.Z, m.Z, initialize=flow_cap, default=0.0)

    # ===== VARIABLES =====

    # Lower-level primal
    m.dk = Var(m.D, domain=NonNegativeReals)         # non-UPP demand
    m.dwk = Var(m.K, domain=NonNegativeReals)         # ATM PUN welfare-cleared [openDAM L130]
    m.ddk = Var(m.K, domain=NonNegativeReals)         # ATM PUN dispatch-cleared [openDAM L136]
    m.sp = Var(m.S, domain=NonNegativeReals)         # supply
    m.F = Var(m.Z, m.Z, domain=Reals)               # flows

    # Lower-level dual
    m.phi_w = Var(m.K, domain=NonNegativeReals)       # dual of dwk ≤ ue*dmax [openDAM vphikPUNw]
    m.phi_d = Var(m.D, domain=NonNegativeReals)       # dual of dk ≤ dmax [openDAM vphiknonPUNw]
    m.phi_s = Var(m.S, domain=NonNegativeReals)       # dual of sp ≤ smax [openDAM vphip]
    m.delta = Var(m.Z, m.Z, domain=NonNegativeReals)  # dual of F ≤ Fmax [openDAM deltaMax]
    m.eta = Var(m.Z, m.Z, domain=Reals)             # dual of F+F=0 [openDAM etaij]
    m.zeta = Var(m.Z, domain=Reals)                  # zonal prices [openDAM pZi]

    # Upper-level
    m.pi = Var(domain=Reals)                          # UPP price
    m.ug = Var(m.K, domain=Binary)                    # ITM indicator
    m.ue = Var(m.K, domain=Binary)                    # ATM indicator
    m.dkpi = Var(m.K, domain=NonNegativeReals)          # total PUN quantity [openDAM L135]

    # Linearization
    m.y_gpi = Var(m.K, domain=Reals)                # ug*pi [openDAM yugPUNk]
    m.y_gzeta = Var(m.K, domain=Reals)                # ug*zeta [openDAM yugPzk]
    m.y_epi = Var(m.K, domain=Reals)                # ue*pi [openDAM not used directly, via eq37]
    m.y_uwphi = Var(m.K, domain=NonNegativeReals)     # ue*phi_w [openDAM yuwvphik]
    m.bexp = Var(m.B, m.Zpi, domain=Binary)        # binary expansion bits [openDAM bexp]
    m.y_bzeta = Var(m.B, m.Zpi, domain=Reals)         # bexp*zeta [openDAM ybPzi]

    # ===== FIX PRICE=3000 BIDS =====
    # [Paper Sec III]: orders at max price are always ITM
    for k, bid in enumerate(pun):
        if bid.price >= MAX_PRICE:
            m.ug[k].fix(1)
            m.ue[k].fix(0)
            m.dwk[k].fix(0)
            m.ddk[k].fix(0)
            m.dkpi[k].fix(bid.volume)

    # ===== OBJECTIVE — full social welfare [openDAM L977-985] =====
    # max Σ p^d_k·dk (non-UPP) + Σ p^d_k·dkpi (PUN) - Σ p^s_p·sp
    def obj_rule(m):
        # Non-UPP demand welfare (price × executed quantity)
        w = sum(demand[k].price * m.dk[k] for k in m.D)
        # PUN demand welfare (price × total executed = ug*dmax + dwk + ddk)
        w += sum(pun[k].price * m.dkpi[k] for k in m.K)
        # Supply cost (subtracted)
        w -= sum(supply[p].price * m.sp[p] for p in m.S)
        return w
    m.obj = Objective(rule=obj_rule, sense=maximize)

    # ===== UPPER LEVEL CONSTRAINTS =====

    # [Paper eq 7-8 / openDAM L207-223] ITM indicator: ug=1 iff p^d > π
    def itm_le_rule(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return pun[k].price - m.pi <= MAX_PRICE * m.ug[k]          # p-π ≤ M·ug

    def itm_ge_rule(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return pun[k].price - m.pi >= EPS_PUN - MAX_PRICE * (1 - m.ug[k])  # p-π ≥ ε-M(1-ug)
    m.c_itm_le = Constraint(m.K, rule=itm_le_rule)
    m.c_itm_ge = Constraint(m.K, rule=itm_ge_rule)

    # [Paper eq 8-9 / openDAM L225-241] ATM indicator: ue=1 only if p^d = π
    def atm_le_rule(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return pun[k].price - m.pi <= MAX_PRICE * (1 - m.ue[k])

    def atm_ge_rule(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return pun[k].price - m.pi >= -MAX_PRICE * (1 - m.ue[k])
    m.c_atm_le = Constraint(m.K, rule=atm_le_rule)
    m.c_atm_ge = Constraint(m.K, rule=atm_ge_rule)

    # [Paper eq 13] ue constraint: dwk and ddk only active when ue=1
    # In Savelli 2017: u^e plays role of both u^w and u^d
    # dwk is welfare-cleared (like social welfare intersection, Fig 5)
    # ddk is dispatch-cleared (like vertical demand, Fig 4)
    # At most one can be nonzero.
    # We keep both for compatibility with the binary expansion.

    # dwk ≤ dmax * ue  (can only have welfare-cleared qty when ATM)
    # (This is enforced by the lower-level constraint below)

    # ddk ≤ dmax * ue  [openDAM L267-274]
    def ddk_ub_rule(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.ddk[k] <= pun[k].volume * m.ue[k]
    m.c_ddk_ub = Constraint(m.K, rule=ddk_ub_rule)

    # [Paper eq 11 / openDAM L641-648] dkpi = ug*dmax + dwk + ddk
    def dkpi_rule(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.dkpi[k] == m.ug[k] * pun[k].volume + m.dwk[k] + m.ddk[k]
    m.c_dkpi = Constraint(m.K, rule=dkpi_rule)

    # [Paper eq 38 / openDAM L278-294] Merit order: ug[h] ≥ ug[k] for m_h < m_k
    pun_sorted = sorted(pun, key=lambda b: b.merit_order)
    mo_pairs = []
    for i in range(len(pun_sorted) - 1):
        if pun_sorted[i].price >= MAX_PRICE:
            continue
        h_idx = pun.index(pun_sorted[i])
        k_idx = pun.index(pun_sorted[i + 1])
        mo_pairs.append((h_idx, k_idx))
    m.mo_set = Set(initialize=range(len(mo_pairs)))

    def merit_rule(m, idx):
        h, k = mo_pairs[idx]
        return m.ug[h] >= m.ug[k]
    m.c_merit = Constraint(m.mo_set, rule=merit_rule)

    # [Paper eq 39/A.23 / openDAM L302-329] Price order: ue[k] ≤ ug[h]-ug[k]
    # for h,k where p^d_h > p^d_k
    pun_by_price = sorted(pun, key=lambda b: -b.price)
    po_pairs = []
    for i in range(len(pun_by_price)):
        h = pun_by_price[i]
        if h.price >= MAX_PRICE:
            continue
        for j in range(i + 1, len(pun_by_price)):
            k = pun_by_price[j]
            if h.price > k.price:
                h_idx = pun.index(h)
                k_idx = pun.index(k)
                po_pairs.append((h_idx, k_idx))
                break
    m.po_set = Set(initialize=range(len(po_pairs)))

    def price_order_rule(m, idx):
        h, k = po_pairs[idx]
        return m.ue[k] <= m.ug[h] - m.ug[k]
    m.c_price_order = Constraint(m.po_set, rule=price_order_rule)

    # ===== LOWER LEVEL PRIMAL CONSTRAINTS =====

    # [Paper eq 19 / openDAM L692-697] dwk ≤ ue * dmax
    def dwk_ub_rule(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.dwk[k] <= m.ue[k] * pun[k].volume
    m.c_dwk_ub = Constraint(m.K, rule=dwk_ub_rule)

    # [Paper eq 45 / openDAM L699-702] dk ≤ dmax (non-UPP)
    def dk_ub_rule(m, k):
        return m.dk[k] <= demand[k].volume
    m.c_dk_ub = Constraint(m.D, rule=dk_ub_rule)

    # [Paper eq 46 / openDAM L704-707] sp ≤ smax
    def sp_ub_rule(m, p):
        return m.sp[p] <= supply[p].volume
    m.c_sp_ub = Constraint(m.S, rule=sp_ub_rule)

    # [Paper eq 47 / openDAM L709-712] F ≤ Fmax
    def fmax_rule(m, i, j):
        return m.F[i, j] <= m.Fmax[i, j]
    m.c_fmax = Constraint(m.Z, m.Z, rule=fmax_rule)

    # [Paper eq 48 / openDAM L714-717] F_ij + F_ji = 0
    def fantisym_rule(m, i, j):
        return m.F[i, j] + m.F[j, i] == 0
    m.c_fantisym = Constraint(m.Z, m.Z, rule=fantisym_rule)

    # [Paper eq 49+50 / openDAM L730-747] Power balance (all zones)
    # demand + flow_out = supply  (with PUN ITM+dispatch on demand side)
    def balance_rule(m, i):
        # Non-UPP demand in zone i
        dem = sum(m.dk[k] for k in m.D if demand[k].zone_id == i)
        # PUN welfare-cleared demand in zone i
        dem += sum(m.dwk[k] for k in m.K if pun[k].zone_id == i)
        # PUN ITM demand + dispatch-cleared demand in zone i
        # (ug*dmax is the fully-executed ITM part, ddk is dispatch part)
        dem += sum(m.ug[k] * pun[k].volume + m.ddk[k]
                   for k in m.K if pun[k].zone_id == i)
        # Supply in zone i
        sup = sum(m.sp[p] for p in m.S if supply[p].zone_id == i)
        # Net outflow
        flow = sum(m.F[i, j] for j in m.Z)
        return dem + flow == sup
    m.c_balance = Constraint(m.Z, rule=balance_rule)

    # ===== LOWER LEVEL DUAL CONSTRAINTS =====

    # [Paper eq 42 / openDAM L753-757] dual for PUN demand: phi_w + zeta ≥ price
    def dual_pun_rule(m, k):
        return m.phi_w[k] + m.zeta[pun[k].zone_id] >= pun[k].price
    m.c_dual_pun = Constraint(m.K, rule=dual_pun_rule)

    # [Paper eq 42 / openDAM L759-763] dual for non-UPP demand: phi_d + zeta ≥ price
    def dual_dem_rule(m, k):
        return m.phi_d[k] + m.zeta[demand[k].zone_id] >= demand[k].price
    m.c_dual_dem = Constraint(m.D, rule=dual_dem_rule)

    # [Paper eq 43 / openDAM L765-769] dual for supply: phi_s - zeta ≥ -price
    def dual_sup_rule(m, p):
        return m.phi_s[p] - m.zeta[supply[p].zone_id] >= -supply[p].price
    m.c_dual_sup = Constraint(m.S, rule=dual_sup_rule)

    # [Paper eq 44 / openDAM L771-775] dual for flows: delta + eta_ij + eta_ji + zeta_i = 0
    def dual_flow_rule(m, i, j):
        return m.delta[i, j] + m.eta[i, j] + m.eta[j, i] + m.zeta[i] == 0
    m.c_dual_flow = Constraint(m.Z, m.Z, rule=dual_flow_rule)

    # ===== STRONG DUALITY [Paper eq 41 / openDAM L938-972] =====
    # primal_obj(lower level) = dual_obj
    def strong_duality_rule(m):
        # PRIMAL side: the lower-level objective
        # [openDAM L942-947]
        primal = sum(demand[k].price * m.dk[k] for k in m.D)
        primal += sum(pun[k].price * m.dwk[k] for k in m.K)
        primal -= sum(supply[p].price * m.sp[p] for p in m.S)

        # DUAL side
        # [openDAM L949-967]
        # PUN terms: volume * (-y_gzeta + y_uwphi)
        dual = sum(pun[k].volume * (-m.y_gzeta[k] + m.y_uwphi[k])
                   for k in m.K)
        # Non-UPP demand: -volume * phi_d  (openDAM uses negative volumes,
        # we use positive, and openDAM has -bid.volume*phi = +|vol|*phi.
        # So with positive volumes: +volume * phi_d)
        dual += sum(demand[k].volume * m.phi_d[k] for k in m.D)
        # Supply: +volume * phi_s
        dual += sum(supply[p].volume * m.phi_s[p] for p in m.S)
        # Congestion: delta * Fmax
        dual += sum(m.delta[i, j] * m.Fmax[i, j] for i in m.Z for j in m.Z)
        # Binary expansion: -10^{-c} * Σ 2^j * y_bzeta
        dual -= sum((1.0 / options.BINARY_EXP_DECIMALS) *  # WRONG — should be 10^{-c}
                    0 for _ in [0])  # placeholder
        # Let me redo this properly:
        bexp_term = 0
        for i in m.Zpi:
            for j in m.B:
                bexp_term += (2**j) * m.y_bzeta[j, i]
        dual -= bexp_term / (10 ** options.BINARY_EXP_DECIMALS)

        return primal == dual
    m.c_strong_duality = Constraint(rule=strong_duality_rule)

    # ===== BINARY EXPANSION [Paper eq 53 / openDAM L650-660] =====
    # 10^c * Σ_{k in zone i} ddk[k] = Σ_j 2^j * bexp[j,i]
    def bexp_rule(m, i):
        rhs = sum(m.ddk[k] for k in m.K if pun[k].zone_id == i)
        return (10**options.BINARY_EXP_DECIMALS) * rhs == sum(
            (2**j) * m.bexp[j, i] for j in m.B)
    m.c_bexp = Constraint(m.Zpi, rule=bexp_rule)

    # ===== PUN DEFINITION [Paper eq 35/46 / openDAM L662-686] =====
    # Σ_k volume_k * y_gpi[k]  +  Σ_k price_k * ddk[k]
    #   = Σ_k volume_k * y_gzeta[k]  -  Σ_k volume_k * y_uwphi[k]
    #     + 10^{-c} * Σ_{i,j} 2^j * y_bzeta[j,i]
    def pun_def_rule(m):
        lhs = 0
        rhs = 0
        for k in m.K:
            lhs += pun[k].volume * m.y_gpi[k]
            lhs += pun[k].price * m.ddk[k]
            rhs += pun[k].volume * m.y_gzeta[k]
            rhs -= pun[k].volume * m.y_uwphi[k]
        for i in m.Zpi:
            for j in m.B:
                rhs += (2**j) * m.y_bzeta[j, i] / (10**options.BINARY_EXP_DECIMALS)
        return lhs == rhs
    m.c_pun_def = Constraint(rule=pun_def_rule)

    # ===== LINEARIZATION =====

    # --- y_gpi = ug * pi [Paper eq 58-59 / openDAM L789-822] ---
    def ygpi_lb(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return -MAX_PRICE * m.ug[k] <= m.y_gpi[k]

    def ygpi_ub(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.y_gpi[k] <= MAX_PRICE * m.ug[k]

    def ygpi_llb(m, k):
        if pun[k].price >= MAX_PRICE:
            return m.y_gpi[k] == m.pi  # ug fixed to 1 → y_gpi = pi
        return -MAX_PRICE * (1 - m.ug[k]) <= m.pi - m.y_gpi[k]

    def ygpi_lub(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.pi - m.y_gpi[k] <= MAX_PRICE * (1 - m.ug[k])
    m.c_ygpi_lb = Constraint(m.K, rule=ygpi_lb)
    m.c_ygpi_ub = Constraint(m.K, rule=ygpi_ub)
    m.c_ygpi_llb = Constraint(m.K, rule=ygpi_llb)
    m.c_ygpi_lub = Constraint(m.K, rule=ygpi_lub)

    # --- y_gzeta = ug * zeta [Paper eq 60-61 / openDAM L824-857] ---
    def ygz_lb(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return -MAX_PRICE * m.ug[k] <= m.y_gzeta[k]

    def ygz_ub(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.y_gzeta[k] <= MAX_PRICE * m.ug[k]

    def ygz_llb(m, k):
        if pun[k].price >= MAX_PRICE:
            return m.y_gzeta[k] == m.zeta[pun[k].zone_id]
        return -MAX_PRICE * (1 - m.ug[k]) <= m.zeta[pun[k].zone_id] - m.y_gzeta[k]

    def ygz_lub(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.zeta[pun[k].zone_id] - m.y_gzeta[k] <= MAX_PRICE * (1 - m.ug[k])
    m.c_ygz_lb = Constraint(m.K, rule=ygz_lb)
    m.c_ygz_ub = Constraint(m.K, rule=ygz_ub)
    m.c_ygz_llb = Constraint(m.K, rule=ygz_llb)
    m.c_ygz_lub = Constraint(m.K, rule=ygz_lub)

    # --- y_uwphi = ue * phi_w [Paper eq 64-65 / openDAM L859-876] ---
    # Note: in Savelli 2017, this is y^eϕ = u^e * ϕ^d.
    # In openDAM (2018), it's y^wϕ = u^w * ϕ^w. Since we collapsed u^w into u^e,
    # we use ue here.
    def yuwp_ub(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.y_uwphi[k] <= MAX_PRICE * m.ue[k]

    def yuwp_llb(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return 0 <= m.phi_w[k] - m.y_uwphi[k]

    def yuwp_lub(m, k):
        if pun[k].price >= MAX_PRICE:
            return Constraint.Skip
        return m.phi_w[k] - m.y_uwphi[k] <= MAX_PRICE * (1 - m.ue[k])
    m.c_yuwp_ub = Constraint(m.K, rule=yuwp_ub)
    m.c_yuwp_llb = Constraint(m.K, rule=yuwp_llb)
    m.c_yuwp_lub = Constraint(m.K, rule=yuwp_lub)

    # --- y_bzeta = bexp * zeta [Paper eq 55-57 / openDAM L878-905] ---
    def ybz_lb(m, j, i):
        return -MAX_PRICE * m.bexp[j, i] <= m.y_bzeta[j, i]

    def ybz_ub(m, j, i):
        return m.y_bzeta[j, i] <= MAX_PRICE * m.bexp[j, i]

    def ybz_llb(m, j, i):
        return -MAX_PRICE * (1 - m.bexp[j, i]) <= m.zeta[i] - m.y_bzeta[j, i]

    def ybz_lub(m, j, i):
        return m.zeta[i] - m.y_bzeta[j, i] <= MAX_PRICE * (1 - m.bexp[j, i])
    m.c_ybz_lb = Constraint(m.B, m.Zpi, rule=ybz_lb)
    m.c_ybz_ub = Constraint(m.B, m.Zpi, rule=ybz_ub)
    m.c_ybz_llb = Constraint(m.B, m.Zpi, rule=ybz_llb)
    m.c_ybz_lub = Constraint(m.B, m.Zpi, rule=ybz_lub)

    # --- y_epi = ue * pi [for eq 37: ue*price - y_epi = 0] ---
    # [openDAM uses this via ATM constraint, not as separate aux var]
    # We need it for constraint (37): ue_k * p^d_k = y_epi_k
    # where y_epi_k should equal ue_k * pi when ue_k=1.
    # But eq(37) says ue_k * p^d_k - y_epi_k = 0, and y_epi = ue*pi,
    # so this enforces p^d_k = pi when ue_k=1. Already handled by atm_le/ge above.
    # No separate y_epi variable needed since openDAM handles it through
    # the ATM le/ge constraints directly.

    logger.info(f"Hour {t}: model built — "
                f"{len(supply)} supply, {len(demand)} demand, {len(pun)} PUN bids, "
                f"{len(zones)} zones")
    return m


# ========================================================================
#  SOLVER
# ========================================================================

def _solve_model(model, solver_name):
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
        solver.options["output_flag"] = options.VERBOSE

    results = solver.solve(model, tee=options.VERBOSE)

    if results.solver.termination_condition == TerminationCondition.infeasible:
        raise RuntimeError("Model is infeasible!")
    if results.solver.termination_condition != TerminationCondition.optimal:
        logger.warning(f"Solver: {results.solver.termination_condition}")

    return results


# ========================================================================
#  SOLUTION EXTRACTION
# ========================================================================

def _extract_solution(model, data):
    pun = data.pun_bids
    demand = data.demand_bids
    supply = data.supply_bids

    data.pun_price = value(model.pi)
    for z in model.Z:
        data.zonal_prices[z] = value(model.zeta[z])
    data.social_welfare = value(model.obj)

    for p_idx, bid in enumerate(supply):
        bid.acceptance = value(model.sp[p_idx]) / bid.volume if bid.volume > 0 else 0

    for k_idx, bid in enumerate(demand):
        bid.acceptance = value(model.dk[k_idx]) / bid.volume if bid.volume > 0 else 0

    for k_idx, bid in enumerate(pun):
        executed = value(model.dkpi[k_idx])
        bid.acceptance = executed / bid.volume if bid.volume > 0 else 0

    logger.info(f"Hour {data.period}: PUN = {data.pun_price:.6f} €/MWh, "
                f"welfare = {data.social_welfare:.2f}")
