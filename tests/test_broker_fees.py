from app.application.services import broker_fees as bf


def test_ibkr_tiered_usd_minimum_and_per_share():
    assert round(bf.estimate_fee("IBKR", "USD", 1, 650, 0.86), 2) == 0.35          # mínimo + tasas de 1 acción
    assert round(bf.estimate_fee("IBKR", "USD", 200, 30, 0.86), 2) == 0.80         # 200 × (0,0035 + 0,0005)


def test_ibkr_capped_at_one_percent():
    assert round(bf.estimate_fee("IBKR", "USD", 10, 1.0, 0.86), 3) == 0.105        # 1 % de 10 $ + tasas


def test_ibkr_eur_and_degiro():
    assert abs(bf.estimate_fee("IBKR", "EUR", 2, 1365, 1.0) - 1.365) < 1e-9     # 0,05 % (mín. 1,25 €)
    assert round(bf.estimate_fee("DEGIRO", "USD", 10, 100, 0.86), 2) == round((1.0 + 0.0025 * 860) / 0.86, 2)


def test_saved_settings_win_and_auto_broker():
    assert bf.estimate_fee("IBKR", "USD", 1, 100, 0.86, {"ibkr_fee_usd_min": 1.0, "ibkr_fee_usd_extra_per_share": 0}) == 1.0
    assert bf.resolve_broker("AUTO", "2025-12-31") == "DEGIRO" and bf.resolve_broker("AUTO", "2026-01-02") == "IBKR"
