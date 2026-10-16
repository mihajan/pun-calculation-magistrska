# PUN clearing

Implementacija MILP modela iz članka Savelli et al. (2017) za italijanski day-ahead
trg. Proizvajalci so plačani po conski ceni, odjemalci pa plačajo PUN.

## Rezultati

Model je preizkušen na podatkih italijanskega trga za 4. maj 2010 (isti dan kot so  predstavljeni rezultati v članku):

- izračunani PUN je v vseh 24 urah enak objavljenemu;
- market split se pojavi v 17 urah; običajno se ločita Sicilija in Sardinija,
  v nekaterih urah tudi sever, tako da so hkrati lahko štiri različne cene;
- Uporablja se solver HiGHS, ki je prosto dostopen. Izračun posamezne ure traja v povprečju minuto.

Rezultati z grafi in podrobnejšo analizo ene izmed ur so vidni v
[notebooku](notebooks/results_20100504.ipynb).
Urni rezultati, ki jih notebook uporabi so shranjeni v
[`results/20100504_hourly.csv`](results/20100504_hourly.csv).

## Ključne funkcije

| Kaj | Lokacija |
|---|---|
| **MILP model** | [`model/pun_model.py`](src/pun_clearing/model/pun_model.py) → `build_model()` |
| Prehod iz bilevel v single-level problem | `_add_lower_level_dual()`, `_add_strong_duality()` |
| Definicija PUN (en. 1) | `_add_pun_definition()`, `_add_binary_expansion()` |
| Linearizacije z big-M (M = 3000) | `_add_linearizations()` |
| ITM / ATM in merit order | `_add_upper_level()` |
| PUN ponudbe brez cenovne omejitve (vedno sprejete) | `_fix_max_price_bids()` |
| Parametri (M, ε, B, c, solver) | [`conf/options.py`](src/pun_clearing/conf/options.py) |
| Branje podatkov GME | [`dataio/gme_importer.py`](src/pun_clearing/dataio/gme_importer.py) |
| Primerjava z objavljenimi cenami | [`tests/test_gme_validation.py`](tests/test_gme_validation.py) |


## Namestitev

Če želite sami prezkusiti celotno kodo je potrebno slediti naslednjim navodilom:

Potrebuješte Python 3.10 ali novejši.

**1. Prenesite repozitorij**

```bash
git clone https://github.com/mihajan/pun-calculation-magistrska.git
cd pun-calculation-magistrska
```

**2. Ustvarite in aktivirajte virtualno okolje**, da se paketi namestijo samo za ta projekt:

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows (PowerShell)
source .venv/bin/activate         # Linux / macOS
```

**3. Namestite paket** — izberi eno od možnosti:

```bash
pip install -e .                  # model in ukaz pun-clearing
pip install -e ".[notebook]"      # + vse, kar potrebuje notebook
pip install -e ".[dev]"           # + orodja za razvoj (pytest, mypy, flake8)
```

Solver HiGHS se namesti skupaj s paketom. Zaradi `-e` spremembe v kodi veljajo
takoj, brez ponovne namestitve.

## Uporaba

Ukaz `pun-clearing` prebere podatke GME za izbrani dan, izračuna izbrane ure in
rezultate primerja z objavljenimi cenami. Trenutno so v mapi podatki le za 4. maj 2010 in sicer v mapi `data`.

```bash
pun-clearing --path data --date 20100504 --hours 9               # ena ura
pun-clearing --path data --date 20100504 --hours 8 9 13          # več ur
pun-clearing --path data --date 20100504 --jobs 8 --quiet        # cel dan, 8 ur hkrati
```

| Možnost | Pomen |
|---|---|
| `--path` | mapa s podatki GME |
| `--date` | datum v obliki YYYYMMDD |
| `--hours` | ure, ki jih želiš izračunati (privzeto vseh 24) |
| `--jobs` | koliko ur se računa hkrati (privzeto 1) |
| `--quiet` | brez sprotnega izpisa solverja |
| `--json` | rezultati v obliki JSON namesto tabele |

Za vsako uro izpiše PUN iz modela, objavljeni PUN in razliko med njima, za ure z
market splitom pa še conske cene vseh šestih con. Cel dan po vrsti traja okoli
20 minut, z `--jobs 8` nekaj minut.

Model lahko pokličeš tudi iz Pythona:

```python
from pun_clearing.dataio.gme_importer import load_day
from pun_clearing.model.pun_model import build_and_solve

day = load_day("data", "20100504")
solution = build_and_solve(day.hours[9])
print(solution.pun_price, solution.zonal_prices)
```

Notebook odpreš v VS Code ali Jupytru; kot kernel izberi `.venv`.


## Struktura repozitorija

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
└── tests/                      #testi za pravilno delovanje in oblikovanje kode
```

## Povezava s člankom

MILP iz dodatka A članka obsega enačbe (34)–(66). Pri vsaki omejitvi v
`pun_model.py` je v komentarju številka enačbe, ki se direktno nanaša na članek.

Tako izvirni članek kot implementacija v tem repozitoriju ne sprejemata block orderjev in so zato ure med seboj neodvisne. Namesto enega velikega
problema za cel dan zato rešimo 24 manjših MILP-ov. To vpliva tudi na testno obdobje, saj moramo MILP testirati na (zelo) starih podatkih, ko še ni bilo block orderjev, saj le tako lahko dobimo rezultate enake objavljenim. 


