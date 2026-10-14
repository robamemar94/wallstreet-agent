"""Liquidez del bróker: saldo de partida + aportaciones, retiradas, compras, ventas y dividendos (sin internet)."""
import datetime

import pytest

from app.application.services import cash_service as cash


def test_saldo_desde_el_ancla():
    anchor = {"date": "2026-10-01", "balance": 1000.0}
    moves = [{"id": "a", "date": "2026-10-02", "type": "deposit", "amount": 500.0, "note": ""},
             {"id": "b", "date": "2026-09-30", "type": "deposit", "amount": 999.0, "note": "antes del ancla: ya está dentro"},
             {"id": "c", "date": "2026-10-05", "type": "withdrawal", "amount": 100.0, "note": ""}]
    auto = [{"date": "2026-10-03", "type": "buy", "ticker": "AAA", "amount": -800.0},
            {"date": "2026-10-04", "type": "sell", "ticker": "BBB", "amount": 300.0},
            {"date": "2026-10-06", "type": "dividend", "ticker": "AAA", "amount": 12.5},
            {"date": "2026-10-01", "type": "buy", "ticker": "CCC", "amount": -50.0}]   # el día del ancla: ya descontado
    st = cash.compute(anchor, moves, auto, datetime.date(2026, 10, 14))
    assert st["balance"] == pytest.approx(1000 + 500 - 100 - 800 + 300 + 12.5)
    assert st["since"]["buy"] == pytest.approx(-800)
    assert st["month"]["deposited"] == pytest.approx(500)


def test_sin_configurar():
    assert cash.compute(None, [], [], datetime.date(2026, 10, 14)) == {"configured": False}


def test_aviso_del_mes(monkeypatch):
    base = {"configured": True, "balance": 1234.0, "anchor": {"date": "2026-09-10", "balance": 0},
            "month": {"key": "2026-10", "label": "octubre", "deposited": 0.0}}
    monkeypatch.setattr(cash, "status", lambda db, today=None: base)
    out = cash.notices(None, datetime.date(2026, 10, 1))
    assert out and out[0]["kind"] == "cash_deposit" and "octubre" in out[0]["text"]
    base["month"]["deposited"] = 300.0                       # ya apuntada la aportación del mes: se cierra
    assert cash.notices(None, datetime.date(2026, 10, 1)) == []
    base["month"]["deposited"], base["anchor"]["date"] = 0.0, "2026-10-14"   # se empezó este mes: no avisa
    assert cash.notices(None, datetime.date(2026, 10, 20)) == []


def test_api_liquidez(monkeypatch):
    # contra la copia temporal de la base de datos (conftest): saldo, aportación, borrar
    from fastapi.testclient import TestClient
    from main import app
    from app.application.services import cash_service
    from app.infrastructure.db.database import SessionLocal
    monkeypatch.setattr(cash_service, "trade_flows", lambda db: [])
    monkeypatch.setattr(cash_service, "dividend_flows", lambda db: [])
    db = SessionLocal()
    cash_service.save(db, {"anchor": None, "movements": []})
    c = TestClient(app)
    assert c.get("/api/portfolio/liquidez").json() == {"configured": False}
    st = c.post("/api/portfolio/liquidez/saldo", json={"amount": 1000, "date": "2026-01-01"}).json()
    assert st["balance"] == pytest.approx(1000)
    st = c.post("/api/portfolio/liquidez/movimiento", json={"type": "deposit", "amount": 250, "date": "2026-01-05", "note": "nómina"}).json()
    assert st["balance"] == pytest.approx(1250)
    assert c.post("/api/portfolio/liquidez/movimiento", json={"type": "deposit", "amount": -5}).status_code == 400
    move_id = next(m["id"] for m in st["recent"] if m.get("manual"))
    assert c.delete(f"/api/portfolio/liquidez/movimiento/{move_id}").json()["balance"] == pytest.approx(1000)
    db.close()


def test_curva_con_liquidez():
    # 1.000 € invertidos que suben un 10 % y 1.000 € en efectivo: el patrimonio sube un 5 % (la liquidez lastra)
    dates = ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    values = [900.0, 1000.0, 1100.0, 1100.0]
    nav = [9.0, 10.0, 11.0, 11.0]
    anchor = {"date": "2026-10-14", "balance": 1000.0}
    moves = [{"id": "x", "date": "2026-10-16", "type": "deposit", "amount": 500.0, "note": ""}]   # aportar no es ganar
    out = cash.patrimony_series(dates, values, nav, anchor, moves, [])
    assert out["uv"][0] is None                              # antes del saldo de partida
    assert out["uv"][1] == pytest.approx(10.0)               # arranca junto a la cartera
    assert out["uv"][2] == pytest.approx(10.0 * 2100 / 2000)
    assert out["uv"][3] == pytest.approx(out["uv"][2])       # la aportación no mueve el índice
    assert out["eur"][3] == pytest.approx(1100 + 1500)
    assert out["cash_pct"][1] == pytest.approx(50.0)


def test_distribucion_con_liquidez():
    from app.application.services import distribution_service
    pos = [{"ticker": "AAA", "value": 900.0, "currency": "USD"}, {"ticker": "BBB", "value": 100.0, "currency": "EUR"}]
    d = distribution_service.build(pos, [], cash=1000.0)
    ccy = {c["label"]: c["pct"] for c in d["currency"]}
    assert ccy == pytest.approx({"USD": 45.0, "EUR": 55.0})       # la divisa, con la liquidez en euros
    assert d["cash"]["pct"] == pytest.approx(50.0)
    assert d["concentration"]["max_pct"] == pytest.approx(90.0)   # los pesos, sobre lo invertido
