from utils import alpha_vantage as av

INCOME = {"annualReports": [{"fiscalDateEnding": "2024-12-31", "totalRevenue": "200", "netIncome": "20"},
                            {"fiscalDateEnding": "2023-12-31", "totalRevenue": "100", "netIncome": "None"}]}
CASH = {"annualReports": [{"fiscalDateEnding": "2024-12-31", "operatingCashflow": "50", "capitalExpenditures": "10"}]}
EARN = {"annualEarnings": [{"fiscalDateEnding": "2024-12-31", "reportedEPS": "1.5"}]}


def _fake(calls, payloads):
    def fetch(function, ticker):
        calls.append(function)
        return payloads[function]
    return fetch


def test_sin_clave_o_fuera_de_eeuu_no_pide_nada(monkeypatch):
    calls = []
    monkeypatch.setattr(av, "_fetch", _fake(calls, {}))
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    assert av.get_financials("MSFT") is None
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "k")
    assert av.get_financials("EVO.ST") is None
    assert calls == []


def test_construye_el_historico_y_lo_guarda(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "k")
    monkeypatch.setattr(av, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(av, "_fetch", _fake(calls, {"INCOME_STATEMENT": INCOME, "CASH_FLOW": CASH, "EARNINGS": EARN}))
    data = av.get_financials("ROL")
    assert data == {"years": ["2023", "2024"], "revenue": [100.0, 200.0], "net_income": [0.0, 20.0],
                    "eps": [0.0, 1.5], "fcf": [0.0, 40.0]}
    assert av.get_financials("ROL") == data and len(calls) == 3   # la segunda vez, de la caché


def test_sin_datos_se_recuerda_y_el_limite_no(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "k")
    monkeypatch.setattr(av, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(av, "_fetch", _fake(calls, {"INCOME_STATEMENT": None, "CASH_FLOW": None, "EARNINGS": None}))
    assert av.get_financials("ZZZ") is None and av.get_financials("ZZZ") is None
    assert len(calls) == 3   # el «sin datos» se guarda un día

    def limited(function, ticker):
        calls.append(function)
        raise av.RateLimited("25 calls/day")
    monkeypatch.setattr(av, "_fetch", limited)
    assert av.get_financials("AAA") is None and av.get_financials("AAA") is None
    assert len(calls) == 5   # con el límite no se guarda nada: se reintenta
