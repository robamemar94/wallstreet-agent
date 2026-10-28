from app.application.services import financial_trends_service as ft


def fin(debt, cash, ebitda, years=("2022", "2023")):
    return {"years": list(years), "revenue": [100.0] * len(years),
            "income_statement": {"EBITDA": ebitda},
            "balance_sheet": {"Total Debt": debt, "Cash and Cash Equivalents": cash}}


def test_ratio_del_ultimo_ejercicio():
    assert ft.net_debt_to_ebitda(fin([50.0, 60.0], [10.0, 20.0], [20.0, 20.0])) == 2.0


def test_mas_caja_que_deuda_sale_negativo():
    assert ft.net_debt_to_ebitda(fin([10.0, 10.0], [30.0, 30.0], [20.0, 20.0])) == -1.0


def test_ebitda_no_positivo_o_sin_dato_no_da_ratio():
    assert ft.net_debt_to_ebitda(fin([50.0, 60.0], [10.0, 20.0], [20.0, -5.0])) is None
    assert ft.net_debt_to_ebitda({}) is None
