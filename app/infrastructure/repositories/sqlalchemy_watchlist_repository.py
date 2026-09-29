from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.infrastructure.db.models import DBWatchlistItem
from app.application.services.thesis_service import now_iso


def _item_dict(w: DBWatchlistItem) -> Dict[str, Any]:
    return {"id": w.id, "ticker": w.ticker, "added_price": w.added_price, "added_at": w.added_at}


class SqlAlchemyWatchlistRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_items(self) -> List[Dict[str, Any]]:
        return [_item_dict(w) for w in self.db.query(DBWatchlistItem).order_by(DBWatchlistItem.ticker).all()]

    def get_by_ticker(self, ticker: str) -> Optional[Dict[str, Any]]:
        w = self.db.query(DBWatchlistItem).filter_by(ticker=ticker).first()
        return _item_dict(w) if w else None

    def add_item(self, ticker: str, added_price: Optional[float] = None) -> Dict[str, Any]:
        w = DBWatchlistItem(ticker=ticker, added_price=added_price, added_at=now_iso())
        self.db.add(w)
        self.db.commit()
        return _item_dict(w)

    def delete_item(self, ticker: str) -> bool:
        w = self.db.query(DBWatchlistItem).filter_by(ticker=ticker).first()
        if not w:
            return False
        self.db.delete(w)
        self.db.commit()
        return True
