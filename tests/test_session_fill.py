import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.application.services.session_fill import missing_today, opened_today

MAD = ZoneInfo("Europe/Madrid")


def at(h, m, d=7):
    return datetime.datetime(2026, 10, d, h, m, tzinfo=MAD)   # miércoles


def test_tokyo_session_counts_for_our_day():
    assert not opened_today("6861.T", at(1, 30))      # 8:30 en Tokio: aún no ha abierto
    assert opened_today("6861.T", at(2, 30))          # 9:30 en Tokio
    assert opened_today("6861.T", at(18, 30))         # ya cerró, pero su sesión de hoy existió
    assert opened_today("6861.T", at(23, 30))         # en España sigue siendo miércoles: su sesión de hoy ya fue


def test_us_and_europe():
    assert not opened_today("MSFT", at(10, 0)) and opened_today("MSFT", at(16, 0))
    assert not opened_today("SAF.PA", at(9, 5)) and opened_today("SAF.PA", at(9, 30))


def test_not_for_indices_fx_crypto_or_weekend():
    assert not opened_today("^GSPC", at(18, 0)) and not opened_today("USDEUR=X", at(18, 0)) and not opened_today("BTC-USD", at(18, 0))
    assert not opened_today("6861.T", at(12, 0, d=10))   # sábado


def test_missing_today_only_where_session_happened():
    idx = pd.to_datetime(["2026-10-06", "2026-10-07"])
    closes = pd.DataFrame({"6861.T": [81490, None], "MSFT": [529.3, None], "SAF.PA": [329.4, 319.4]}, index=idx)
    # 18:30 en España: Tokio ya cotizó hoy (falta), MSFT también ha abierto (falta), SAF.PA ya tiene el de hoy
    assert missing_today(closes, closes.columns, at(18, 30)) == ["6861.T", "MSFT"]
    # 10:00: solo Tokio; EE. UU. aún no ha abierto, así que su hueco es normal
    assert missing_today(closes, closes.columns, at(10, 0)) == ["6861.T"]
