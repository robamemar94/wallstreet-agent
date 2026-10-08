import pandas as pd
from typing import List, Dict, Any
import yfinance as yf
from datetime import datetime
import json
import os

from app.domain.models import PortfolioItem, Transaction, TransactionType, LedgerEntry
from app.domain.repositories.portfolio_repository import PortfolioRepositoryInterface
from app.infrastructure.config import ISIN_TO_TICKER, CSV_FILE, DIV_CACHE_FILE, SPLIT_CACHE_FILE

class PortfolioService:
    def __init__(self, repository: PortfolioRepositoryInterface):
        self.repository = repository

    def add_manual_transaction(self, tx: Transaction):
        txs = self.repository.load_manual_transactions()
        txs.append(tx)
        self.repository.save_manual_transactions(txs)
        self.rebuild_portfolio(force_tickers={tx.ticker})   # sus splits y dividendos, recién pedidos

    def delete_manual_transaction(self, target_tx: Transaction):
        txs = self.repository.load_manual_transactions()
        filtered_txs = []
        found = False
        for tx in txs:
            if not found and tx.date == target_tx.date and tx.type == target_tx.type \
               and tx.ticker == target_tx.ticker and abs(tx.shares - target_tx.shares) < 0.001 \
               and abs(tx.price - target_tx.price) < 0.001:
                found = True
                continue
            filtered_txs.append(tx)
        
        if found:
            self.repository.save_manual_transactions(filtered_txs)
            self.rebuild_portfolio()

    def load_csv_transactions(self) -> List[Transaction]:
        transactions = []
        if not os.path.exists(CSV_FILE):
            return transactions
            
        df = pd.read_csv(CSV_FILE)
        df['Número'] = df['Número'].fillna(0)
        df['Precio'] = df['Precio'].astype(str).str.replace(',', '.').astype(float)
        
        # Combinar Fecha y Hora para un ordenamiento preciso
        if 'Hora' in df.columns:
            df['datetime'] = pd.to_datetime(df['Fecha'] + ' ' + df['Hora'], format='%d-%m-%Y %H:%M')
        else:
            df['datetime'] = pd.to_datetime(df['Fecha'], format='%d-%m-%Y')
        
        def to_float(val):
            if pd.isna(val): return 0.0
            try:
                return float(str(val).replace(',', '.'))
            except Exception:
                return 0.0

        for _, row in df.iterrows():
            isin = row['ISIN']
            ticker = ISIN_TO_TICKER.get(isin)
            if not ticker:
                name = row['Producto']
                if pd.notna(name) and 'ROCKET LAB' in str(name): ticker = 'RKLB'
                else: continue
            
            if pd.notna(row['Producto']) and 'RTS' in str(row['Producto']): continue

            shares_delta = float(row['Número'])
            if shares_delta == 0: continue
            
            price = float(row['Precio'])
            # Guardamos el string completo de datetime para el modelo Transaction si es necesario, 
            # pero el campo date del modelo es str. Usaremos ISO format.
            date_str = row['datetime'].strftime('%Y-%m-%d %H:%M:%S')
            currency = row['Unnamed: 8'] if pd.notna(row['Unnamed: 8']) else 'USD'

            # Calcular exchange rate y comisiones de forma exacta
            val_local = abs(to_float(row.get('Valor local', 0.0)))
            val_eur = abs(to_float(row.get('Valor EUR', 0.0)))
            tot_eur = abs(to_float(row.get('Total EUR', 0.0)))

            if currency.upper() == 'EUR':
                fx_rate = 1.0
                fee_eur = abs(tot_eur - val_local)
                fee_native = fee_eur
            else:
                fx_rate = val_eur / val_local if val_local > 0 else 1.0
                fee_eur = abs(tot_eur - val_eur)
                fee_native = fee_eur / fx_rate if fx_rate > 0 else fee_eur
            
            transactions.append(Transaction(
                date=date_str,
                type=TransactionType.BUY if shares_delta > 0 else TransactionType.SELL,
                ticker=ticker,
                shares=abs(shares_delta),
                price=price,
                currency=currency,
                total=val_local,
                fee=fee_native,
                fx_rate_at_purchase=fx_rate
            ))
        return transactions

    # Datos de Yahoo por empresa (splits, dividendos y fechas del próximo dividendo). Antes se pedían uno a uno y sin
    # caché en cada recálculo de la cartera: ~165 consultas seguidas, ~45 s por cada operación nueva. Ahora se piden en
    # paralelo y se guardan 12 h; la empresa de la operación que se acaba de apuntar se vuelve a pedir (force).
    YAHOO_TTL_H = 12

    @staticmethod
    def _per_ticker(kind: str, tickers: set, fetch, force: set = frozenset()) -> Dict[str, Any]:
        from concurrent.futures import ThreadPoolExecutor
        from app.application.services import market_cache
        tickers = sorted(t for t in tickers if t)
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = pool.map(lambda t: market_cache.cached(kind, t, lambda: fetch(t), ttl_hours=PortfolioService.YAHOO_TTL_H,
                                                            force=t in force), tickers)
            return dict(zip(tickers, values))

    @staticmethod
    def _yahoo_splits(ticker: str) -> Dict[str, float]:
        try:
            splits = yf.Ticker(ticker).splits
            if splits is None or splits.empty:
                return {}
            splits.index = splits.index.tz_convert(None)
            return {d.strftime('%Y-%m-%d'): float(r) for d, r in splits.items()}
        except Exception:
            return None   # error de red: no se guarda como «sin splits» (cached sí guarda None, pero caduca)

    @staticmethod
    def _yahoo_dividends(ticker: str) -> Dict[str, float]:
        try:
            divs = yf.Ticker(ticker).dividends
            if divs is None or divs.empty:
                return {}
            divs.index = divs.index.tz_convert(None)
            return {d.strftime('%Y-%m-%d'): float(v) for d, v in divs.items()}
        except Exception:
            return None

    @staticmethod
    def _yahoo_dividend_dates(ticker: str) -> Dict[str, Any]:
        try:
            info = yf.Ticker(ticker).info or {}
            return {"ex": info.get('exDividendDate') or info.get('dividendExDate'),
                    "pay": info.get('dividendDate') or info.get('dividendPaymentDate')}
        except Exception:
            return None

    def _get_pending_dividend_ex_dates(self, tickers: set, force: set = frozenset()) -> Dict[str, str]:
        """Para cada ticker, si el dividendo más reciente (ex-date) todavía no se ha
        pagado (pay-date en el futuro), devuelve {ticker: ex_date_str}. yfinance solo
        expone la pay-date del próximo/último evento vía `.info`, no un histórico, así
        que solo podemos detectar como "pendiente de cobro" ese último evento."""
        pending = {}
        today = datetime.now().date()
        for ticker, d in self._per_ticker("div_dates", tickers, self._yahoo_dividend_dates, force).items():
            try:
                if not d or not d.get("ex") or not d.get("pay"):
                    continue
                ex_date = datetime.fromtimestamp(d["ex"]).date()
                pay_date = datetime.fromtimestamp(d["pay"]).date()
                if ex_date <= today and pay_date > today:
                    pending[ticker] = ex_date.strftime('%Y-%m-%d')
            except Exception:
                pass
        return pending

    @staticmethod
    def _merge_history(path: str, fresh: Dict[str, Any]) -> Dict[str, Any]:
        """Historial guardado en disco + lo recién descargado (no se pierde lo que Yahoo deje de dar)."""
        history = {}
        if os.path.exists(path):
            with open(path, 'r') as f:
                history = json.load(f)
        for ticker, events in fresh.items():
            history.setdefault(ticker, {})
            history[ticker].update(events or {})
        with open(path, 'w') as f:
            json.dump(history, f, indent=4)
        return history

    def _sync_dividends(self, tickers: set, force: set = frozenset()):
        return self._merge_history(DIV_CACHE_FILE, self._per_ticker("dividends_hist", tickers, self._yahoo_dividends, force))

    def _sync_splits(self, tickers: set, force: set = frozenset()):
        return self._merge_history(SPLIT_CACHE_FILE, self._per_ticker("splits_hist", tickers, self._yahoo_splits, force))

    def rebuild_portfolio(self, force_tickers: set = frozenset()):
        csv_txs = self.load_csv_transactions()
        manual_txs = self.repository.load_manual_transactions()
        
        # 1. Obtener tickers de transacciones iniciales para buscar splits
        all_tx_raw = csv_txs + manual_txs
        tickers_seen_initial = {tx.ticker for tx in all_tx_raw}
        master_split_history = self._sync_splits(tickers_seen_initial, force_tickers)
        
        # Encontrar la fecha de la primera transacción para cada ticker
        first_tx_dates = {}
        for tx in all_tx_raw:
            ticker = tx.ticker
            dt = pd.to_datetime(tx.date)
            if ticker not in first_tx_dates or dt < first_tx_dates[ticker]:
                first_tx_dates[ticker] = dt

        all_transactions = all_tx_raw.copy()
        
        # 2. Integrar splits como transacciones virtuales
        for ticker, splits in master_split_history.items():
            if ticker not in tickers_seen_initial: continue
            first_date = first_tx_dates.get(ticker)
            for d_str, ratio in splits.items():
                if first_date and pd.to_datetime(d_str) >= first_date:
                    all_transactions.append(Transaction(
                        date=d_str + " 00:00:00", # Split al inicio del día
                        type=TransactionType.SPLIT,
                        ticker=ticker,
                        shares=ratio,
                        price=0.0,
                        total=0.0
                    ))

        all_transactions.sort(key=lambda x: pd.to_datetime(x.date))

        portfolio: Dict[str, PortfolioItem] = {}
        ledger: Dict[str, List[LedgerEntry]] = {}
        tickers_seen = set()
        
        for tx in all_transactions:
            ticker = tx.ticker
            tickers_seen.add(ticker)
            if ticker not in portfolio:
                portfolio[ticker] = PortfolioItem(ticker=ticker, currency_code=tx.currency)
                ledger[ticker] = []
            
            p = portfolio[ticker]
            fee = getattr(tx, 'fee', 0.0) or 0.0
            fx = getattr(tx, 'fx_rate_at_purchase', None)
            ccy = (getattr(tx, 'currency', 'USD') or 'USD').upper()
            fx_rate = fx if fx is not None else (1.0 if ccy == 'EUR' else (0.92 if ccy == 'USD' else (0.088 if ccy == 'SEK' else 1.0)))

            # Auto-calcular comisión si es transacción manual y no tiene comisión asignada
            tx_broker = getattr(tx, 'broker', 'IBKR') or 'IBKR'
            if tx_broker == 'AUTO':
                tx_broker = 'IBKR' if tx.date >= '2026' else 'DEGIRO'

            if fee == 0.0 and tx in manual_txs:
                b_set = self._get_broker_settings()
                conv_rate = fx_rate if fx_rate is not None else (0.92 if ccy == 'USD' else (0.088 if ccy == 'SEK' else 1.0))
                is_degiro = tx_broker == 'DEGIRO'
                
                if is_degiro:
                    if ccy == 'EUR':
                        fee = b_set.get("degiro_fee_eur", 1.0)
                    elif ccy == 'USD':
                        fee_eur = b_set.get("degiro_fee_usd", 1.0) + (b_set.get("degiro_autofx_pct", 0.25) / 100.0) * (tx.shares * tx.price * conv_rate)
                        fee = fee_eur / conv_rate if conv_rate > 0 else fee_eur
                    else:  # SEK
                        fee_eur = b_set.get("degiro_fee_sek", 3.90) + (b_set.get("degiro_autofx_pct", 0.25) / 100.0) * (tx.shares * tx.price * conv_rate)
                        fee = fee_eur / conv_rate if conv_rate > 0 else fee_eur
                else:
                    # IBKR
                    if ccy == 'USD':
                        comm_usd = max(b_set.get("ibkr_fee_usd_min", 1.00), b_set.get("ibkr_fee_usd_per_share", 0.005) * tx.shares)
                        comm_usd = min(comm_usd, (b_set.get("ibkr_fee_usd_max_pct", 1.0) / 100.0) * tx.shares * tx.price)
                        fx_usd = max(b_set.get("ibkr_autofx_usd_min", 2.00), (b_set.get("ibkr_autofx_usd_pct", 0.2) / 100.0) * tx.shares * tx.price)
                        fee = comm_usd + fx_usd
                    elif ccy == 'SEK':
                        comm_sek = max(b_set.get("ibkr_fee_sek_min", 40.0), (b_set.get("ibkr_fee_sek_pct", 0.05) / 100.0) * tx.shares * tx.price)
                        fx_sek = 22.0
                        fee = comm_sek + fx_sek
                    else:
                        comm_eur = max(b_set.get("ibkr_fee_eur_min", 1.25), (b_set.get("ibkr_fee_eur_pct", 0.05) / 100.0) * tx.shares * tx.price)
                        fee = comm_eur / conv_rate if conv_rate > 0 else comm_eur

            ledger_entry = LedgerEntry(
                date=tx.date,
                type=tx.type,
                shares=tx.shares,
                price=tx.price,
                total=tx.total if tx.total is not None else (tx.shares * tx.price),
                fee=fee,
                fx_rate_at_purchase=fx_rate,
                broker=tx_broker,
            )
            ledger[ticker].append(ledger_entry)

            if tx.type == TransactionType.BUY:
                cost_native = tx.shares * tx.price + fee
                p.shares += tx.shares
                p.total_cost += cost_native
                p.average_price = p.total_cost / p.shares if p.shares > 0 else 0
                p.total_shares_bought += tx.shares
                p.total_fees += fee
                if fx_rate is not None:
                    p.total_cost_eur += cost_native * fx_rate
            elif tx.type == TransactionType.SELL:
                shares_sold = tx.shares
                p.total_shares_sold += shares_sold
                p.total_sell_revenue += shares_sold * tx.price
                p.total_fees += fee

                actual_shares_to_deduct = min(shares_sold, p.shares)
                if actual_shares_to_deduct > 0:
                    cost_basis = shares_sold * p.average_price if p.shares > 0 else 0
                    p.realized_pnl += (shares_sold * tx.price - cost_basis) - fee
                    p.shares -= shares_sold
                    p.total_cost -= (shares_sold * p.average_price if p.shares > 0 else 0)
                    if fx_rate is not None and p.total_cost_eur > 0 and p.total_cost > 0:
                        # reducimos total_cost_eur proporcionalmente al coste que sale
                        p.total_cost_eur -= p.total_cost_eur * (cost_basis / (p.total_cost + cost_basis)) if (p.total_cost + cost_basis) > 0 else 0
                else:
                    p.realized_pnl += shares_sold * tx.price - fee
                    p.shares -= shares_sold

                if p.shares <= 0.0001:
                    p.shares = 0.0
                    p.average_price = 0.0
                    p.total_cost = 0.0
                    p.total_cost_eur = 0.0

            elif tx.type == TransactionType.SPLIT:
                ratio = tx.shares
                if ratio > 0 and ratio != 1.0:
                    p.shares *= ratio
                    p.average_price /= ratio
                    p.total_shares_bought *= ratio
                    p.total_shares_sold *= ratio
                    # total_cost y total_cost_eur se mantienen iguales

        master_dividend_history = self._sync_dividends(tickers_seen, force_tickers)
        pending_ex_dates = self._get_pending_dividend_ex_dates(tickers_seen, force_tickers)

        for ticker, txs in ledger.items():
            daily_balance = {}
            curr = 0
            for tx in txs:
                if tx.type == TransactionType.DIVIDEND: continue 
                dt = pd.to_datetime(tx.date)
                if tx.type == TransactionType.SPLIT:
                    curr *= tx.shares
                else:
                    curr += tx.shares if tx.type == TransactionType.BUY else -tx.shares
                daily_balance[dt] = curr
            
            if not daily_balance: continue
            balance_series = pd.Series(daily_balance).sort_index()

            ticker_divs = master_dividend_history.get(ticker, {})
            ticker_splits = master_split_history.get(ticker, {})
            total_divs_collected = 0

            for d_date_str, d_amount in ticker_divs.items():
                if pd.isna(d_amount) or d_amount <= 0: continue
                if pending_ex_dates.get(ticker) == d_date_str: continue  # ex-date ya pasó pero el pago sigue pendiente
                d_date = pd.to_datetime(d_date_str)
                held_before = balance_series[balance_series.index <= d_date]
                if not held_before.empty:
                    shares_at_ex_date = held_before.iloc[-1]
                    if shares_at_ex_date > 0.0001:
                        # yfinance devuelve dividendos ajustados por splits posteriores.
                        # Hay que multiplicar las acciones por el ratio acumulado de
                        # splits que ocurrieron DESPUÉS de esta fecha para que
                        # total = shares_post_split_equiv × d_amount_ajustado sea correcto.
                        future_split_ratio = 1.0
                        for s_date_str, s_ratio in ticker_splits.items():
                            if pd.to_datetime(s_date_str) > d_date and s_ratio > 0:
                                future_split_ratio *= s_ratio
                        adj_shares = shares_at_ex_date * future_split_ratio
                        payout = adj_shares * d_amount
                        total_divs_collected += payout
                        ledger[ticker].append(LedgerEntry(
                            date=d_date_str,
                            type=TransactionType.DIVIDEND,
                            shares=adj_shares,
                            price=d_amount,
                            total=payout
                        ))

            portfolio[ticker].dividends_collected = total_divs_collected
            ledger[ticker] = sorted(ledger[ticker], key=lambda x: x.date)

        active_portfolio = [p for p in portfolio.values() if p.shares > 0.0001]
        closed_portfolio = [p for p in portfolio.values() if p.total_shares_sold > 0 and p.shares <= 0.0001]
        
        for p in closed_portfolio: 
            p.average_sell_price = p.total_sell_revenue / p.total_shares_sold if p.total_shares_sold > 0 else 0

        self.repository.save_active_portfolio(active_portfolio)
        self.repository.save_closed_portfolio(closed_portfolio)
        self.repository.save_ledger(ledger)

    def get_active_portfolio(self) -> List[PortfolioItem]:
        return self.repository.load_active_portfolio()

    def get_closed_portfolio(self) -> List[PortfolioItem]:
        return self.repository.load_closed_portfolio()

    def get_ledger(self, ticker: str) -> List[LedgerEntry]:
        return self.repository.load_ledger(ticker)

    def delete_ticker_data(self, ticker: str) -> None:
        self.repository.delete_ticker_data(ticker)

    def dividends_eur(self, fx_rates: dict = None) -> list:
        """Dividendos cobrados (del ledger, que es donde se guardan) pasados a EUR con el cambio de hoy:
        [{date, ticker, currency, native, eur}]. Lo usan Fiscal, la pestaña Dividendos e Historial."""
        fx = fx_rates or {}
        out = []
        for p in self.get_active_portfolio() + self.get_closed_portfolio():
            ccy = (p.currency_code or 'USD').upper()
            rate = 1.0 if ccy == 'EUR' else fx.get(ccy, 1.0)
            for entry in self.get_ledger(p.ticker):
                if entry.type == TransactionType.DIVIDEND:
                    out.append({'date': entry.date, 'ticker': p.ticker, 'currency': ccy,
                                'native': entry.total, 'eur': entry.total * rate})
        return out

    def get_fiscal_summary(self, fx_rates: dict = None) -> list:
        """P&L realizado y dividendos agrupados por año (en EUR aproximado)."""
        fx = fx_rates or {}
        csv_txs = self.load_csv_transactions()
        manual_txs = self.repository.load_manual_transactions()
        all_txs = sorted(csv_txs + manual_txs, key=lambda x: x.date)

        holdings: Dict[str, dict] = {}
        yearly: Dict[str, dict] = {}

        for tx in all_txs:
            year = str(tx.date)[:4]
            if year not in yearly:
                yearly[year] = {'gains': 0.0, 'losses': 0.0, 'dividends': 0.0, 'fees': 0.0}

            ticker = tx.ticker.upper()
            ccy = (getattr(tx, 'currency', 'USD') or 'USD').upper()
            fee = getattr(tx, 'fee', 0.0) or 0.0
            # Preferir FX histórico si está disponible; fallback al FX actual
            fx_hist = getattr(tx, 'fx_rate_at_purchase', None)
            rate = fx_hist if (fx_hist is not None) else (1.0 if ccy == 'EUR' else fx.get(ccy, 1.0))

            if ticker not in holdings:
                holdings[ticker] = {'shares': 0.0, 'avg_eur': 0.0}
            h = holdings[ticker]
            price_eur = tx.price * rate
            fee_eur = fee * rate

            if tx.type.value == 'BUY':
                total_cost = h['shares'] * h['avg_eur'] + tx.shares * price_eur + fee_eur
                h['shares'] += tx.shares
                h['avg_eur'] = total_cost / h['shares'] if h['shares'] > 0 else 0.0
                yearly[year]['fees'] += fee_eur
            elif tx.type.value == 'SELL':
                pnl = (price_eur - h['avg_eur']) * tx.shares - fee_eur
                if pnl >= 0:
                    yearly[year]['gains'] += pnl
                else:
                    yearly[year]['losses'] += pnl
                h['shares'] = max(0.0, h['shares'] - tx.shares)
                yearly[year]['fees'] += fee_eur

        # los dividendos están en el ledger (sincronizados del bróker), no entre las operaciones
        for d in self.dividends_eur(fx):
            yearly.setdefault(d['date'][:4], {'gains': 0.0, 'losses': 0.0, 'dividends': 0.0, 'fees': 0.0})['dividends'] += d['eur']

        result = []
        for year in sorted(yearly.keys()):
            d = yearly[year]
            net = d['gains'] + d['losses'] + d['dividends']
            result.append({
                'year': year,
                'gains': round(d['gains'], 2),
                'losses': round(d['losses'], 2),
                'dividends': round(d['dividends'], 2),
                'fees': round(d.get('fees', 0.0), 2),
                'net': round(net, 2),
                'tax_estimate': round(self._spanish_tax(net), 2) if net > 0 else 0.0,
            })
        return result

    @staticmethod
    def _spanish_tax(amount: float) -> float:
        """IRPF, base del ahorro (desde 2025): límite superior acumulado de cada tramo y su tipo."""
        if amount <= 0:
            return 0.0
        brackets = [(6_000, 0.19), (50_000, 0.21), (200_000, 0.23), (300_000, 0.27), (float('inf'), 0.30)]
        tax, prev = 0.0, 0.0
        for limit, rate in brackets:
            chunk = min(amount - prev, (limit - prev) if limit != float('inf') else amount)
            tax += max(0.0, chunk) * rate
            if amount <= limit:
                break
            prev = limit
        return tax

    def get_realized_trades(self, fx_rates: dict = None) -> list:
        """Obtiene un historial detallado de transacciones individuales de Compra, Venta (con P&L calculado desglosado)
        y Dividendos cobrados (del ledger), ordenado por fecha de forma descendente."""
        fx = fx_rates or {}
        csv_txs = self.load_csv_transactions()
        manual_txs = self.repository.load_manual_transactions()
        all_txs = sorted(csv_txs + manual_txs, key=lambda x: x.date)

        holdings: Dict[str, dict] = {}
        realized_trades = []

        # 1. Procesar compras y ventas cronológicamente para calcular el P&L de cada venta
        for tx in all_txs:
            ticker = tx.ticker.upper()
            ccy = (getattr(tx, 'currency', 'USD') or 'USD').upper()
            fee = getattr(tx, 'fee', 0.0) or 0.0
            fx_hist = getattr(tx, 'fx_rate_at_purchase', None)
            rate = fx_hist if (fx_hist is not None) else (1.0 if ccy == 'EUR' else fx.get(ccy, 1.0))

            # Auto-calcular comisión si es transacción manual y no tiene comisión asignada
            tx_broker = getattr(tx, 'broker', 'IBKR') or 'IBKR'
            if tx_broker == 'AUTO':
                tx_broker = 'IBKR' if tx.date >= '2026' else 'DEGIRO'

            if fee == 0.0 and tx in manual_txs:
                b_set = self._get_broker_settings()
                is_degiro = tx_broker == 'DEGIRO'
                
                if is_degiro:
                    if ccy == 'EUR':
                        fee = b_set.get("degiro_fee_eur", 1.0)
                    elif ccy == 'USD':
                        fee_eur = b_set.get("degiro_fee_usd", 1.0) + (b_set.get("degiro_autofx_pct", 0.25) / 100.0) * (tx.shares * tx.price * rate)
                        fee = fee_eur / rate if rate > 0 else fee_eur
                    else:  # SEK
                        fee_eur = b_set.get("degiro_fee_sek", 3.90) + (b_set.get("degiro_autofx_pct", 0.25) / 100.0) * (tx.shares * tx.price * rate)
                        fee = fee_eur / rate if rate > 0 else fee_eur
                else:
                    # IBKR
                    if ccy == 'USD':
                        comm_usd = max(b_set.get("ibkr_fee_usd_min", 1.00), b_set.get("ibkr_fee_usd_per_share", 0.005) * tx.shares)
                        comm_usd = min(comm_usd, (b_set.get("ibkr_fee_usd_max_pct", 1.0) / 100.0) * tx.shares * tx.price)
                        fx_usd = max(b_set.get("ibkr_autofx_usd_min", 2.00), (b_set.get("ibkr_autofx_usd_pct", 0.2) / 100.0) * tx.shares * tx.price)
                        fee = comm_usd + fx_usd
                    elif ccy == 'SEK':
                        comm_sek = max(b_set.get("ibkr_fee_sek_min", 40.0), (b_set.get("ibkr_fee_sek_pct", 0.05) / 100.0) * tx.shares * tx.price)
                        fx_sek = 22.0
                        fee = comm_sek + fx_sek
                    else:
                        comm_eur = max(b_set.get("ibkr_fee_eur_min", 1.25), (b_set.get("ibkr_fee_eur_pct", 0.05) / 100.0) * tx.shares * tx.price)
                        fee = comm_eur / rate if rate > 0 else comm_eur

            if ticker not in holdings:
                holdings[ticker] = {'shares': 0.0, 'avg_eur': 0.0, 'avg_native': 0.0}
            h = holdings[ticker]
            price_eur = tx.price * rate
            fee_eur = fee * rate

            if tx.type.value == 'BUY':
                total_cost_eur = h['shares'] * h['avg_eur'] + tx.shares * price_eur + fee_eur
                total_cost_native = h['shares'] * h['avg_native'] + tx.shares * tx.price + fee
                h['shares'] += tx.shares
                h['avg_eur'] = total_cost_eur / h['shares'] if h['shares'] > 0 else 0.0
                h['avg_native'] = total_cost_native / h['shares'] if h['shares'] > 0 else 0.0

                realized_trades.append({
                    'date': tx.date,
                    'type': 'COMPRA',
                    'ticker': ticker,
                    'shares': tx.shares,
                    'price': tx.price,
                    'currency': ccy,
                    'pnl_eur': 0.0,
                    'pnl_price_eur': 0.0,
                    'pnl_fx_eur': 0.0,
                    'fx_rate': rate,
                    'fx_avg_purchase': rate,
                    'fee_eur': round(fee_eur, 2),
                    'broker': tx_broker,
                })
            elif tx.type.value == 'SELL':
                pnl_eur = (price_eur - h['avg_eur']) * tx.shares - fee_eur
                fx_avg_purchase = h['avg_eur'] / h['avg_native'] if h['avg_native'] > 0 else rate
                pnl_price_eur = tx.shares * (tx.price - h['avg_native']) * fx_avg_purchase - fee_eur
                pnl_fx_eur = tx.shares * tx.price * (rate - fx_avg_purchase)

                realized_trades.append({
                    'date': tx.date,
                    'type': 'VENTA',
                    'ticker': ticker,
                    'shares': tx.shares,
                    'price': tx.price,
                    'currency': ccy,
                    'pnl_eur': round(pnl_eur, 2),
                    'pnl_price_eur': round(pnl_price_eur, 2),
                    'pnl_fx_eur': round(pnl_fx_eur, 2),
                    'fx_rate': rate,
                    'fx_avg_purchase': fx_avg_purchase,
                    'fee_eur': round(fee_eur, 2),
                    'broker': tx_broker,
                })
                h['shares'] = max(0.0, h['shares'] - tx.shares)

        # 2. Obtener dividendos cobrados del ledger
        active = self.get_active_portfolio()
        closed = self.get_closed_portfolio()
        ticker_ccy = {p.ticker: p.currency_code for p in active + closed}

        for ticker, ccy in ticker_ccy.items():
            ledger = self.get_ledger(ticker)
            ccy_upper = ccy.upper()
            rate = 1.0 if ccy_upper == 'EUR' else fx.get(ccy_upper, 1.0)
            for entry in ledger:
                if entry.type == TransactionType.DIVIDEND:
                    pnl_eur = entry.total * rate
                    
                    div_broker = getattr(entry, 'broker', 'AUTO') or 'AUTO'
                    if div_broker == 'AUTO':
                        div_broker = 'IBKR' if entry.date >= '2026' else 'DEGIRO'
                        
                    realized_trades.append({
                        'date': entry.date,
                        'type': 'DIVIDENDO',
                        'ticker': ticker,
                        'shares': entry.shares,
                        'price': entry.price,
                        'currency': ccy_upper,
                        'pnl_eur': round(pnl_eur, 2),
                        'pnl_price_eur': round(pnl_eur, 2),
                        'pnl_fx_eur': 0.0,
                        'fx_rate': rate,
                        'fx_avg_purchase': rate,
                        'fee_eur': 0.0,
                        'broker': div_broker,
                    })

        # 3. Ordenar todas las operaciones por fecha descendente (tomando los primeros 10 caracteres "YYYY-MM-DD")
        realized_trades.sort(key=lambda x: x['date'][:10], reverse=True)
        return realized_trades

    def get_yearly_dividends(self, fx_rates: dict = None) -> Dict[str, Any]:
        """Dividendos cobrados por año y por empresa, en EUR."""
        data_by_year_ticker: Dict[str, Dict[str, float]] = {}
        for d in self.dividends_eur(fx_rates):
            year = data_by_year_ticker.setdefault(d['date'][:4], {})
            year[d['ticker']] = year.get(d['ticker'], 0) + d['eur']
        return {
            "years": sorted(data_by_year_ticker),
            "tickers": sorted({t for y in data_by_year_ticker.values() for t in y}),
            "data": data_by_year_ticker
        }

    def get_latest_collected_dividends(self, limit: int = 10) -> list:
        """Obtiene una lista de los últimos dividendos cobrados en el ledger, ordenados por fecha descendente."""
        active = self.get_active_portfolio()
        closed = self.get_closed_portfolio()
        ticker_ccy = {p.ticker: p.currency_code for p in active + closed}
        
        all_dividends = []
        for ticker, ccy in ticker_ccy.items():
            ledger = self.get_ledger(ticker)
            for entry in ledger:
                if entry.type == TransactionType.DIVIDEND:
                    all_dividends.append({
                        "ticker": ticker,
                        "date": entry.date,
                        "amount_per_share": entry.price,
                        "total_amount": entry.total,
                        "currency_code": ccy
                    })
        
        # Ordenar por fecha descendente (las fechas de dividendos son strings en formato YYYY-MM-DD o similar)
        all_dividends.sort(key=lambda x: x["date"], reverse=True)
        return all_dividends[:limit]

    def get_dividend_kpis(self, fx_rates: dict = None) -> Dict[str, Any]:
        """KPIs de dividendos: YTD, año anterior, media mensual."""
        from datetime import date, timedelta
        today = date.today()
        current_year = str(today.year)
        last_year = str(today.year - 1)

        total_ytd = 0.0
        total_last_year = 0.0
        monthly_totals: Dict[str, float] = {}

        for d in self.dividends_eur(fx_rates):
            year = d['date'][:4]
            if year == current_year:
                total_ytd += d['eur']
            elif year == last_year:
                total_last_year += d['eur']
            ym = d['date'][:7]
            monthly_totals[ym] = monthly_totals.get(ym, 0) + d['eur']

        # Promedio mensual últimos 12 meses
        months_12 = []
        d = today.replace(day=1)
        for _ in range(12):
            months_12.append(d.strftime('%Y-%m'))
            d = (d - timedelta(days=1)).replace(day=1)
        avg_monthly = sum(monthly_totals.get(m, 0) for m in months_12) / 12

        yoy_change = ((total_ytd / total_last_year) - 1) * 100 if total_last_year > 0 else None

        return {
            "total_ytd": total_ytd,
            "total_last_year": total_last_year,
            "yoy_change": yoy_change,
            "avg_monthly": avg_monthly,
            "current_year": today.year,
        }

    def get_dividend_by_ticker(self) -> list:
        """Métricas de dividendos por ticker: TTM, yield on cost, histórico."""
        from datetime import date, timedelta
        today = date.today()
        one_year_ago = (today - timedelta(days=365)).strftime('%Y-%m-%d')

        active = self.get_active_portfolio()
        closed = self.get_closed_portfolio()
        active_map = {p.ticker: p for p in active}
        closed_map = {p.ticker: p for p in closed}
        all_tickers = list(set(list(active_map.keys()) + list(closed_map.keys())))

        result = []
        for ticker in all_tickers:
            item = active_map.get(ticker) or closed_map.get(ticker)
            total_collected = 0.0
            ttm_income = 0.0

            for entry in self.get_ledger(ticker):
                if entry.type == TransactionType.DIVIDEND:
                    total_collected += entry.total
                    if entry.date >= one_year_ago:
                        ttm_income += entry.total

            if total_collected == 0:
                continue

            avg_price = getattr(item, 'average_price', 0) or 0
            shares = getattr(item, 'shares', 0) or 0
            cost_basis = avg_price * shares
            ttm_per_share = ttm_income / shares if shares > 0 else 0
            yoc = (ttm_income / cost_basis * 100) if cost_basis > 0 else 0
            ccy = getattr(item, 'currency_code', 'EUR') or 'EUR'

            result.append({
                "ticker": ticker,
                "total_collected": total_collected,
                "ttm_income": ttm_income,
                "ttm_per_share": ttm_per_share,
                "cost_basis": cost_basis,
                "yield_on_cost": yoc,
                "currency_code": ccy,
                "is_active": ticker in active_map,
                "shares": shares,
                "avg_price": avg_price,
            })

        result.sort(key=lambda x: x["ttm_income"], reverse=True)
        return result

    def _get_broker_settings(self) -> dict:
        """Obtiene las comisiones guardadas de brokers de la DB o usa valores por defecto."""
        try:
            from app.infrastructure.db.models import DBSetting
            db_session = self.repository.db
            row = db_session.query(DBSetting).filter(DBSetting.key == 'brokers').first()
            if row and row.value:
                return row.value
        except Exception:
            pass
        return {
            "degiro_fee_eur": 1.0,
            "degiro_fee_usd": 1.0,
            "degiro_fee_sek": 3.90,
            "degiro_autofx_pct": 0.25,
            "ibkr_fee_usd_per_share": 0.005,
            "ibkr_fee_usd_min": 1.00,
            "ibkr_fee_usd_max_pct": 1.0,
            "ibkr_autofx_usd_min": 2.00,
            "ibkr_autofx_usd_pct": 0.2,
            "ibkr_fee_sek_min": 40.0,
            "ibkr_fee_sek_pct": 0.05,
            "ibkr_fee_eur_min": 1.25,
            "ibkr_fee_eur_pct": 0.05
        }
