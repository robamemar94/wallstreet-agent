"""Caché de datos de mercado: un fallo de Yahoo (None) no tira lo último bueno y se reintenta pronto."""
import pytest

from app.application.services import market_cache as mc


@pytest.fixture(autouse=True)
def _memoria(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "CACHE_FILE", str(tmp_path / "cache.json"))
    monkeypatch.setattr(mc, "_data", {})
    monkeypatch.setattr(mc, "_loaded", True, raising=False)


def test_un_fallo_no_tira_lo_ultimo_bueno(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mc.time, "time", lambda: now[0])
    assert mc.cached("k", "AAA", lambda: 12.5, ttl_hours=12) == 12.5
    now[0] += 13 * 3600                                          # caducado: Yahoo falla
    assert mc.cached("k", "AAA", lambda: None, ttl_hours=12) == 12.5
    now[0] += 0.5 * 3600                                         # antes de la hora: no se vuelve a pedir
    assert mc.cached("k", "AAA", lambda: pytest.fail("no debería pedirlo"), ttl_hours=12) == 12.5
    now[0] += 1 * 3600                                           # pasada la hora: se reintenta
    assert mc.cached("k", "AAA", lambda: 13.0, ttl_hours=12) == 13.0


def test_un_none_sin_nada_se_reintenta_en_una_hora(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mc.time, "time", lambda: now[0])
    assert mc.cached("k", "BBB", lambda: None, ttl_hours=12) is None
    now[0] += 2 * 3600
    assert mc.cached("k", "BBB", lambda: 7.0, ttl_hours=12) == 7.0
