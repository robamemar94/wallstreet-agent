import pytest
from app.infrastructure.db.database import SessionLocal
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.models import DBAsset

def test_company_lists_crud_and_associations():
    db = SessionLocal()
    repo = SqlAlchemyAssetRepository(db)

    list_name = "Semiconductores Test List"
    list_desc = "Chips y semiconductores para pruebas"
    ticker = "NVDA"

    try:
        # 1. Asegurar que existe el asset "NVDA" en la DB para las pruebas
        asset = db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
        if not asset:
            asset = DBAsset(ticker=ticker, company_name="NVIDIA Corporation", data={})
            db.add(asset)
            db.commit()

        # 2. Crear una nueva lista personalizada
        new_list = repo.create_company_list(name=list_name, description=list_desc)
        assert new_list is not None
        assert new_list.name == list_name
        assert new_list.description == list_desc
        list_id = new_list.id

        # 3. Recuperar listas y verificar existencia
        all_lists = repo.get_company_lists()
        assert any(l.id == list_id for l in all_lists)

        list_by_id = repo.get_company_list_by_id(list_id)
        assert list_by_id is not None
        assert list_by_id.name == list_name

        list_by_name = repo.get_company_list_by_name(list_name)
        assert list_by_name is not None
        assert list_by_name.id == list_id

        # 4. Añadir ticker a la lista
        added = repo.add_ticker_to_list(list_id, ticker)
        assert added is True

        # Verificar que el asset está en la lista
        db.refresh(list_by_id)
        assert any(a.ticker == ticker for a in list_by_id.assets)

        # Verificar que la lista se asocia con el ticker
        ticker_lists = repo.get_lists_for_ticker(ticker)
        assert any(l.id == list_id for l in ticker_lists)

        # Intentar añadir duplicado (debe retornar False o comportarse correctamente)
        re_added = repo.add_ticker_to_list(list_id, ticker)
        assert re_added is False

        # 5. Quitar ticker de la lista
        removed = repo.remove_ticker_from_list(list_id, ticker)
        assert removed is True

        # Verificar que ya no está asociado
        db.refresh(list_by_id)
        assert not any(a.ticker == ticker for a in list_by_id.assets)

        ticker_lists_after = repo.get_lists_for_ticker(ticker)
        assert not any(l.id == list_id for l in ticker_lists_after)

        # 6. Eliminar la lista personalizada
        deleted = repo.delete_company_list(list_id)
        assert deleted is True

        # Verificar que ya no existe
        assert repo.get_company_list_by_id(list_id) is None

    finally:
        # Limpieza (por si acaso falló algún assert)
        try:
            clist = repo.get_company_list_by_name(list_name)
            if clist:
                repo.delete_company_list(clist.id)
        except Exception:
            pass
        db.close()

if __name__ == "__main__":
    test_company_lists_crud_and_associations()
    print("✅ All custom company lists repository tests passed successfully!")
