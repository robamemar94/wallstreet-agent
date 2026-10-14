"""Resumen de la Cartera: las posiciones que caen mucho desde su máximo de 52 semanas."""
import pandas as pd
import pytest

from app.interfaces.views.portfolio_views import falls_from_high


def test_caidas_desde_maximo():
    idx = pd.bdate_range("2025-10-01", "2026-10-13")
    df = pd.DataFrame({"AAA": 100.0, "BBB": 100.0}, index=idx)
    df.loc["2026-09-01":, "AAA"] = 60.0          # −40 %: sale
    df.loc["2026-09-01":, "BBB"] = 80.0          # −20 %: no llega al 30 %
    out = falls_from_high(df, ["AAA", "BBB", "CCC"])
    assert [d["ticker"] for d in out] == ["AAA"]
    assert out[0]["pct"] == pytest.approx(-40.0)
