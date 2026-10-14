from app.application.services import tier_service as ts


def test_levels():
    assert ts.tier_of(True, {"status": "ACCEPTED"}) == "1"                 # la estrella = Tier 1
    assert ts.tier_of(False, {"watch_tier": "2", "status": "ACCEPTED"}) == "2"
    assert ts.tier_of(False, {"status": "ACCEPTED"}) == "seg"
    assert ts.tier_of(False, {"status": "REJECTED"}) is None
    assert ts.tier_of(True, {}, held=True) == "cartera"


def test_set_tier_moves_the_star_and_warns_past_the_soft_max():
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset
    db = SessionLocal()
    a = db.query(DBAsset).filter(DBAsset.is_favorite.is_(False)).first()
    out = ts.set_tier(db, a.ticker, "2")
    db.refresh(a)
    assert out["tier"] == "2" and not a.is_favorite and a.data.get("watch_tier") == "2" and a.data.get("status") == "ACCEPTED"
    out = ts.set_tier(db, a.ticker, "1")
    db.refresh(a)
    assert a.is_favorite and "watch_tier" not in a.data
    assert (out["warning"] is not None) == (out["tier1_count"] > ts.TIER1_SOFT_MAX)
    ts.set_tier(db, a.ticker, None)
    db.refresh(a)
    assert not a.is_favorite and ts.tier_of(a.is_favorite, a.data) == "seg"   # sigue aceptada: queda en seguimiento


def test_best_candidate_prefers_buy_zone_then_cagr():
    rows = [{"ticker": "A", "held": False, "drop_pct": 12.0, "cagr": 20.0},
            {"ticker": "B", "held": False, "drop_pct": -2.0, "cagr": 11.0},     # ya en zona
            {"ticker": "C", "held": False, "drop_pct": -5.0, "cagr": 14.0},     # en zona y más CAGR
            {"ticker": "D", "held": True, "drop_pct": -9.0, "cagr": 30.0},      # la tienes: no cuenta
            {"ticker": "E", "held": False, "drop_pct": None, "cagr": None}]     # sin valoración
    assert ts.best_candidate(rows)["ticker"] == "C"
    assert ts.best_candidate([rows[3], rows[4]]) is None


def test_add_many_to_a_new_theme():
    from fastapi.testclient import TestClient
    from main import app
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset, DBCompanyList
    tickers = [a.ticker for a in SessionLocal().query(DBAsset).limit(3).all()]
    client = TestClient(app)
    r = client.post("/api/temas/anadir", json={"tickers": tickers, "new_name": "Industriales prueba"})
    assert r.status_code == 200 and sorted(r.json()["added"]) == sorted(tickers)
    db = SessionLocal()
    lst = db.query(DBCompanyList).filter(DBCompanyList.name == "Industriales prueba").one()
    assert sorted(a.ticker for a in lst.assets) == sorted(tickers)
    assert client.post("/api/temas/anadir", json={"tickers": tickers, "new_name": "Industriales prueba"}).status_code == 400
    assert client.post("/api/temas/anadir", json={"tickers": tickers}).status_code == 400


def test_mute_theme_and_company():
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset, DBCompanyList, DBPortfolioItem, DBWatchlistItem
    from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
    db = SessionLocal()
    held = {p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0}
    manual = {w.ticker for w in db.query(DBWatchlistItem).all()}
    in_lists = {a.ticker for lst in db.query(DBCompanyList).all() for a in lst.assets}
    pool = [a for a in db.query(DBAsset).all() if a.ticker not in held | manual | in_lists]
    quiet = next(a for a in pool if not a.is_favorite)
    fav = next(a for a in pool if a.is_favorite)
    repo = SqlAlchemyAssetRepository(db)
    lst = repo.create_company_list("Cartera ajena prueba", None)
    repo.add_ticker_to_list(lst.id, quiet.ticker)
    repo.add_ticker_to_list(lst.id, fav.ticker)
    assert quiet.ticker not in ts.muted_tickers(db)
    ts.set_theme_muted(db, lst.id, True)
    muted = ts.muted_tickers(db)
    assert quiet.ticker in muted and fav.ticker not in muted            # la Tier 1 sigue avisando
    ts.set_theme_watch(db, lst.id, True)                                # aunque se siga en la watchlist,
    cands = ts.watch_candidates(db)                                     # la lista silenciada no la mete
    assert quiet.ticker not in cands and fav.ticker in cands
    ts.set_target(db, lst.id, 2, None)                                  # poner objetivo no quita el silencio
    assert quiet.ticker in ts.muted_tickers(db)
    ts.set_theme_muted(db, lst.id, False)
    ts.set_muted(db, fav.ticker, True)                                  # una empresa silenciada, aunque sea Tier 1
    muted = ts.muted_tickers(db)
    assert fav.ticker in muted and quiet.ticker not in muted
    ts.set_muted(db, fav.ticker, False)


def test_followed_outside_watchlist():
    """Tier 2 y 3 fuera de la watchlist: entran en caídas y movimientos raros; una silenciada, no."""
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset, DBCompanyList, DBPortfolioItem, DBWatchlistItem
    db = SessionLocal()
    held = {p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0}
    manual = {w.ticker for w in db.query(DBWatchlistItem).all()}
    in_lists = {a.ticker for lst in db.query(DBCompanyList).all() for a in lst.assets}
    a = next(x for x in db.query(DBAsset).all() if x.ticker not in held | manual | in_lists and not x.is_favorite)
    ts.set_tier(db, a.ticker, "2")
    assert ts.followed_outside_watchlist(db).get(a.ticker) == "2"
    ts.set_tier(db, a.ticker, "seg")
    assert ts.followed_outside_watchlist(db).get(a.ticker) == "seg"
    ts.set_muted(db, a.ticker, True)
    assert a.ticker not in ts.followed_outside_watchlist(db)
    ts.set_muted(db, a.ticker, False)
    ts.set_tier(db, a.ticker, "1")                                      # Tier 1: ya está en la watchlist
    assert a.ticker not in ts.followed_outside_watchlist(db)


def test_list_enters_watchlist_only_when_followed():
    """Una lista nueva no mete nada en la watchlist hasta que se sigue («Seguir en la watchlist»)."""
    from fastapi.testclient import TestClient
    from main import app
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBAsset, DBCompanyList, DBPortfolioItem, DBWatchlistItem
    from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
    db = SessionLocal()
    held = {p.ticker for p in db.query(DBPortfolioItem).all() if (p.shares or 0) > 0}
    manual = {w.ticker for w in db.query(DBWatchlistItem).all()}
    in_lists = {a.ticker for lst in db.query(DBCompanyList).all() for a in lst.assets}
    a = next(x for x in db.query(DBAsset).all() if x.ticker not in held | manual | in_lists and not x.is_favorite)
    repo = SqlAlchemyAssetRepository(db)
    lst = repo.create_company_list("Lista sin seguir prueba", None)
    repo.add_ticker_to_list(lst.id, a.ticker)
    assert a.ticker not in ts.watch_candidates(db)                      # de serie, fuera
    assert a.ticker not in ts.muted_tickers(db)                         # pero no silenciada: no seguir ≠ silenciar
    r = TestClient(app).put(f"/api/lists/{lst.id}/watchlist", json={"watch": True})
    assert r.status_code == 200 and r.json()["watch"] is True
    db.expire_all()
    assert ts.watch_candidates(db)[a.ticker]["themes"] == ["Lista sin seguir prueba"]
