# PUN clearing

Implementacija MILP modela iz članka Savelli et al. (2017) za italijanski day-ahead
trg. Proizvajalci prejmejo consko ceno, odjemalci pa plačajo PUN.

Savelli, Giannitrapani, Paoletti, Vicino: *An Optimization Model for the Electricity
Market Clearing Problem with Uniform Purchase Price and Zonal Selling Prices*,
IEEE Transactions on Power Systems, 2017.

## Rezultati

Model je preizkušen na podatkih trga MGP za 4. maj 2010:

- izračunani PUN je v vseh 24 urah enak objavljenemu (največja razlika je
  4,5·10⁻⁷ €/MWh), enako velja za conske cene vseh šestih italijanskih con;
- market split se pojavi v 17 urah; običajno se ločita Sicilija in Sardinija,
  v nekaterih urah tudi sever, tako da so hkrati lahko štiri različne cene;
- HiGHS vse ure reši optimalno.

Rezultati z grafi in podrobnejšo analizo 9. ure so v
[notebooku](notebooks/results_20100504.ipynb), ki ga lahko pregledaš kar na GitHubu.
Urni rezultati so shranjeni tudi v
[`results/20100504_hourly.csv`](results/20100504_hourly.csv).

## Kje začeti

| Kaj | Kje |
|---|---|
| **MILP model** | [`model/pun_model.py`](src/pun_clearing/model/pun_model.py) → `build_model()` |
| Prehod iz bilevel v single-level problem | `_add_lower_level_dual()`, `_add_strong_duality()` |
| Definicija PUN (en. 1) | `_add_pun_definition()`, `_add_binary_expansion()` |
| Linearizacije z big-M (M = 3000) | `_add_linearizations()` |
| ITM / ATM in merit order | `_add_upper_level()` |
| Parametri (M, ε, B, c, solver) | [`conf/options.py`](src/pun_clearing/conf/options.py) |
| Branje podatkov GME | [`dataio/gme_importer.py`](src/pun_clearing/dataio/gme_importer.py) |
| Primerjava z objavljenimi cenami | [`tests/test_gme_validation.py`](tests/test_gme_validation.py) |

CLI je le ovoj okoli teh delov.

## Namestitev

Potrebuješ Python 3.10 ali novejši.

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Linux / macOS:         source .venv/bin/activate
pip install -e ".[dev]"          # z notebookom: ".[dev,notebook]"
```

Zaradi `-e` paket kaže naravnost na `src/pun_clearing`, zato spremembe v kodi
veljajo takoj, brez ponovne namestitve. `[dev]` doda še pytest, mypy in flake8.

## Uporaba

```bash
pun-clearing --path data --date 20100504 --quiet              # vseh 24 ur
pun-clearing --path data --date 20100504 --hours 8 9 13       # samo izbrane ure
pun-clearing --path data --date 20100504 --jobs 8 --quiet     # 8 ur hkrati
```

Iz Pythona:

```python
from pun_clearing.dataio.gme_importer import load_day
from pun_clearing.model.pun_model import build_and_solve

day = load_day("data", "20100504")
solution = build_and_solve(day.hours[9])
print(solution.pun_price, solution.zonal_prices)
```

Za eno uro v obliki JSON (PUN iz modela, objavljeni PUN, ob market splitu pa še
conske cene):

```bash
pun-clearing --path data --date 20100504 --hours 9 --json
```

## Testi

```bash
flake8 src tests          # slog kode
mypy                      # preverjanje tipov
pytest -m "not slow"      # hitri testi (~5 s)
pytest -m slow            # primerjava z objavljenimi cenami GME (~2 min)
pytest                    # vsi testi
```

Vse to se ob vsakem pushu samodejno požene tudi na GitHub Actions
(`.github/workflows/ci.yml`).

## Struktura projekta

```
├── pyproject.toml              # opis paketa, odvisnosti, nastavitve orodij
├── .flake8                     # nastavitve flake8 (pyproject.toml ne bere)
├── data/                       # podatki GME za 4. 5. 2010 (zipani XML)
├── notebooks/                  # notebook z rezultati (skupaj z izpisi)
├── results/                    # shranjeni urni rezultati za notebook
├── src/pun_clearing/
│   ├── conf/options.py         # solver, big-M, parametri binary expansion
│   ├── model/data_classes.py   # Order, Zone, HourlyData (vhod), Solution (rezultat)
│   ├── model/pun_model.py      # jedro: zgradi in reši MILP
│   ├── dataio/gme_importer.py  # branje XML datotek GME
│   └── run_hourly.py           # CLI (`pun-clearing`): izračun ur in primerjava z GME
└── tests/
    ├── test_synthetic.py       # majhen trg, ki ga lahko preveriš na roko
    ├── test_model_helpers.py   # pomožne funkcije modela
    ├── test_gme_importer.py    # branje podatkov GME
    ├── test_report.py          # povzetek ure, ki ga izpiše CLI
    └── test_gme_validation.py  # model proti objavljenim cenam (počasen)
```

## Koda in članek

MILP iz dodatka A članka obsega enačbe (34)–(66). Pri vsaki omejitvi v
`pun_model.py` je v komentarju številka enačbe. Model nastane v treh korakih:

1. **Bilevel model** (II-A): zgornji nivo določa PUN in binarne spremenljivke,
   spodnji nivo je LP, ki počisti trg.
2. **Single-level** (II-B): spodnji LP nadomestimo s pogoji primarne in dualne
   dopustnosti ter s strong duality.
3. **MILP** (II-C): bilinearne člene lineariziramo — produkte binarne in zvezne
   spremenljivke z big-M = 3000, definicijo PUN pa z binary expansion.

Ker ni block orders, so ure med seboj neodvisne. Namesto enega velikega
problema za cel dan zato rešimo 24 manjših MILP-ov.

## Razlike glede na openDAM (Savelli et al. 2018)

- ni block orders (ni `rp`, `ubp`, `vphibMax`, `vphibMin`)
- ni delitve na `u^w_tk` / `u^d_tk`, za ponudbe at-the-money je samo `u^e_k`
- ni zaznavanja market splita (spremenljivke `uf`), to lahko dodamo kasneje
- ni tolerance `κ_t`, PUN je definiran točno (en. 1 v članku)
- vsaka ura je svoj problem: 24 majhnih MILP-ov namesto enega velikega
