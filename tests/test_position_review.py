"""«Mi posición» (ficha) y los minigráficos de Finanzas: cuentas sin internet."""
import pandas as pd
import pytest

from app.application.services import financial_trends_service as ft
from app.application.services import position_review_service as prs


def _df():
    idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
    # la acción dobla; el índice sube un 10 % y luego otro 10 %; todo en EUR para no depender del cambio
    return pd.DataFrame({"ACME": [10.0, 15.0, 20.0], "SPY": [100.0, 110.0, 121.0], "USDEUR=X": [1.0, 1.0, 1.0]}, index=idx)


def _tx(date, kind, shares, price):
    return {"ticker": "ACME", "date": date, "type": kind, "shares": shares, "price": price, "currency": "USD", "fx": 1.0}


def test_versus_index_buy_and_hold():
    v = prs.versus_index([_tx("2025-01-02", "BUY", 10, 10)], [{"date": "2025-06-01", "eur": 5.0}], _df(), "USD")
    assert v["invested"] == 100 and v["value"] == pytest.approx(200)
    assert v["result"] == pytest.approx(105)            # +100 de precio y 5 de dividendos
    assert v["index_pct"] == pytest.approx(21)          # 100 → 121
    assert v["excess"] == pytest.approx(105 - 21)


def test_versus_index_partial_sale_sells_same_part_of_index():
    txs = [_tx("2025-01-02", "BUY", 10, 10), _tx("2025-06-02", "SELL", 5, 15)]
    v = prs.versus_index(txs, [], _df(), "USD")
    assert v["sold"] == pytest.approx(75) and v["shares"] == pytest.approx(5)
    assert v["result"] == pytest.approx(5 * 20 + 75 - 100)
    # índice: 1 unidad; vende media a 110 (55) y la otra media vale 60,5
    assert v["index_result"] == pytest.approx(55 + 60.5 - 100)


def test_markers_group_by_day_and_adjust_splits():
    txs = [_tx("2025-01-02", "BUY", 1, 100), _tx("2025-01-02", "BUY", 1, 110), _tx("2025-06-02", "SELL", 1, 120)]
    m = prs.markers(txs, [("2025-03-01", 2.0)])
    assert m[0] == {"date": "2025-01-02", "type": "BUY", "shares": 2, "price": pytest.approx(52.5)}
    assert m[1]["price"] == pytest.approx(120)


def test_trends_tones_and_missing_rows():
    fin = {"years": ["2022", "2023", "2024", "LTM"], "revenue": [100, 110, 121, 125], "eps": [-1, 0.5, 1, 1.1],
           "fcf": [10, 9, 8, 8], "net_income": [10, 12, 15, 15],
           "income_statement": {"Operating Margin": [0.10, 0.12, 0.15, 0.15], "Diluted Weighted Average Shares Outstanding": [100, 98, 96, 95]},
           "balance_sheet": {"Long-Term Debt": [None, 50, 40, 40], "Total Cash and Cash Equivalents": [30, 30, 30, 30]}}
    tiles = {t["key"]: t for t in ft.build(fin)}
    assert tiles["revenue"]["tone"] == "good" and tiles["revenue"]["trend"]["pct"] == pytest.approx(10)
    assert tiles["eps"]["trend"]["kind"] == "dir" and tiles["eps"]["tone"] == "good"   # de pérdidas a beneficios
    assert tiles["fcf"]["tone"] == "bad"
    assert tiles["margins"]["trend"]["pp"] == pytest.approx(5)
    assert tiles["shares"]["tone"] == "good"                                              # recompra
    assert tiles["debt"]["series"][0]["values"][0] == 0                                   # sin deuda ese año
    assert ft.build({}) == []


