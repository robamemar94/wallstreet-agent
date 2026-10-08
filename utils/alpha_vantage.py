"""Histórico financiero anual (ingresos, beneficio, BPA, FCF) de Alpha Vantage: plan B cuando la empresa no tiene
histórico cargado a mano ni por la IA. Solo funciona con ALPHAVANTAGE_API_KEY en el .env (sin clave no se pide nada)
y para empresas de EE. UU. (ticker sin sufijo de mercado). El plan gratuito da 25 llamadas al día y cada empresa
gasta 3, así que todo se guarda en data/alphavantage/: los datos hasta que cambia el año y los «sin datos» un día."""
import datetime
import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
CACHE_DIR = os.path.join("data", "alphavantage")
MISS_TTL = datetime.timedelta(days=1)


def api_key() -> Optional[str]:
    return os.getenv("ALPHAVANTAGE_API_KEY") or None


def supports(ticker: str) -> bool:
    return bool(api_key()) and bool(ticker) and "." not in ticker and not ticker.startswith("^")


def _cache_path(ticker: str) -> str:
    return os.path.join(CACHE_DIR, f"{ticker.upper()}.json")


def _read_cache(ticker: str):
    """(hay caché válida, datos o None)."""
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return False, None
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        saved = datetime.datetime.fromisoformat(cached["saved_at"])
    except Exception:
        return False, None
    now = datetime.datetime.now()
    if cached.get("data") is None:
        return now - saved < MISS_TTL, None
    return saved.year == now.year, cached["data"]


def _write_cache(ticker: str, data) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump({"saved_at": datetime.datetime.now().isoformat(timespec="seconds"), "data": data}, f)
    except Exception as e:
        logger.warning(f"AlphaVantage: no se pudo guardar la caché de {ticker}: {e}")


class RateLimited(Exception):
    pass


def _fetch(function: str, ticker: str) -> Optional[dict]:
    r = requests.get(BASE_URL, params={"function": function, "symbol": ticker.upper(), "apikey": api_key()}, timeout=10)
    data = r.json()
    if "Note" in data or "Information" in data:   # límite diario o por minuto
        raise RateLimited(data.get("Note") or data.get("Information"))
    if "Error Message" in data or not data:
        return None
    return data


def _num(v) -> float:
    try:
        return float(v) if v not in (None, "", "None") else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_financials(ticker: str, max_years: int = 15) -> Optional[dict]:
    """{years, revenue, net_income, eps, fcf} en orden cronológico, o None si no hay (o no se puede pedir)."""
    if not supports(ticker):
        return None
    valid, data = _read_cache(ticker)
    if not valid:
        try:
            income, cash, earnings = (_fetch(f, ticker) for f in ("INCOME_STATEMENT", "CASH_FLOW", "EARNINGS"))
        except RateLimited as e:
            logger.warning(f"AlphaVantage: límite de llamadas alcanzado ({e}); se reintentará más tarde")
            return None   # sin caché: no es que no haya datos
        except Exception as e:
            logger.warning(f"AlphaVantage: error pidiendo {ticker}: {e}")
            return None
        data = _build(income, cash, earnings)
        _write_cache(ticker, data)
    return {k: v[-max_years:] for k, v in data.items()} if data else None


def _build(income, cash, earnings) -> Optional[dict]:
    reports = (income or {}).get("annualReports") or []
    if not reports:
        return None
    by_year = lambda rows: {r.get("fiscalDateEnding", "")[:4]: r for r in rows or []}
    cash_y, earn_y = by_year((cash or {}).get("annualReports")), by_year((earnings or {}).get("annualEarnings"))
    out = {"years": [], "revenue": [], "net_income": [], "eps": [], "fcf": []}
    for inc in reversed(reports):   # vienen de más reciente a más antiguo
        year = inc.get("fiscalDateEnding", "")[:4]
        if not year:
            continue
        cf = cash_y.get(year, {})
        out["years"].append(year)
        out["revenue"].append(_num(inc.get("totalRevenue")))
        out["net_income"].append(_num(inc.get("netIncome")))
        out["eps"].append(_num(earn_y.get(year, {}).get("reportedEPS")))
        out["fcf"].append(_num(cf.get("operatingCashflow")) - _num(cf.get("capitalExpenditures")))
    return out
