"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from pun_clearing.dataio.gme_importer import GMEDay, load_day

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GME_DATE = "20100504"


@pytest.fixture(scope="session")
def gme_day() -> GMEDay:
    """GME data for 2010-05-04, parsed once per test session (~1 s)."""
    return load_day(DATA_DIR, GME_DATE)
