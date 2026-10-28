"""El histórico común del monitor siempre tiene un cierre por día, aunque el respaldo traiga barras intradía (21/10/2026)."""
import numpy as np
import pandas as pd

from app.application.services import price_monitor_service as pm
from app.application.services import session_fill


def test_intraday_backup_does_not_flatten_the_rest(tmp_path, monkeypatch):
    days = pd.bdate_range("2025-04-01", "2026-10-21")
    rng = np.random.default_rng(1)
    good = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.02, len(days))), index=days)
    # respaldo de B: diario + dos días de barras de 5 minutos (lo que dejó la descarga cruzada del 16/10)
    intraday = pd.date_range("2026-10-14 15:30", "2026-10-15 22:00", freq="5min")
    b = pd.concat([pd.Series(50.0, index=days), pd.Series(50.0 + np.arange(len(intraday)) * 0.001, index=intraday)])
    monkeypatch.setattr(pm, "BACKUP_FILE", str(tmp_path / "backup.pkl"))
    pd.to_pickle({"B": b}, pm.BACKUP_FILE)
    pm._cache.clear()
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame({"A": good}))   # B no llega en el bloque
    monkeypatch.setattr(pm, "_single_history", lambda sym: None)
    monkeypatch.setattr(session_fill, "fill_today", lambda df, symbols=None: df)

    df = pm.download_history(["A", "B"])
    assert df.index.is_unique and (df.index == df.index.normalize()).all()
    st = pm.price_stats(df["A"])
    assert 0.015 < st["sigma_d"] < 0.025          # la σ de A no se aplasta con filas copiadas
    assert (df.index[-1] - df["A"].iloc[-252:].index[0]).days > 300   # sus 52 semanas son un año, no dos días
    assert len(pm._load_backup()["B"]) == len(days)   # el respaldo sale limpio: un precio por día
