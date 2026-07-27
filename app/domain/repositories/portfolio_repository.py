from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from app.domain.models import PortfolioItem, Transaction, LedgerEntry

class PortfolioRepositoryInterface(ABC):
    @abstractmethod
    def load_active_portfolio(self) -> List[PortfolioItem]:
        pass

    @abstractmethod
    def load_closed_portfolio(self) -> List[PortfolioItem]:
        pass

    @abstractmethod
    def save_active_portfolio(self, portfolio: List[PortfolioItem]) -> None:
        pass

    @abstractmethod
    def save_closed_portfolio(self, portfolio: List[PortfolioItem]) -> None:
        pass

    @abstractmethod
    def load_ledger(self, ticker: str) -> List[LedgerEntry]:
        pass
    
    @abstractmethod
    def save_ledger(self, ledger: Dict[str, List[LedgerEntry]]) -> None:
        pass

    @abstractmethod
    def load_manual_transactions(self) -> List[Transaction]:
        pass

    @abstractmethod
    def save_manual_transactions(self, transactions: List[Transaction]) -> None:
        pass

    @abstractmethod
    def delete_ticker_data(self, ticker: str) -> None:
        pass
