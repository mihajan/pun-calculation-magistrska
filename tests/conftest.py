"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from pun_clearing.conf import options
from pun_clearing.dataio.gme_importer import GMEImporter

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GME_DATE = "20100504"


@pytest.fixture(autouse=True)
def _quiet_solver():
    """Keep HiGHS output out of the test log."""
    previous = options.VERBOSE
    options.VERBOSE = False
    yield
    options.VERBOSE = previous


@pytest.fixture(scope="session")
def gme_importer() -> GMEImporter:
    """GME data for 2010-05-04, parsed once per test session (~1 s)."""
    return GMEImporter(path=str(DATA_DIR), date=GME_DATE)
