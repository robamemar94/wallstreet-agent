from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.domain.repositories.asset_repository import AssetRepositoryInterface
from app.infrastructure.db.models import DBAsset, DBCompanyList

class SqlAlchemyAssetRepository(AssetRepositoryInterface):
    def __init__(self, db: Session):
        self.db = db

    def get_all_tickers(self) -> List[str]:
        rows = self.db.query(DBAsset.ticker).all()
        return sorted([row[0] for row in rows])

    def get_asset_data(self, ticker: str) -> Dict[str, Any]:
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if asset:
            return asset.data if asset.data else {}
        return {}

    def save_asset_data(self, ticker: str, key: str, value: Any) -> None:
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if not asset:
            asset = DBAsset(ticker=ticker, data={key: value})
            self.db.add(asset)
        else:
            data = dict(asset.data) if asset.data else {}
            data[key] = value
            asset.data = data
            
        # Update company_name if key is company_name
        if key == "company_name":
            asset.company_name = value
            
        self.db.commit()

    def delete_asset(self, ticker: str) -> bool:
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if asset:
            self.db.delete(asset)
            self.db.commit()
            return True
        return False

    def get_all_companies(self) -> List[Dict[str, str]]:
        rows = self.db.query(DBAsset.ticker, DBAsset.company_name).all()
        return [{"ticker": row[0], "name": row[1] or row[0]} for row in rows]

    def toggle_favorite(self, ticker: str) -> bool:
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if asset:
            asset.is_favorite = not asset.is_favorite
            self.db.commit()
            return asset.is_favorite
        return False

    def is_favorite(self, ticker: str) -> bool:
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        return asset.is_favorite if asset else False

    def create_company_list(self, name: str, description: Optional[str] = None) -> Any:
        existing = self.db.query(DBCompanyList).filter(DBCompanyList.name == name).first()
        if existing:
            return existing
        new_list = DBCompanyList(name=name, description=description)
        self.db.add(new_list)
        self.db.commit()
        return new_list

    def delete_company_list(self, list_id: int) -> bool:
        clist = self.db.query(DBCompanyList).filter(DBCompanyList.id == list_id).first()
        if clist:
            self.db.delete(clist)
            self.db.commit()
            return True
        return False

    def get_company_lists(self) -> List[Any]:
        return self.db.query(DBCompanyList).all()

    def get_company_list_by_id(self, list_id: int) -> Optional[Any]:
        return self.db.query(DBCompanyList).filter(DBCompanyList.id == list_id).first()

    def get_company_list_by_name(self, name: str) -> Optional[Any]:
        return self.db.query(DBCompanyList).filter(DBCompanyList.name == name).first()

    def add_ticker_to_list(self, list_id: int, ticker: str) -> bool:
        clist = self.db.query(DBCompanyList).filter(DBCompanyList.id == list_id).first()
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if clist and asset:
            if asset not in clist.assets:
                clist.assets.append(asset)
                self.db.commit()
                return True
        return False

    def remove_ticker_from_list(self, list_id: int, ticker: str) -> bool:
        clist = self.db.query(DBCompanyList).filter(DBCompanyList.id == list_id).first()
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if clist and asset:
            if asset in clist.assets:
                clist.assets.remove(asset)
                self.db.commit()
                return True
        return False

    def get_lists_for_ticker(self, ticker: str) -> List[Any]:
        asset = self.db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if asset:
            return asset.custom_lists
        return []

    def update_company_list(self, list_id: int, name: str, description: Optional[str] = None) -> Any:
        clist = self.db.query(DBCompanyList).filter(DBCompanyList.id == list_id).first()
        if clist:
            clist.name = name
            clist.description = description
            self.db.commit()
            return clist
        return None
