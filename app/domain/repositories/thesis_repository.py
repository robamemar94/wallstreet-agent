from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class ThesisRepositoryInterface(ABC):
    @abstractmethod
    def list_theses(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_thesis(self, thesis_id: int) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def import_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def update_thesis(self, thesis_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def set_extra(self, thesis_id: int, key: str, value: Any) -> bool:
        pass

    @abstractmethod
    def upsert_observation(self, kpi_id: int, period: str, date: str, value: Optional[float],
                           status: str, note: Optional[str], source: Optional[str]) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def delete_observation(self, observation_id: int) -> bool:
        pass

    @abstractmethod
    def add_event(self, thesis_id: int, date: str, type: str, title: str,
                  body: Optional[str] = None, impact: Optional[str] = None) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def delete_event(self, event_id: int) -> bool:
        pass
