"""
Configuration for the PUN clearing MILP.

Parameters here correspond to the values described in Section III (Results)
of Savelli et al. 2017:
  - ε = 10^-6  (constraint 7/36: strict inequality tolerance)
  - c = 3      (number of decimal digits in binary expansion)
  - B = 24     (number of bits in binary expansion)
  - M1 = -3000, M2 = 3000  (big-M bounds, from Italian market price cap)

The solver is CPLEX via Pyomo. If you don't have CPLEX, you can substitute
'glpk', 'gurobi', or 'highs' — but CPLEX is recommended for performance.
"""

# ===========================================================================
#  SOLVER SETUP
# ===========================================================================

SOLVER_NAME = 'appsi_highs'
"""Which solver to use. Options: 'appsi_highs', 'cplex', 'gurobi', 'glpk'.
   'appsi_highs' requires: pip install highspy
   This is the default — free, no license needed, good performance."""

SOLVER_TIME_LIMIT = 600
"""Maximum solve time per hourly problem, in seconds.
   The slowest hour of 2010-05-04 needs ~300 s on one thread (parallel runs)."""

MIP_GAP = 0.0
"""Relative MIP gap. Set to 0 for exact solutions (as in the paper)."""

HIGHS_OPTIONS: dict[str, float | int | str] = {}
"""Extra HiGHS options (search strategy only — they never change the model)."""

# ===========================================================================
#  ITALIAN MARKET PARAMETERS
# ===========================================================================

MAX_PRICE = 3000.0
"""Maximum allowed price on the Italian market (€/MWh).
   Orders without a specified price are set to this value.
   This also serves as M2 in the big-M linearization."""

MIN_PRICE = 0.0
"""Minimum allowed price (€/MWh). Serves as |M1| bound."""

EPSILON = 1e-6
"""ε in constraint (7)/(36): ensures strict inequality p^d_k > π.
   The paper uses 10^-6. Must be smaller than the price resolution."""

# ===========================================================================
#  BINARY EXPANSION PARAMETERS  (Section II-C, equations 29/53)
# ===========================================================================

BINARY_EXP_BITS = 24
"""B: number of bits in the binary expansion.
   With c=3 decimal digits, the maximum representable integer is 2^B - 1.
   B=24 can represent up to 16,777,215 → i.e., up to 16,777.215 MWh,
   which is more than enough for any single zone's at-the-money demand."""

BINARY_EXP_DECIMALS = 3
"""c: number of decimal digits. Italian market uses 3 (quantities in kWh).
   The binary expansion multiplies quantities by 10^c to get integers."""

# ===========================================================================
#  PUN ZONES  (the 6 Italian physical zones enforcing the UPP rule)
# ===========================================================================

PUN_ZONES = ("NORD", "CNOR", "CSUD", "SUD", "SICI", "SARD")
"""Zones where the Uniform Purchase Price (PUN) rule is enforced.
   Consumers in these zones pay π (unless they are pumping units).
   Producers in all zones receive their respective zonal price ζ_i."""

# ===========================================================================
#  NUMERICAL TOLERANCES
# ===========================================================================

EPS = 1e-4
"""Threshold for considering a volume as nonzero in solution extraction."""

VERBOSE = True
"""Whether to print solver output to stdout."""
