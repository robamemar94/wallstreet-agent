from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class AssetRepositoryInterface(ABC):
    @abstractmethod
    def get_all_tickers(self) -> List[str]:
        pass

    @abstractmethod
    def get_asset_data(self, ticker: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def save_asset_data(self, ticker: str, key: str, value: Any) -> None:
        pass

    @abstractmethod
    def delete_asset(self, ticker: str) -> bool:
        pass

    @abstractmethod
    def get_all_companies(self) -> List[Dict[str, str]]:
        pass

    @abstractmethod
    def create_company_list(self, name: str, description: Optional[str] = None) -> Any:
        pass

    @abstractmethod
    def delete_company_list(self, list_id: int) -> bool:
        pass

    @abstractmethod
    def get_company_lists(self) -> List[Any]:
        pass

    @abstractmethod
    def get_company_list_by_id(self, list_id: int) -> Optional[Any]:
        pass

    @abstractmethod
    def get_company_list_by_name(self, name: str) -> Optional[Any]:
        pass

    @abstractmethod
    def add_ticker_to_list(self, list_id: int, ticker: str) -> bool:
        pass

    @abstractmethod
    def remove_ticker_from_list(self, list_id: int, ticker: str) -> bool:
        pass

    @abstractmethod
    def get_lists_for_ticker(self, ticker: str) -> List[Any]:
        pass

    @abstractmethod
    def update_company_list(self, list_id: int, name: str, description: Optional[str] = None) -> Any:
        pass
