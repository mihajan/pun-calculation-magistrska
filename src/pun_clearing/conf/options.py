"""
Parameters of the PUN clearing MILP (values from Section III of Savelli et al. 2017)
and of the solver.
"""

# ===========================================================================
#  MODEL
# ===========================================================================

MAX_PRICE = 3000.0
"""Price cap of the Italian market (€/MWh). Bids without a price limit get this
   price, and it is the big-M of all linearisations."""

EPSILON = 1e-6
"""ε in constraint (7)/(36): turns p^d_k > π into p^d_k − π ≥ ε."""

BINARY_EXP_BITS = 24
"""B: bits of the binary expansion. With c = 3 decimals, 2^24 − 1 = 16,777,215
   represents up to 16,777.215 MWh of at-the-money demand per zone."""

BINARY_EXP_DECIMALS = 3
"""c: decimals of the binary expansion (quantities in kWh)."""

PUN_ZONES = ("NORD", "CNOR", "CSUD", "SUD", "SICI", "SARD")
"""Zones enforcing the UPP rule: their consumers (except pumping units) pay π."""

# ===========================================================================
#  SOLVER
# ===========================================================================

SOLVER_NAME = "appsi_highs"
"""Default solver: HiGHS, installed with the package (highspy)."""

SOLVER_OPTIONS: dict[str, dict[str, float | int | str]] = {
    "appsi_highs": {
        "time_limit": 600.0,   # s per hour; the slowest hour needs ~340 s on one thread
        "mip_rel_gap": 0.0,    # exact optimum, as in the paper
    },
}
"""Options passed to each solver. They change how the solver searches, never the model."""
