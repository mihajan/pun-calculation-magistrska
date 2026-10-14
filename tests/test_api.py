"""Tests for the single-hour service and the HTTP API."""

import pytest
from fastapi.testclient import TestClient

from pun_clearing import service
from pun_clearing.api import app

from conftest import DATA_DIR, GME_DATE


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch):
    monkeypatch.setenv("PUN_DATA_DIR", str(DATA_DIR))


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_available_dates():
    assert service.available_dates() == [GME_DATE]


def test_clear_hour_with_split():
    """Hour 24 (~2 s): SICI and SARD separate from the rest of Italy."""
    r = service.clear_hour(GME_DATE, 24)
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
def test_clear_hour_without_split():
    """Hour 7 (~25 s): one price everywhere, so no zonal prices are listed."""
    r = service.clear_hour(GME_DATE, 7)
    assert r.pun_model == pytest.approx(47.5, abs=1e-5)
    assert not r.market_split
    assert r.zonal_prices == []


def test_unknown_date_or_hour():
    with pytest.raises(KeyError):
        service.clear_hour("19990101", 1)
    with pytest.raises(KeyError):
        service.clear_hour(GME_DATE, 25)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_dates_endpoint(client):
    assert client.get("/dates").json() == [GME_DATE]


def test_clear_endpoint(client):
    response = client.get(f"/clear/{GME_DATE}/24")
    assert response.status_code == 200
    body = response.json()
    assert body["hour"] == 24
    assert body["pun_model"] == pytest.approx(body["pun_real"], abs=1e-5)
    assert body["market_split"] is True
    assert {z["zone"] for z in body["zonal_prices"]} == {
        "NORD", "CNOR", "CSUD", "SUD", "SICI", "SARD"}


def test_clear_endpoint_errors(client):
    assert client.get("/clear/19990101/1").status_code == 404
    assert client.get(f"/clear/{GME_DATE}/25").status_code == 404
    assert client.get(f"/clear/{GME_DATE}/0").status_code == 422      # hour out of range
    assert client.get("/clear/2010-05-04/1").status_code == 422       # bad date format
