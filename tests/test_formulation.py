"""
The MILP must stay the Savelli et al. (2017) formulation (big-M = 3000).

The model of an hour is exported as an LP file and reduced to a canonical form:
every constraint (coefficients, sense, right-hand side), the objective, the bounds
and the binaries, independent of constraint names and ordering. Its hash must equal
the hash of the original model, recorded before any refactoring of the code.

If you change the model on purpose, recompute the hashes with canonical_hash().
"""

import hashlib
import re
from pathlib import Path

import pytest

from pun_clearing.model.pun_model import build_model

# sha256 of the canonical LP of the original (pre-refactor) model, 2010-05-04
ORIGINAL_MODEL_HASHES = {
    9: "e6e6cf4d883786c816c2d24ddb403235f16a3bb1303d302742d7f8801d1ce07f",
    13: "099c5e41fa0f42ceddef4cc2f5029f17ffbcd553f411666445e305606860b1f3",
}

_ROW_NAME = re.compile(r"^\S+:$")


def canonical_hash(lp_file: Path) -> str:
    rows: list[str] = []
    objective: list[str] = []
    bounds: list[str] = []
    binaries: list[str] = []
    section = ""
    current: list[str] = []

    def flush():
        if current:
            rows.append(" ".join(current))
            current.clear()

    for line in lp_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("\\"):
            continue
        keyword = line.lower()
        if keyword in ("max", "min", "s.t.", "bounds", "binary", "general", "end"):
            flush()
            section = keyword
        elif section in ("max", "min"):
            if not _ROW_NAME.match(line):
                objective.append(line)
        elif section == "s.t.":
            if _ROW_NAME.match(line):
                flush()
            else:
                current.append(line)
        elif section == "bounds":
            bounds.append(line)
        elif section == "binary":
            binaries.append(line)
    flush()

    def terms(tokens):
        return sorted((var, float(coef)) for coef, var in zip(tokens[::2], tokens[1::2]))

    def row(text):
        tokens = text.split()
        i = next(i for i, t in enumerate(tokens) if t in ("<=", ">=", "="))
        return (terms(tokens[:i]), tokens[i], float(tokens[i + 1]))

    canonical = (sorted(row(r) for r in rows), terms(" ".join(objective).split()),
                 sorted(bounds), sorted(binaries))
    return hashlib.sha256(repr(canonical).encode()).hexdigest()


@pytest.mark.parametrize("hour", sorted(ORIGINAL_MODEL_HASHES))
def test_formulation_unchanged(gme_day, hour, tmp_path):
    lp_file = tmp_path / f"hour{hour}.lp"
    build_model(gme_day.hours[hour]).write(
        str(lp_file), io_options={"symbolic_solver_labels": True})
    assert canonical_hash(lp_file) == ORIGINAL_MODEL_HASHES[hour]


def test_hash_detects_a_changed_coefficient(tmp_path):
    """Guard against a hash that ignores what it should check."""
    lp = "max\nobj:\n+1 x\n\ns.t.\n\nc1_:\n+3000.0 x\n-1 y\n<= 5\n\nbounds\n0 <= x <= 1\nend\n"
    a, b = tmp_path / "a.lp", tmp_path / "b.lp"
    a.write_text(lp)
    b.write_text(lp.replace("+3000.0 x", "+2999.0 x"))
    assert canonical_hash(a) != canonical_hash(b)
