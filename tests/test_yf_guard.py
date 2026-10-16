import threading
import time

import numpy as np
import pandas as pd
import yfinance as yf

import utils.yf_guard  # noqa: F401
from app.application.services.price_monitor_service import price_stats


def test_yf_download_runs_one_at_a_time(monkeypatch):
    """Dos yf.download a la vez se mezclan en yfinance: el guard las pone en fila."""
    active, overlap = [0], [False]

    def fake(*a, **k):
        active[0] += 1
        overlap[0] |= active[0] > 1
        time.sleep(0.05)
        active[0] -= 1
        return pd.DataFrame()
    monkeypatch.setattr(utils.yf_guard, "_lock", threading.RLock())
    monkeypatch.setattr(yf, "download", fake)
    utils.yf_guard.install()
    threads = [threading.Thread(target=yf.download, args=(["X"],)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not overlap[0]


def _series(days):
    idx = pd.bdate_range(end="2026-10-15", periods=days)
    return pd.Series(np.linspace(100, 150, days), index=idx)   # sube sin parar: el último es el máximo


def test_no_52w_extreme_with_short_history():
    assert price_stats(_series(300))["new_high"] is True          # más de un año: sí
    short = price_stats(_series(60))                               # ~3 meses (o un histórico roto): no
    assert short["new_high"] is False and short["new_low"] is False
