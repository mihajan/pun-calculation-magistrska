# PUN Clearing — Savelli et al. (2017) MILP Implementation

Implementation of the MILP market clearing model for the Italian day-ahead
electricity market with Uniform Purchase Price (PUN) and zonal selling prices.

**Reference:** Savelli, Giannitrapani, Paoletti, Vicino — "An Optimization Model
for the Electricity Market Clearing Problem with Uniform Purchase Price and
Zonal Selling Prices," IEEE Trans. Power Syst., 2017.

## Results at a glance

Run on all orders of the Italian day-ahead market (MGP) of **4 May 2010**:

- the model reproduces the **published PUN in all 24 hours** (largest difference
  4.5·10⁻⁷ €/MWh) and **every zonal price** of the six Italian zones;
- the market splits in 17 of 24 hours (Sicily and Sardinia, at times also the north),
  with up to four price areas at once;
- every hour is solved to proven optimality with the open-source solver HiGHS.

📓 **[Results notebook](notebooks/results_20100504.ipynb)**: the model explained,
all results, charts and one hour (hour 9) examined in detail. It opens directly on
GitHub, without running anything. The hourly results are also in
[`results/20100504_hourly.csv`](results/20100504_hourly.csv).

## Where to look in the code

| What | Where |
|---|---|
| **The MILP (start here)** | [`model/pun_model.py`](src/pun_clearing/model/pun_model.py) → `build_model()` |
| Bilevel → single level | `_add_lower_level_dual()`, `_add_strong_duality()` |
| PUN definition (eq. 1) | `_add_pun_definition()`, `_add_binary_expansion()` |
| Big-M linearisations (M = 3000) | `_add_linearizations()` |
| ITM / ATM rules, merit order | `_add_upper_level()` |
| Parameters (M, ε, B, c, solver) | [`conf/options.py`](src/pun_clearing/conf/options.py) |
| Reading GME files | [`dataio/gme_importer.py`](src/pun_clearing/dataio/gme_importer.py) |
| Validation against GME prices | [`tests/test_gme_validation.py`](tests/test_gme_validation.py) |

Everything else (CLI, API, Docker) only wraps these.

## Setup

Requires Python ≥ 3.10.

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Linux / macOS:         source .venv/bin/activate
pip install -e ".[dev]"          # add ,notebook for the results notebook: ".[dev,notebook]"
```

`-e` installs the package in *editable* mode: the installed `pun_clearing`
points at `src/pun_clearing`, so code changes take effect without reinstalling.
`[dev]` also installs pytest, mypy and flake8.

## Usage

```bash
pun-clearing --path data --date 20100504 --quiet              # all 24 hours
pun-clearing --path data --date 20100504 --hours 8 9 13       # selected hours
pun-clearing --path data --date 20100504 --jobs 8 --quiet     # 8 hours in parallel
```

Or from Python:

```python
from pun_clearing.dataio.gme_importer import GMEImporter
from pun_clearing.model.pun_model import build_and_solve

importer = GMEImporter(path="data", date="20100504")
hour = build_and_solve(importer.get_hourly_data()[9])
print(hour.pun_price, hour.zonal_prices)
```

A single hour as JSON (model PUN, published PUN and, if the market split,
the zonal prices of the six Italian zones):

```bash
pun-clearing --path data --date 20100504 --hours 9 --json
```

## HTTP API and Docker

```bash
docker compose up --build          # or: docker build -t pun-clearing . && docker run -p 8000:8000 pun-clearing
```

Without Docker: `pip install -e ".[api]"` and `uvicorn pun_clearing.api:app`.

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `GET /dates` | dates (YYYYMMDD) with GME data on the server |
| `GET /clear/{date}/{hour}` | one cleared hour, e.g. `/clear/20100504/9` |
| `GET /docs` | interactive API documentation |

Example response of `/clear/20100504/9` (abridged):

```json
{
  "date": "20100504", "hour": 9,
  "pun_model": 68.498829, "pun_real": 68.498829, "pun_difference": -0.0,
  "market_split": true,
  "zonal_prices": [
    {"zone": "NORD", "model": 62.33, "real": 62.33, "difference": -0.0},
    {"zone": "SICI", "model": 154.01, "real": 154.01, "difference": -0.0},
    {"zone": "SARD", "model": 93.01, "real": 93.01, "difference": 0.0}
  ],
  "solve_time_s": 15.23, "solver_status": "optimal"
}
```

The CLI runs in the same image, e.g. for the whole day:

```bash
docker run --rm pun-clearing pun-clearing --path /app/data --date 20100504 --jobs 8 --quiet
```

`zonal_prices` is empty when the whole country has one price. A request solves
the hour (seconds to a few minutes); results are cached, so repeating it is
instant. Requests are solved one at a time. The data directory is
`PUN_DATA_DIR` (default `./data`; `/app/data` in the image).

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
├── Dockerfile, docker-compose.yml
├── data/                       # GME files for 2010-05-04 (zipped XML)
├── notebooks/                  # results notebook (saved with outputs)
├── results/                    # cached hourly results used by the notebook
├── src/pun_clearing/
│   ├── conf/options.py         # solver config, big-M, binary expansion params
│   ├── model/data_classes.py   # Bid, Zone, Line, HourlyData — plain containers
│   ├── model/pun_model.py      # THE CORE: builds and solves the Savelli 2017 MILP
│   ├── dataio/gme_importer.py  # reads GME XML files into data classes
│   ├── service.py              # clear one hour + compare with GME (used by CLI and API)
│   ├── api.py                  # FastAPI app
│   └── run_hourly.py           # CLI entry point (`pun-clearing`)
└── tests/
    ├── test_synthetic.py       # tiny hand-checkable market (+ parallel solving)
    ├── test_model_helpers.py   # helpers used to build the MILP
    ├── test_gme_importer.py    # parsing of the GME files
    ├── test_api.py             # single-hour service and HTTP API
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
