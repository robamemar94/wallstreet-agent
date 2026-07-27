from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.domain.models import PortfolioItem, Transaction, LedgerEntry, TransactionType
from app.domain.repositories.portfolio_repository import PortfolioRepositoryInterface
from app.infrastructure.db.models import DBPortfolioItem, DBLedgerEntry, DBTransaction, DBAsset

class SqlAlchemyPortfolioRepository(PortfolioRepositoryInterface):
    def __init__(self, db_session: Session):
        self.db = db_session

    def _ensure_asset_exists(self, ticker: str):
        ticker = ticker.upper()
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if not asset:
            asset = DBAsset(ticker=ticker)
            self.db.add(asset)
            self.db.flush() # Ensure the asset is available for FK constraints but don't commit yet

    def _portfolio_item_from_db(self, item) -> PortfolioItem:
        return PortfolioItem(
            ticker=item.ticker,
            shares=item.shares,
            average_price=item.average_price,
            total_cost=item.total_cost,
            realized_pnl=item.realized_pnl,
            total_shares_bought=item.total_shares_bought,
            total_shares_sold=item.total_shares_sold,
            total_sell_revenue=item.total_sell_revenue,
            currency_code=item.currency_code,
            dividends_collected=item.dividends_collected,
            average_sell_price=item.average_sell_price,
            total_fees=item.total_fees or 0.0,
            total_cost_eur=item.total_cost_eur or 0.0,
        )

    def _db_item_from_portfolio(self, p_item: PortfolioItem, is_closed: bool) -> DBPortfolioItem:
        return DBPortfolioItem(
            ticker=p_item.ticker.upper(),
            is_closed=is_closed,
            shares=p_item.shares,
            average_price=p_item.average_price,
            total_cost=p_item.total_cost,
            realized_pnl=p_item.realized_pnl,
            total_shares_bought=p_item.total_shares_bought,
            total_shares_sold=p_item.total_shares_sold,
            total_sell_revenue=p_item.total_sell_revenue,
            currency_code=p_item.currency_code,
            dividends_collected=p_item.dividends_collected,
            average_sell_price=p_item.average_sell_price,
            total_fees=p_item.total_fees,
            total_cost_eur=p_item.total_cost_eur,
        )

    def load_active_portfolio(self) -> List[PortfolioItem]:
        items = self.db.query(DBPortfolioItem).filter(DBPortfolioItem.is_closed == False).all()
        return [self._portfolio_item_from_db(item) for item in items]

    def load_closed_portfolio(self) -> List[PortfolioItem]:
        items = self.db.query(DBPortfolioItem).filter(DBPortfolioItem.is_closed == True).all()
        return [self._portfolio_item_from_db(item) for item in items]

    def save_active_portfolio(self, portfolio: List[PortfolioItem]) -> None:
        ticker_list = [p.ticker.upper() for p in portfolio]
        self.db.query(DBPortfolioItem).filter(DBPortfolioItem.ticker.in_(ticker_list)).delete(synchronize_session=False)
        for p_item in portfolio:
            self._ensure_asset_exists(p_item.ticker)
            self.db.add(self._db_item_from_portfolio(p_item, is_closed=False))
        self.db.commit()

    def save_closed_portfolio(self, portfolio: List[PortfolioItem]) -> None:
        ticker_list = [p.ticker.upper() for p in portfolio]
        self.db.query(DBPortfolioItem).filter(DBPortfolioItem.ticker.in_(ticker_list)).delete(synchronize_session=False)
        for p_item in portfolio:
            self._ensure_asset_exists(p_item.ticker)
            self.db.add(self._db_item_from_portfolio(p_item, is_closed=True))
        self.db.commit()

    def load_ledger(self, ticker: str) -> List[LedgerEntry]:
        entries = self.db.query(DBLedgerEntry).filter(DBLedgerEntry.ticker == ticker).all()
        return [
            LedgerEntry(
                date=entry.date,
                type=entry.type,
                shares=entry.shares,
                price=entry.price,
                total=entry.total
            ) for entry in entries
        ]

    def save_ledger(self, ledger: Dict[str, List[LedgerEntry]]) -> None:
        # Assuming ledger dict contains all ledger entries we want to persist, replacing old ones
        # For safety we might only update the tickers provided in the dict
        for ticker, entries in ledger.items():
            self._ensure_asset_exists(ticker)
            self.db.query(DBLedgerEntry).filter(DBLedgerEntry.ticker == ticker).delete()
            for entry in entries:
                db_entry = DBLedgerEntry(
                    ticker=ticker,
                    date=entry.date,
                    type=entry.type,
                    shares=entry.shares,
                    price=entry.price,
                    total=entry.total
                )
                self.db.add(db_entry)
        self.db.commit()

    def load_manual_transactions(self) -> List[Transaction]:
        transactions = self.db.query(DBTransaction).all()
        return [
            Transaction(
                date=t.date,
                type=t.type,
                ticker=t.ticker,
                shares=t.shares,
                price=t.price,
                currency=t.currency,
                total=t.total,
                fee=t.fee or 0.0,
                fx_rate_at_purchase=t.fx_rate_at_purchase,
                broker=t.broker or "AUTO",
            ) for t in transactions
        ]

    def save_manual_transactions(self, transactions: List[Transaction]) -> None:
        self.db.query(DBTransaction).delete()
        for t in transactions:
            self._ensure_asset_exists(t.ticker)
            db_t = DBTransaction(
                ticker=t.ticker,
                date=t.date,
                type=t.type,
                shares=t.shares,
                price=t.price,
                currency=t.currency,
                total=t.total,
                fee=t.fee or 0.0,
                fx_rate_at_purchase=t.fx_rate_at_purchase,
                broker=t.broker or "AUTO",
            )
            self.db.add(db_t)
        self.db.commit()

    def delete_ticker_data(self, ticker: str) -> None:
        ticker = ticker.upper()
        self.db.query(DBPortfolioItem).filter(DBPortfolioItem.ticker == ticker).delete()
        self.db.query(DBLedgerEntry).filter(DBLedgerEntry.ticker == ticker).delete()
        self.db.query(DBTransaction).filter(DBTransaction.ticker == ticker).delete()
        self.db.commit()
