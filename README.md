# PUN Clearing — Savelli et al. (2017) MILP Implementation

Implementation of the MILP market clearing model for the Italian day-ahead
electricity market with Uniform Purchase Price (PUN) and zonal selling prices.

**Reference:** Savelli, Giannitrapani, Paoletti, Vicino — "An Optimization Model
for the Electricity Market Clearing Problem with Uniform Purchase Price and
Zonal Selling Prices," IEEE Trans. Power Syst., 2017.

## Setup

Requires Python ≥ 3.10.

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Linux / macOS:         source .venv/bin/activate
pip install -e ".[dev]"
```

`-e` installs the package in *editable* mode: the installed `pun_clearing`
points at `src/pun_clearing`, so code changes take effect without reinstalling.
`[dev]` also installs pytest, mypy and flake8.

## Usage

```bash
pun-clearing --path data --date 20100504 --quiet              # all 24 hours
pun-clearing --path data --date 20100504 --hours 8 9 13       # selected hours
```

Or from Python:

```python
from pun_clearing.dataio.gme_importer import GMEImporter
from pun_clearing.model.pun_model import build_and_solve

importer = GMEImporter(path="data", date="20100504")
hour = build_and_solve(importer.get_hourly_data()[9])
print(hour.pun_price, hour.zonal_prices)
```

## Development checks

```bash
flake8 src tests          # style
mypy                      # static types
pytest -m "not slow"      # fast tests (~5 s)
pytest -m slow            # validation against published GME prices (~2 min)
pytest                    # everything
```

The same checks run on GitHub Actions (`.github/workflows/ci.yml`).

## Project structure

```
├── pyproject.toml              # package metadata, dependencies, tool config
├── .flake8                     # flake8 config (flake8 does not read pyproject.toml)
├── data/                       # GME XML files for 2010-05-04
├── src/pun_clearing/
│   ├── conf/options.py         # solver config, big-M, binary expansion params
│   ├── model/data_classes.py   # Bid, Zone, Line, HourlyData — plain containers
│   ├── model/pun_model.py      # THE CORE: builds and solves the Savelli 2017 MILP
│   ├── dataio/gme_importer.py  # reads GME XML files into data classes
│   └── run_hourly.py           # CLI entry point (`pun-clearing`)
└── tests/
    ├── test_synthetic.py       # tiny hand-checkable market
    ├── test_gme_importer.py    # parsing of the GME files
    └── test_gme_validation.py  # model vs. published GME prices (slow)
```

## How the code maps to the paper

The MILP in Appendix A of Savelli 2017 has equations (34)–(66).
Each constraint in `pun_model.py` is labeled with the corresponding
equation number. The pipeline is:

1. **Bilevel model** (Section II-A): upper level handles UPP rule + binary
   variables; lower level is an LP that clears the market.
2. **Single-level equivalent** (Section II-B): replace lower level LP with
   strong duality + primal feasibility + dual feasibility.
3. **MILP linearization** (Section II-C): linearize all bilinear terms
   (binary × continuous via big-M = 3000, binary expansion for UPP definition).

Since we have NO block orders, each hour is independent → we solve 24
separate MILPs per day instead of one coupled 24-hour problem.

## Key simplifications vs. openDAM (Savelli et al. 2018)

- No block orders (no `rp`, `ubp`, `vphibMax`, `vphibMin`)
- No `u^w_tk` / `u^d_tk` split — only `u^e_k` for at-the-money orders
- No market-split detection (`uf` variables) — can be added later
- No `κ_t` tolerance — exact PUN definition (eq. 1 of the paper)
- Hourly independence — 24 small MILPs instead of 1 large one
