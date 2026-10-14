import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.services import alerts_service, base_valuation_service as bv, price_monitor_service as pm
from app.infrastructure.db.database import Base
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository

VAL = {"metric": "eps", "metric_value": 5.0, "growth": 0.10, "exit_multiple": 20.0, "dps": 0.0, "div_growth": 0.0, "years": 5}


def _setup(monkeypatch, legacy=False, high=100.0):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    repo = SqlAlchemyAssetRepository(db)
    repo.save_asset_data("AAA", "status", "ACCEPTED")
    repo.toggle_favorite("AAA")   # la watchlist (y sus alertas) son las favoritas y las añadidas a mano
    if legacy:
        repo.save_asset_data("AAA", "projection_scenarios", {"initial_eps": 5, "base": {"eps_growth": 0.1, "exit_pe": 20}})
    else:
        repo.save_asset_data("AAA", bv.ASSET_KEY, VAL)
    monkeypatch.setattr(bv, "_market_info", lambda t: {"trailingEps": 5.0, "dividendRate": 0.0})
    monkeypatch.setattr(bv, "analyst_eps_estimates", lambda t: None)
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=60, freq="B")
    hist = pd.DataFrame({"AAA": [high] * 60, "URTH": [100.0] * 59 + [95.0]}, index=idx)
    monkeypatch.setattr(pm, "download_history", lambda symbols, fill=True: hist)
    return db, repo


def _alerts(db, kind):
    return [h for h in alerts_service.get_alerts_data(db)["history"] if h["type"] == kind]


def test_zone_change_creates_alert_but_first_pass_only_records(monkeypatch):
    db, repo = _setup(monkeypatch, high=150.0)
    alerts_service.run_alerts_scan({"AAA": 150.0}, repo, db, {})      # CAGR ≈ 1,4 %: cara (solo se registra)
    assert _alerts(db, "VALORACIÓN") == []
    alerts_service.run_alerts_scan({"AAA": 65.0}, repo, db, {})       # precio de compra ≈ 70: ganga
    (a,) = _alerts(db, "VALORACIÓN")
    assert (a["old_status"], a["new_status"]) == ("CARA", "GANGA") and a["cagr"] > 18 and a["buy_price"] > 65


def test_legacy_valuation_does_not_alert(monkeypatch):
    db, repo = _setup(monkeypatch, legacy=True)
    alerts_service.run_alerts_scan({"AAA": 150.0}, repo, db, {})
    alerts_service.run_alerts_scan({"AAA": 60.0}, repo, db, {})
    assert _alerts(db, "VALORACIÓN") == []


def test_drop_is_one_episode_updated_in_place_until_it_recovers(monkeypatch):
    db, repo = _setup(monkeypatch)                 # máximo de 52 semanas: 100
    data = alerts_service.get_alerts_data(db); data["drops_v2"] = True; alerts_service.save_alerts_data(db, data)
    key = lambda: alerts_service.build_alert_key(_alerts(db, "CAÍDA")[0])

    alerts_service.run_alerts_scan({"AAA": 95.0}, repo, db, {})
    assert _alerts(db, "CAÍDA") == []              # −5 %: nada
    assert alerts_service.run_alerts_scan({"AAA": 88.0}, repo, db, {}) == 1
    first = key()
    alerts_service.run_alerts_scan({"AAA": 85.0}, repo, db, {})   # sigue en el escalón del 10 %: se actualiza, no es nueva
    (a,) = _alerts(db, "CAÍDA")
    assert key() == first and a["drop_pct"] == 15.0 and a["buy_gap"] is not None and a["bench_pct"] == -5.0
    alerts_service.run_alerts_scan({"AAA": 78.0}, repo, db, {})   # −22 %: mismo episodio, vuelve a salir como nuevo
    assert len(_alerts(db, "CAÍDA")) == 1 and key() != first and _alerts(db, "CAÍDA")[0]["escalon"] == 20
    alerts_service.run_alerts_scan({"AAA": 90.0}, repo, db, {})   # rebote a −10 %: no cierra
    assert _alerts(db, "CAÍDA")[0]["status"] == "abierta" and _alerts(db, "CAÍDA")[0]["max_drop"] == 22.0
    alerts_service.run_alerts_scan({"AAA": 96.0}, repo, db, {})   # a menos de un 5 % del máximo: se cierra
    (c,) = _alerts(db, "CAÍDA")
    assert c["status"] == "cerrada" and c["max_drop"] == 22.0


def test_ignored_drop_comes_back_only_if_it_gets_worse(monkeypatch):
    db, repo = _setup(monkeypatch)
    data = alerts_service.get_alerts_data(db); data["drops_v2"] = True; alerts_service.save_alerts_data(db, data)
    alerts_service.run_alerts_scan({"AAA": 88.0}, repo, db, {})
    data = alerts_service.get_alerts_data(db)          # «Ignorar» (como hace /api/alerts/delete)
    data["history"] = []; data["drops"]["AAA"]["dismissed"] = True; alerts_service.save_alerts_data(db, data)
    alerts_service.run_alerts_scan({"AAA": 86.0}, repo, db, {})
    assert _alerts(db, "CAÍDA") == []
    alerts_service.run_alerts_scan({"AAA": 75.0}, repo, db, {})
    assert _alerts(db, "CAÍDA")[0]["escalon"] == 20


def test_old_drop_alerts_are_replaced_by_silent_episodes(monkeypatch):
    db, repo = _setup(monkeypatch)
    alerts_service.save_alerts_data(db, {"history": [{"type": "CAÍDA", "ticker": "AAA", "escalon": 50, "date": "2026-01-01"}],
                                         "ath_refs": {"AAA": 300}})
    alerts_service.run_alerts_scan({"AAA": 85.0}, repo, db, {})
    (a,) = _alerts(db, "CAÍDA")
    assert a["episode"] and a["silent"] and a["escalon"] == 10 and "ath_refs" not in alerts_service.get_alerts_data(db)


def test_tier3_only_warns_of_big_drops(monkeypatch):
    db, repo = _setup(monkeypatch)
    repo.toggle_favorite("AAA")   # sin estrella pero aceptada: Tier 3 fuera de la watchlist, solo caídas desde un 20 %
    alerts_service.run_alerts_scan({"AAA": 85.0}, repo, db, {})
    assert alerts_service.get_alerts_data(db).get("drops") == {}
    alerts_service.run_alerts_scan({"AAA": 75.0}, repo, db, {})
    assert alerts_service.get_alerts_data(db)["drops"]["AAA"]["escalon"] == 20


def test_tier2_warns_like_the_watchlist(monkeypatch):
    from app.application.services import tier_service
    db, repo = _setup(monkeypatch)
    tier_service.set_tier(db, "AAA", "2")   # Tier 2 fuera de la watchlist: avisa igual que ella (desde un 10 %)
    alerts_service.run_alerts_scan({"AAA": 85.0}, repo, db, {})
    assert alerts_service.get_alerts_data(db)["drops"]["AAA"]["escalon"] == 10


def test_old_alerts_are_purged_but_open_drops_are_kept():
    old, recent = "2025-01-01 10:00:00", "2099-01-01 10:00:00"
    history = [{"type": "VALORACIÓN", "date": old}, {"type": "VALORACIÓN", "date": recent},
               {"type": "CAÍDA", "episode": True, "status": "abierta", "date": old, "updated": old},
               {"type": "CAÍDA", "episode": True, "status": "cerrada", "date": old, "closed_date": old}]
    kept = alerts_service.purge_old(history, 180)
    assert [(a["type"], a.get("status")) for a in kept] == [("VALORACIÓN", None), ("CAÍDA", "abierta")]
