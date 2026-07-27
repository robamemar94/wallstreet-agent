import json
import os
from typing import List, Dict
from app.domain.models import PortfolioItem, Transaction, LedgerEntry
from app.domain.repositories.portfolio_repository import PortfolioRepositoryInterface
from app.infrastructure.config import PORTFOLIO_FILE, CLOSED_PORTFOLIO_FILE, LEDGER_FILE, MANUAL_TX_FILE, DATA_DIR

class JSONPortfolioRepository(PortfolioRepositoryInterface):
    def _ensure_data_dir(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

    def load_active_portfolio(self) -> List[PortfolioItem]:
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [PortfolioItem(**item) for item in data]
        return []

    def save_active_portfolio(self, portfolio: List[PortfolioItem]) -> None:
        self._ensure_data_dir()
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump([item.model_dump() for item in portfolio], f, indent=4, ensure_ascii=False)

    def load_closed_portfolio(self) -> List[PortfolioItem]:
        if os.path.exists(CLOSED_PORTFOLIO_FILE):
            with open(CLOSED_PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [PortfolioItem(**item) for item in data]
        return []

    def save_closed_portfolio(self, portfolio: List[PortfolioItem]) -> None:
        self._ensure_data_dir()
        with open(CLOSED_PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump([item.model_dump() for item in portfolio], f, indent=4, ensure_ascii=False)

    def load_ledger(self, ticker: str) -> List[LedgerEntry]:
        if os.path.exists(LEDGER_FILE):
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                entries = data.get(ticker.upper(), [])
                return [LedgerEntry(**entry) for entry in entries]
        return []

    def save_ledger(self, ledger: Dict[str, List[LedgerEntry]]) -> None:
        self._ensure_data_dir()
        # Merge with existing ledger instead of overwriting completely if needed
        # Or just overwrite completely since the app builds the whole ledger
        serializable_ledger = {
            ticker: [entry.model_dump() for entry in entries]
            for ticker, entries in ledger.items()
        }
        with open(LEDGER_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable_ledger, f, indent=4, ensure_ascii=False)

    def load_manual_transactions(self) -> List[Transaction]:
        if os.path.exists(MANUAL_TX_FILE):
            with open(MANUAL_TX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [Transaction(**item) for item in data]
        return []

    def save_manual_transactions(self, transactions: List[Transaction]) -> None:
        self._ensure_data_dir()
        with open(MANUAL_TX_FILE, "w", encoding="utf-8") as f:
            json.dump([item.model_dump() for item in transactions], f, indent=4, ensure_ascii=False)
