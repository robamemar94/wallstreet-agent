import time

from app.application.services import market_cache


def test_cached_value_survives_restart_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(market_cache, "CACHE_FILE", str(tmp_path / "cache.json"))
    monkeypatch.setattr(market_cache, "_data", {})
    monkeypatch.setattr(market_cache, "_loaded", False)
    calls = []
    fetch = lambda: calls.append(1) or 7.5
    assert market_cache.cached("eps", "AAA", fetch) == 7.5
    assert market_cache.cached("eps", "AAA", fetch) == 7.5 and len(calls) == 1   # de memoria

    monkeypatch.setattr(market_cache, "_data", {})   # «reinicio»: se lee del disco
    monkeypatch.setattr(market_cache, "_loaded", False)
    assert market_cache.cached("eps", "AAA", fetch) == 7.5 and len(calls) == 1

    market_cache._data["eps"]["AAA"][0] = time.time() - 13 * 3600   # caducado (12 h)
    assert market_cache.cached("eps", "AAA", lambda: 8.0) == 8.0
