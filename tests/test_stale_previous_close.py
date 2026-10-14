"""Variación del día: si Yahoo rellena la sesión anterior (volumen 0, cierre viejo), el cierre anterior se pide aparte."""
import datetime

import pandas as pd
import pytest

from app.interfaces.views import market_views as mv


def _download(*args, **kwargs):
    idx = pd.to_datetime(["2026-10-12", "2026-10-13", "2026-10-14"])
    cols = pd.MultiIndex.from_product([["Close", "Volume"], ["DNP.WA", "AAA", "^GSPC"]])
    rows = [[33.66, 100.0, 5000.0, 0, 10, 0], [33.66, 101.0, 5010.0, 0, 12, 0], [36.79, 102.0, 5020.0, 652910, 11, 0]]
    return pd.DataFrame(rows, index=idx, columns=cols)


def test_sesion_rellenada_usa_el_cierre_anterior_real(monkeypatch):
    monkeypatch.setattr(mv.yf, "download", _download)
    monkeypatch.setattr(mv, "previous_close_for", lambda ts: {t: 36.51 for t in ts})
    monkeypatch.setattr(mv.market_cache, "save_snapshot", lambda *_: None)
    import app.application.services.session_fill as sf
    monkeypatch.setattr(sf, "fill_today", lambda c: c)
    mv.refresh_index_prices(["DNP.WA", "AAA", "^GSPC"], [])
    d = mv.INDEX_CACHE["data"]
    assert d["DNP.WA"]["prev"] == pytest.approx(36.51)      # no 33,66 (de hace una semana)
    assert d["AAA"]["prev"] == pytest.approx(101.0)         # con volumen normal: el de la descarga
    assert d["^GSPC"]["prev"] == pytest.approx(5010.0)      # los índices no traen volumen: no se tocan
    assert d["DNP.WA"]["last_date"] == datetime.date(2026, 10, 14)


def test_simbolos():
    assert mv.get_currency_symbol("PLN", "DNP.WA") == "zł"
    assert mv.get_currency_symbol("XYZ", "") == "XYZ"
    assert mv.get_currency_symbol(None, "SAP.DE") == "€"