def test_financial_labels_spanish_units_and_order():
    from app.application.services import financial_labels as fl
    years = ["LTM", "2024", "2023"]
    rows = {r["label"]: r for r in fl.rows({"Total Revenues": [300e6, 250e6, 200e6], "Gross Profit Margin": [0.5, 0.48, 0.4],
                                            "Diluted EPS": [1.5, 1.2, 1.0], "Unknown Row": [None, None, None]}, years)}
    assert fl.ordered_years(years) == ["2023", "2024", "LTM"]
    assert rows["Ventas"]["values"] == [200, 250, 300] and rows["Ventas"]["total"] and rows["Ventas"]["key"]
    assert rows["Margen bruto"]["values"] == [40, 48, 50] and not rows["Margen bruto"]["total"]
    assert rows["BPA diluido"]["kind"] == "per_share" and rows["BPA diluido"]["values"][-1] == 1.5
    assert "Unknown Row" not in rows                                                     # sin ningún dato: fuera
    assert fl.label("Total Revenues %Chg") == "Ventas, variación" and fl.kind("Total Revenues %Chg") == "pct"


def test_key_figures_tones_and_history():
    from app.application.services import key_figures_service as kf
    fin = {"years": ["2021", "2022", "2023", "2024"], "eps": [1, 2, 2, 4], "fcf": [10, 20, 20, 40], "revenue": [100, 110, 121, 133],
           "net_income": [10, 20, 20, 40], "income_statement": {"Diluted Weighted Average Shares Outstanding": [10, 10, 10, 10]},
           "balance_sheet": {"Total Equity": [100, 100, 100, 100]}}
    dates = ["2021-12-31", "2022-12-30", "2023-12-29", "2024-12-31"]
    figs = {k["key"]: k for k in kf.build({"forwardPE": 30, "trailingPE": 22, "operatingMargins": 0.25, "totalDebt": 50, "totalCash": 80, "ebitda": 10},
                                         {"fair_pe_data": {"fair_forward_pe": 20}}, {"returns": {"ROE (TTM)": 0.4}}, fin, dates, [20, 40, 40, 80])}
    assert figs["pe"]["tone"] == "bad"                                                    # 30x frente a un PER justo de 20x
    assert figs["pe_ttm"]["spark"] == [20, 20, 20, 20] and figs["pe_ttm"]["tone"] == "mid"  # 22x frente a una media de 20x
    assert figs["roe"]["tone"] == "good" and figs["roe"]["spark"][-1] == 40
    assert figs["margin"]["tone"] == "good" and figs["debt"]["tone"] == "good"          # más caja que deuda
    assert figs["debt"]["context"] == "más caja que deuda" and figs["div"]["tone"] == "none"


def test_money_series_matches_result():
    # compra 10 a 10, dividendo de 5 € a mitad: el último punto coincide con el resultado de versus_index
    df = _df()
    txs = [_tx("2025-01-02", "BUY", 10, 10)]
    divs = [{"date": "2025-06-02", "eur": 5.0}]
    m = prs.money_series(txs, divs, df["ACME"], df, "USD")
    v = prs.versus_index(txs, divs, df, "USD")
    assert m["dates"] == ["2025-01-02", "2025-06-02", "2026-01-02"]
    assert m["invested"] == [100.0, 100.0, 100.0]
    assert m["total"] == [100.0, 155.0, 205.0]
    assert m["total"][-1] - m["invested"][-1] == pytest.approx(v["result"])


def test_money_series_sale_keeps_proceeds():
    df = _df()
    txs = [_tx("2025-01-02", "BUY", 10, 10), _tx("2025-06-02", "SELL", 10, 15)]
    m = prs.money_series(txs, [], df["ACME"], df, "USD")
    assert m["total"] == [100.0, 150.0, 150.0]


def test_position_stats_best_worst_and_12m():
    def op(date, since, h12=None):
        return {"date": date, "type": "BUY", "review": {"since": since, "horizons": {"12m": h12} if h12 is not None else {}}}
    ops = [op("2023-01-02", 120.0, 5.0), op("2024-01-02", -10.0, -3.0), op("2026-01-02", 4.0)]
    txs = [_tx("2023-01-02", "BUY", 1, 10)]
    st = prs.position_stats(ops, txs, None, today=pd.Timestamp("2026-03-03"))
    assert (st["years"], st["months"]) == (3, 2)
    assert st["best"]["date"] == "2023-01-02" and st["worst"]["since"] == -10.0
    assert (st["n12"], st["beat12"]) == (2, 1)
