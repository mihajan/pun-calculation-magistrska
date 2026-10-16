"""Tests for the per-hour summary printed by the CLI."""

import pytest

from pun_clearing.model.pun_model import build_and_solve
from pun_clearing.run_hourly import HourResult, summarize

from conftest import GME_DATE


def _solve_and_summarize(gme_importer, hour) -> HourResult:
    data = build_and_solve(gme_importer.get_hourly_data()[hour], solver_name="appsi_highs")
    return summarize(GME_DATE, data, gme_importer.get_real_prices()[hour])


def test_hour_with_split(gme_importer):
    """Hour 24 (~2 s): SICI and SARD separate from the rest of Italy."""
    r = _solve_and_summarize(gme_importer, 24)
    assert r.pun_model == pytest.approx(56.011383, abs=1e-5)
    assert r.pun_real == pytest.approx(56.011383)
    assert r.market_split
    assert r.solver_status == "optimal"
    zones = {z.zone: z for z in r.zonal_prices}
    assert list(zones) == ["NORD", "CNOR", "CSUD", "SUD", "SICI", "SARD"]
    assert zones["SICI"].model == pytest.approx(87.01, abs=1e-3)
    assert zones["SARD"].model == pytest.approx(78.01, abs=1e-3)
    assert zones["NORD"].model == pytest.approx(53.0, abs=1e-3)
    assert all(z.difference is not None and abs(z.difference) < 1e-3 for z in r.zonal_prices)


@pytest.mark.slow
def test_hour_without_split(gme_importer):
    """Hour 7 (~25 s): one price everywhere, so no zonal prices are listed."""
    r = _solve_and_summarize(gme_importer, 7)
    assert r.pun_model == pytest.approx(47.5, abs=1e-5)
    assert not r.market_split
    assert r.zonal_prices == []
