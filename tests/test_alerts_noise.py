"""Alertas sin ruido: margen en los límites de zona, una alerta de valoración por empresa y día, y caídas que se
cierran o se reabren sin volver a avisar."""

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.services import alerts_service as al, base_valuation_service as bv, price_monitor_service as pm
from app.infrastructure.db.database import Base
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository


def test_zone_needs_margin_to_change():
    # límite justo/cara en 8 %: con CAGR 8,0 % no se pasa de cara a justo hasta superar 8,5 %
    assert al.zone_with_margin(8.0, "CARA") == "CARA"
    assert al.zone_with_margin(8.3, "CARA") == "CARA"
    assert al.zone_with_margin(8.6, "CARA") == "PRECIO JUSTO"
    # y de justo a cara hasta bajar de 7,5 %
    assert al.zone_with_margin(7.9, "PRECIO JUSTO") == "PRECIO JUSTO"
    assert al.zone_with_margin(7.4, "PRECIO JUSTO") == "CARA"
    # sin zona anterior, la que toque
    assert al.zone_with_margin(8.0, None) == "PRECIO JUSTO"
    # saltos grandes: la zona confirmada con el margen
    assert al.zone_with_margin(25.0, "CARA") == "GANGA"


def _db(monkeypatch, cagrs, high=100.0, closes_fn=None):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    repo = SqlAlchemyAssetRepository(db)
    repo.save_asset_data("AAA", "status", "ACCEPTED")
    repo.toggle_favorite("AAA")
    it = iter(cagrs)
    def live(t, d, price):
        c = next(it)
        return {"cagr": c, "zone": bv.zone(c), "buy_price": 50.0, "inputs": {}}
    monkeypatch.setattr(bv, "live", live)
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=60, freq="B")
    monkeypatch.setattr(pm, "download_history", lambda symbols, fill=True: pd.DataFrame({"AAA": [high] * 60, "URTH": [100.0] * 60}, index=idx))
    return db, repo


def _val(db):
    return [h for h in al.get_alerts_data(db)["history"] if h["type"] == "VALORACIÓN"]


def test_wts_case_no_back_and_forth(monkeypatch):
    # CAGR rondando el 8 %: ni Cara→Justo ni Justo→Cara
    db, repo = _db(monkeypatch, [7.6, 8.1, 7.9, 8.2, 7.95])
    for _ in range(5):
        al.run_alerts_scan({"AAA": 100.0}, repo, db, {})
    assert _val(db) == []


def test_one_valuation_alert_per_day_and_reverting_removes_it(monkeypatch):
    db, repo = _db(monkeypatch, [7.0, 9.5, 14.0, 7.0])
    al.run_alerts_scan({"AAA": 100.0}, repo, db, {})            # cara (solo se registra)
    assert al.run_alerts_scan({"AAA": 100.0}, repo, db, {}) == 1  # cara → justo
    assert al.run_alerts_scan({"AAA": 100.0}, repo, db, {}) == 0  # justo → barata el mismo día: se actualiza la de hoy
    (a,) = _val(db)
    assert (a["old_status"], a["new_status"]) == ("CARA", "BARATA")
    al.run_alerts_scan({"AAA": 100.0}, repo, db, {})            # vuelve a cara el mismo día: no ha cambiado nada
    assert _val(db) == []


def _drops(db):
    return [h for h in al.get_alerts_data(db)["history"] if h["type"] == "CAÍDA"]


def test_drop_recovery_keeps_key_and_is_not_new(monkeypatch):
    db, repo = _db(monkeypatch, [None] * 10)
    monkeypatch.setattr(bv, "live", lambda t, d, p: None)
    al.run_alerts_scan({"AAA": 100.0}, repo, db, {})                 # primera pasada (sin caída)
    assert al.run_alerts_scan({"AAA": 88.0}, repo, db, {}) == 1      # −12 %: se abre el episodio
    key_open = al.build_alert_key(_drops(db)[0])
    assert al.run_alerts_scan({"AAA": 97.0}, repo, db, {}) == 0      # se recupera: cierre sin avisar
    (closed,) = _drops(db)
    assert closed["status"] == "cerrada" and al.build_alert_key(closed) == key_open


def test_drop_again_soon_reopens_without_alert_unless_worse(monkeypatch):
    db, repo = _db(monkeypatch, [None] * 10)
    monkeypatch.setattr(bv, "live", lambda t, d, p: None)
    al.run_alerts_scan({"AAA": 100.0}, repo, db, {})                 # primera pasada (sin caída)
    al.run_alerts_scan({"AAA": 88.0}, repo, db, {})                  # −12 %
    al.run_alerts_scan({"AAA": 97.0}, repo, db, {})                  # recuperada
    assert al.run_alerts_scan({"AAA": 89.0}, repo, db, {}) == 0      # vuelve a −11 %: mismo episodio, sin avisar
    (ep,) = _drops(db)
    assert ep["status"] == "abierta"
    assert al.run_alerts_scan({"AAA": 78.0}, repo, db, {}) == 1      # −22 %: peor que antes, ahora sí avisa


def test_limpia_cambios_de_zona_repetidos_en_30_dias():
    from app.application.services.alerts_service import drop_repeated_zone_changes
    z = lambda t, old, new, d: {"type": "VALORACIÓN", "ticker": t, "old_status": old, "new_status": new, "date": f"{d} 10:00:00"}
    hist = [z("SPGI", "PRECIO JUSTO", "BARATA", "2026-07-05"), z("SPGI", "PRECIO JUSTO", "BARATA", "2026-07-07"),   # repetida
            z("SPGI", "PRECIO JUSTO", "BARATA", "2026-07-21"),                                                    # repetida (16 d)
            z("SPGI", "BARATA", "PRECIO JUSTO", "2026-07-08"),                                                    # otro cambio: se queda
            z("SPGI", "PRECIO JUSTO", "BARATA", "2026-09-16"),                                                    # más de 30 d: se queda
            {"type": "CAÍDA", "ticker": "SPGI", "date": "2026-07-06 10:00:00"}]
    out = drop_repeated_zone_changes(hist)
    assert [h["date"][:10] for h in out if h["type"] == "VALORACIÓN"] == ["2026-07-05", "2026-07-08", "2026-09-16"]
    assert any(h["type"] == "CAÍDA" for h in out)
