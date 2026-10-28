import datetime

from app.interfaces.views.portfolio_views import held_label, holding_since
from utils.financial_math import xirr


def _e(date, kind, shares):
    return {"date": date, "type": kind, "shares": shares}


def test_holding_since_restarts_after_closing():
    ledger = [_e("2022-03-16", "BUY", 10), _e("2023-05-01", "SELL", 10), _e("2026-03-15", "BUY", 7), _e("2026-04-30", "BUY", 1)]
    assert holding_since(ledger) == datetime.date(2026, 3, 15)


def test_holding_since_counts_splits():
    # 10 acciones, split 4:1 (40) y venta de 20: sigue abierta, no empieza de nuevo
    ledger = [_e("2024-01-10", "BUY", 10), _e("2024-06-10", "SPLIT", 4), _e("2025-01-10", "SELL", 20), _e("2025-02-10", "BUY", 5)]
    assert holding_since(ledger) == datetime.date(2024, 1, 10)


def test_held_label():
    today = datetime.date(2026, 10, 25)
    assert held_label(datetime.date(2022, 8, 23), today) == "4a 2m"
    assert held_label(datetime.date(2026, 3, 15), today) == "7m"
    assert held_label(datetime.date(2026, 10, 20), today) == "5 d"


def test_xirr_weights_each_contribution_by_its_date():
    assert abs(xirr([("2025-01-01", -100), ("2026-01-01", 110)]) - 10) < 0.1
    # la segunda aportación lleva medio año: la TIR sale mayor que el 10 % simple sobre 200
    assert xirr([("2025-01-01", -100), ("2025-07-01", -100), ("2026-01-01", 220)]) > 10
    assert xirr([("2025-01-01", -100)]) is None
