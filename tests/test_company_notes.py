from fastapi.testclient import TestClient


def test_notes_api_and_timeline():
    from main import app
    from app.application.services import company_notes_service as cn
    from app.infrastructure.db.database import SessionLocal
    c = TestClient(app)
    r = c.post("/api/ticker/zzdiary/notas", json={"text": "  resultados flojos; aguanto  ", "date": "2026-03-10"}).json()
    note = r["note"]
    assert (note["ticker"], note["date"], note["text"]) == ("ZZDIARY", "2026-03-10", "resultados flojos; aguanto")
    assert c.post("/api/ticker/ZZDIARY/notas", json={"text": "   "}).status_code == 400
    assert c.post("/api/ticker/ZZDIARY/notas", json={"text": "x", "date": "ayer"}).status_code == 400
    assert c.post(f"/api/notas/{note['id']}", json={"text": "cambio de idea"}).json()["note"]["text"] == "cambio de idea"
    db = SessionLocal()
    txs = [{"ticker": "ZZDIARY", "date": "2026-03-10", "type": "BUY", "shares": 2, "price": 10.0, "currency": "USD"},
           {"ticker": "ZZDIARY", "date": "2026-03-10", "type": "BUY", "shares": 3, "price": 11.0, "currency": "USD"},
           {"ticker": "OTRA", "date": "2026-03-11", "type": "BUY", "shares": 1, "price": 5.0, "currency": "USD"}]
    items = cn.timeline(db, "zzdiary", txs, {"tx_notes": {"2026-03-10|BUY|2|10": "entro en dos tramos"}})
    assert [i["kind"] for i in items] == ["note", "op"]                       # el mismo día, la nota primero
    assert items[1]["orders"] == 2 and items[1]["text"] == "entro en dos tramos" and items[1]["shares"] == 5
    assert cn.notes_between(db, "2026-03-01", "2026-03-31")[0] == {"ticker": "ZZDIARY", "date": "2026-03-10", "text": "cambio de idea"}
    w = cn.written(db, txs, {"ZZDIARY": {"tx_notes": {"2026-03-10|BUY|2|10": "entro en dos tramos"}}}, "zzdiary")
    assert [(i["kind"], i["text"]) for i in w] == [("note", "cambio de idea"), ("op", "entro en dos tramos")]   # OTRA, sin porqué: fuera
    assert c.delete(f"/api/notas/{note['id']}").json() == {"status": "success"}
    assert c.delete(f"/api/notas/{note['id']}").status_code == 404
    assert cn.count(db, "ZZDIARY") == 0


def test_diary_page_lists_notes_and_pending(monkeypatch):
    from main import app
    from app.application.services import price_monitor_service as pm
    monkeypatch.setattr(pm, "load_transactions", lambda ps, db: (
        [{"ticker": "ZZPEND", "date": "2099-01-02", "type": "BUY", "shares": 1, "price": 5.0, "currency": "USD"}], {}))
    c = TestClient(app)
    note = c.post("/api/ticker/ZZPAGE/notas", json={"text": "nota de la pantalla"}).json()["note"]
    html = c.get("/diario").text
    assert "nota de la pantalla" in html and "ZZPEND" in html          # la nota y la operación sin porqué
    assert "nota de la pantalla" not in c.get("/diario?ticker=OTRA").text
    c.delete(f"/api/notas/{note['id']}")


def test_waiting_entries_see_missed_and_reached(monkeypatch):
    import pandas as pd
    from app.application.services import report_service as rs, split_service, price_monitor_service as pm
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset
    db = SessionLocal()
    db.query(DBAsset).filter(DBAsset.ticker.in_(("ZZMISS", "ZZHIT"))).delete()
    db.add(DBAsset(ticker="ZZMISS", data={"my_buy_price_log": [{"date": "2026-03-02", "price": 90.0}, {"date": "2026-05-01", "price": None}]}))
    db.add(DBAsset(ticker="ZZHIT", data={"my_buy_price": {"price": 95.0, "set_at": "2026-03-02"}}))
    db.commit()
    idx = pd.date_range("2026-03-02", "2026-06-30", freq="B")
    df = pd.DataFrame({"ZZMISS": [100.0 + i for i in range(len(idx))], "ZZHIT": [100.0 - (i % 10) for i in range(len(idx))]}, index=idx)
    monkeypatch.setattr(pm, "download_history", lambda syms, fill=True: df)
    monkeypatch.setattr(split_service, "fetch_splits", lambda t: [])
    txs = [{"ticker": "ZZHIT", "date": "2026-03-20", "type": "BUY", "shares": 1, "price": 94.0}]
    w = {e["ticker"]: e for e in rs.waiting_entries(db, txs, "2026-01-01", "2026-06-30")["entries"]}
    miss, hit = w["ZZMISS"], w["ZZHIT"]
    assert (miss["reached"], miss["bought"], miss["removed"], miss["until"]) == (False, False, True, "2026-05-01")   # no llegó y lo quitaste
    assert miss["move_since_set_pct"] > 0
    assert (hit["reached"], hit["bought"], hit["low"]) == (True, True, 91.0)
    db.query(DBAsset).filter(DBAsset.ticker.in_(("ZZMISS", "ZZHIT"))).delete()
    db.commit()


def test_my_price_keeps_its_history():
    from main import app
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset
    c = TestClient(app)
    for price in (150, 150, 140, None):   # repetir el mismo precio no añade nada; quitarlo deja None
        assert c.put("/api/watchlist/ZZLOG/mi-precio", json={"price": price}).status_code == 200
    db = SessionLocal()
    log = (db.get(DBAsset, "ZZLOG").data or {}).get("my_buy_price_log")
    assert [e["price"] for e in log] == [150, 140, None]
    db.query(DBAsset).filter(DBAsset.ticker == "ZZLOG").delete()
    db.commit()
