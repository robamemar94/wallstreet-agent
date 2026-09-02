"""
Performance service — Método del Valor Liquidativo (NAV / Unit Value).

Cuando hay una compra (BUY), se emiten nuevas "participaciones" al valor
liquidativo actual. El VL solo cambia por variaciones de precio, nunca por
entradas/salidas de capital. Esto lo hace comparable directamente con benchmarks
(TWR — Time-Weighted Return).
"""
import json
import os
import time
from collections import defaultdict
from typing import Dict, Any, List

import numpy as np
import pandas as pd
import yfinance as yf


CACHE_FILE      = "data/performance_cache.json"
CACHE_TTL       = 3600          # 1 hora
BENCHMARKS      = [("URTH", "MSCI World"), ("SPY", "S&P 500")]
INITIAL_NAV     = 10.0          # valor liquidativo inicial


class PerformanceService:
    def __init__(self, portfolio_service):
        self.portfolio_service = portfolio_service

    # ─────────────────────────────────────────────────────────────────────────
    # Público
    # ─────────────────────────────────────────────────────────────────────────

    def get_performance_data(self, start_date_override: str = None) -> Dict[str, Any]:
        cached = self._load_cache(start_date_override)
        if cached:
            return cached

        result = self._compute(start_date_override)
        self._save_cache(result, start_date_override)
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Privado
    # ─────────────────────────────────────────────────────────────────────────

    def _empty(self):
        return {
            "dates": [], "portfolio_uv": [],
            "benchmarks": {name: [] for _, name in BENCHMARKS},
            "max_drawdown": 0.0,
            "annual_returns": [],
            "total_return": 0.0,
            "annualized_return": 0.0,
        }

    def _load_cache(self, start_date_override: str = None):
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE) as f:
                    c = json.load(f)
                if (c.get("start_date_override") == start_date_override
                        and time.time() - c.get("timestamp", 0) < CACHE_TTL):
                    return c["data"]
        except Exception:
            pass
        return None

    def _save_cache(self, data, start_date_override: str = None):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({
                    "timestamp": time.time(),
                    "start_date_override": start_date_override,
                    "data": data,
                }, f)
        except Exception:
            pass

    def _compute(self, start_date_override: str = None) -> Dict[str, Any]:
        csv_txs    = self.portfolio_service.load_csv_transactions()
        manual_txs = self.portfolio_service.repository.load_manual_transactions()
        all_txs    = [tx for tx in csv_txs + manual_txs
                      if tx.type.value in ("BUY", "SELL")]
        if not all_txs:
            return self._empty()

        all_txs.sort(key=lambda x: x.date)
        first_tx_date = pd.to_datetime(all_txs[0].date)

        # Fecha de inicio personalizada: permite ignorar operaciones iniciales
        # (p.ej. compras "de prueba" de cuando no se tomaba en serio la cartera)
        # tratando la posición que ya existía en esa fecha como el punto de partida
        # del cálculo (nueva "inception" al valor liquidativo inicial).
        cutoff = None
        if start_date_override:
            try:
                parsed = pd.to_datetime(start_date_override).normalize()
                if parsed > first_tx_date:
                    cutoff = parsed
            except Exception:
                cutoff = None

        if cutoff is not None:
            pre_txs    = [tx for tx in all_txs if pd.to_datetime(tx.date) < cutoff]
            post_txs   = [tx for tx in all_txs if pd.to_datetime(tx.date) >= cutoff]
            start_date = cutoff.strftime("%Y-%m-%d")
        else:
            pre_txs    = []
            post_txs   = all_txs
            start_date = first_tx_date.strftime("%Y-%m-%d")

        if not post_txs:
            return self._empty()

        tickers    = list({tx.ticker.upper() for tx in all_txs})
        currencies = list({tx.currency.upper() for tx in all_txs
                           if tx.currency.upper() != "EUR"})
        if "USD" not in currencies:
            currencies.append("USD")

        bench_symbols = [s for s, _ in BENCHMARKS]
        fx_list       = [f"{c}EUR=X" for c in currencies]
        fetch_all     = tickers + bench_symbols + fx_list

        try:
            raw  = yf.download(fetch_all, start=start_date,
                               auto_adjust=True, progress=False)
            df   = raw["Close"] if "Close" in raw.columns else raw
            if df.empty:
                return self._empty()
            df = df.ffill().bfill()
            if isinstance(df, pd.Series):
                df = df.to_frame()
        except Exception:
            return self._empty()

        col_map  = {c.upper(): c for c in df.columns}
        tick_ccy = {tx.ticker.upper(): tx.currency.upper() for tx in all_txs}

        # ── Agrupar transacciones por fecha de trading (solo posteriores al corte) ──
        tx_by_date: Dict[pd.Timestamp, list] = defaultdict(list)
        for tx in post_txs:
            d = self._align_date(pd.to_datetime(tx.date).normalize(), df.index)
            tx_by_date[d].append(tx)

        # ── Serie NAV del portfolio ────────────────────────────────────────
        holdings: Dict[str, float] = defaultdict(float)
        nav      = INITIAL_NAV
        units    = 0.0

        # Si hay corte personalizado, la posición acumulada antes del corte (pre_txs)
        # se convierte en el punto de partida ("nueva inception" al VL inicial),
        # valorada a precio de mercado del primer día de la serie.
        if pre_txs:
            for tx in pre_txs:
                t = tx.ticker.upper()
                if tx.type.value == "BUY":
                    holdings[t] += tx.shares
                else:
                    holdings[t] -= tx.shares
            holdings = defaultdict(float, {t: q for t, q in holdings.items() if q > 0.0001})
            if holdings and len(df.index) > 0:
                seed_val = self._portfolio_value(holdings, df.index[0], df, col_map, tick_ccy)
                if seed_val > 0:
                    units = seed_val / INITIAL_NAV

        nav_series:   List[float] = []
        value_series: List[float] = []   # valor real en EUR
        date_series:  List[str]   = []

        for d in df.index:
            # 1. Calcular valor de cartera con precios del día (holdings actuales)
            port_val = self._portfolio_value(holdings, d, df, col_map, tick_ccy)

            # 2. Actualizar NAV por variación de precio (antes de procesar operaciones)
            if units > 0 and port_val > 0:
                nav = port_val / units

            # 3. Procesar operaciones del día al NAV calculado
            for tx in tx_by_date.get(d, []):
                t    = tx.ticker.upper()
                ccy  = tx.currency.upper()
                fx_c = col_map.get(f"{ccy}EUR=X")
                t_c  = col_map.get(t)
                price_eur = (df.at[d, t_c] * (df.at[d, fx_c] if fx_c else 1.0)
                             if t_c and t_c in df.columns
                             else tx.price * (df.at[d, fx_c] if fx_c else 1.0))
                cf_eur = tx.shares * price_eur

                if tx.type.value == "BUY":
                    holdings[t] += tx.shares
                    if units == 0:
                        units  = cf_eur / nav   # primera compra: inicializar
                    else:
                        units += cf_eur / nav   # emitir nuevas participaciones
                else:  # SELL
                    holdings[t] = max(0.0, holdings[t] - tx.shares)
                    if units > 0 and nav > 0:
                        units = max(0.0, units - cf_eur / nav)

            # 4. Registrar solo cuando hay cartera activa
            if units > 0:
                nav_series.append(round(nav, 4))
                value_series.append(round(units * nav, 2))  # valor real en EUR
                date_series.append(d.strftime("%Y-%m-%d"))

        if not nav_series:
            return self._empty()

        # ── Series de benchmarks ───────────────────────────────────────────
        first_date = pd.to_datetime(date_series[0])
        bench_series: Dict[str, List[float]] = {}
        for sym, name in BENCHMARKS:
            b_col  = col_map.get(sym)
            fx_col = col_map.get("USDEUR=X")
            series = []
            if b_col and b_col in df.columns:
                start_idx = df.index.searchsorted(first_date)
                b_start   = (df.iloc[start_idx][b_col] *
                             (df.iloc[start_idx][fx_col] if fx_col else 1.0))
                for d_str in date_series:
                    d    = pd.to_datetime(d_str)
                    idx  = df.index.searchsorted(d)
                    if idx >= len(df): idx = len(df) - 1
                    b_v  = (df.iloc[idx][b_col] *
                            (df.iloc[idx][fx_col] if fx_col else 1.0))
                    series.append(round(INITIAL_NAV * b_v / b_start, 4)
                                  if b_start > 0 else INITIAL_NAV)
            bench_series[name] = series

        # ── Métricas ───────────────────────────────────────────────────────
        nav_arr      = np.array(nav_series)
        max_drawdown = self._max_drawdown(nav_arr)
        total_ret    = (nav_arr[-1] / nav_arr[0] - 1) * 100
        years_total  = (pd.to_datetime(date_series[-1]) -
                        pd.to_datetime(date_series[0])).days / 365.25
        ann_ret      = ((nav_arr[-1] / nav_arr[0]) ** (1 / years_total) - 1) * 100 \
                       if years_total > 0.5 else total_ret

        # Calcular rendimiento anualizado del benchmark MSCI World para el Alpha de Jensen
        bench_ann_ret = 0.0
        msci_series = bench_series.get("MSCI World", [])
        if msci_series and len(msci_series) > 0 and msci_series[0] > 0:
            bench_total_ret = (msci_series[-1] / msci_series[0] - 1) * 100
            bench_ann_ret = ((msci_series[-1] / msci_series[0]) ** (1 / years_total) - 1) * 100 \
                            if years_total > 0.5 else bench_total_ret

        annual_returns = self._annual_returns(
            date_series, nav_arr, bench_series
        )

        return {
            "dates":              date_series,
            "portfolio_uv":       nav_series,
            "portfolio_values_eur": value_series,
            "benchmarks":         bench_series,
            "benchmark_uv":       bench_series.get("MSCI World", []),  # compatibilidad
            "max_drawdown":       round(max_drawdown, 2),
            "annual_returns":     annual_returns,
            "total_return":       round(total_ret, 2),
            "annualized_return":  round(ann_ret, 2),
            "benchmark_annualized_return": round(bench_ann_ret, 2),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _align_date(d: pd.Timestamp, index: pd.DatetimeIndex) -> pd.Timestamp:
        loc = index.searchsorted(d)
        if loc >= len(index):
            loc = len(index) - 1
        return index[loc]

    @staticmethod
    def _portfolio_value(holdings, d, df, col_map, tick_ccy) -> float:
        total = 0.0
        for t, qty in holdings.items():
            if qty <= 0:
                continue
            t_c  = col_map.get(t)
            ccy  = tick_ccy.get(t, "USD")
            fx_c = col_map.get(f"{ccy}EUR=X")
            if t_c and t_c in df.columns:
                total += qty * df.at[d, t_c] * (df.at[d, fx_c] if fx_c else 1.0)
        return total

    @staticmethod
    def _max_drawdown(nav: np.ndarray) -> float:
        if len(nav) < 2:
            return 0.0
        running_max = np.maximum.accumulate(nav)
        drawdown    = (nav - running_max) / running_max * 100
        return float(drawdown.min())

    @staticmethod
    def _annual_returns(dates, nav_arr, bench_series) -> list:
        df_nav = pd.Series(nav_arr, index=pd.to_datetime(dates))
        years  = sorted(df_nav.index.year.unique())
        rows   = []
        for yr in years:
            yr_data = df_nav[df_nav.index.year == yr]
            if len(yr_data) < 2:
                continue
            port_ret = round((yr_data.iloc[-1] / yr_data.iloc[0] - 1) * 100, 2)
            row = {"year": yr, "portfolio": port_ret}
            for name, series in bench_series.items():
                if not series:
                    continue
                df_b = pd.Series(series, index=pd.to_datetime(dates))
                b_yr = df_b[df_b.index.year == yr]
                if len(b_yr) >= 2:
                    row[name] = round((b_yr.iloc[-1] / b_yr.iloc[0] - 1) * 100, 2)
                else:
                    row[name] = None
            rows.append(row)
        return rows
