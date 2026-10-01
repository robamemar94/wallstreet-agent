"""F5 en Mercado y Cartera: se enseña al instante lo último que hay y los precios nuevos se piden en segundo plano.
Sin red: las descargas de precios se sustituyen por registros de llamada."""
import datetime
import time

import pytest
from fastapi.testclient import TestClient

from app.interfaces.views import market_views
from app.interfaces.views.market_views import INDEX_CACHE
from main import app

F5 = {"cache-control": "max-age=0"}


@pytest.fixture
def calls(monkeypatch):
    log = {"sync": 0, "background": 0}

    def fake_refresh(symbols, alert_tickers):
        log["sync"] += 1
        INDEX_CACHE["data"].update({s: {"price": 10.0, "prev": 10.0, "last_date": datetime.date.today()} for s in symbols})
        INDEX_CACHE["prices_update"] = time.time()

    def fake_background(symbols, alert_tickers):
        log["background"] += 1

    monkeypatch.setattr(market_views, "refresh_index_prices", fake_refresh)
    monkeypatch.setattr(market_views, "start_price_refresh", fake_background)
    monkeypatch.setattr(market_views, "_load_price_snapshot", lambda: None)
    saved = {k: INDEX_CACHE.get(k) for k in ("data", "prices_update", "last_update")}
    INDEX_CACHE.update({"data": {}, "prices_update": 0, "last_update": time.time()})   # sin refresco de eventos
    yield log
    INDEX_CACHE.update(saved)


def test_market_f5_refreshes_in_background_and_keeps_showing_prices(calls):
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert calls["sync"] == 1                       # primera vez: no hay nada que enseñar, se espera la descarga
    INDEX_CACHE["data"]["DUMMY_TICKER"] = {"price": 123.45, "prev": 123.45, "last_date": datetime.date.today()}

    assert client.get("/").status_code == 200        # visita normal con precios recientes: nada que descargar
    assert calls == {"sync": 1, "background": 0}

    assert client.get("/", headers=F5).status_code == 200   # F5 a los pocos segundos: no se repite la descarga
    assert calls["background"] == 0

    INDEX_CACHE["prices_update"] = time.time() - 60
    assert client.get("/", headers=F5).status_code == 200   # F5 con precios de hace un minuto: en segundo plano
    assert calls == {"sync": 1, "background": 1}
    assert "DUMMY_TICKER" in INDEX_CACHE["data"]     # mientras tanto se siguen enseñando los que había


def test_portfolio_f5_asks_for_fresh_prices(calls):
    client = TestClient(app)
    client.get("/")                                  # caché de precios llena
    INDEX_CACHE["prices_update"] = time.time() - 60
    assert client.get("/portfolio", headers=F5).status_code == 200
    assert calls["background"] == 1                  # la Cartera tampoco espera: los pide en segundo plano
