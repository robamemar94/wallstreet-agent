import os
import sys
import json

# Agregar el directorio raíz al path para poder importar módulos de la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.db.database import SessionLocal, engine, Base
from app.infrastructure.db.models import DBAsset, DBPortfolioItem, DBLedgerEntry, DBTransaction, DBSetting, DBAlerts
from app.domain.models import TransactionType
import storage
from app.infrastructure.config import PORTFOLIO_FILE, CLOSED_PORTFOLIO_FILE, LEDGER_FILE, MANUAL_TX_FILE

def migrate():
    print("Iniciando migración de JSON a SQLite...")
    
    # Crear las tablas
    print("Creando tablas en la base de datos...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        # 1. Migrar Tickers (Assets)
        print("Migrando Tickers (Assets)...")
        tickers = storage.get_all_analyzed_tickers()
        for t in tickers:
            data = storage.load_data(t)
            company_name = data.get("company_name", t)
            asset = db.query(DBAsset).filter(DBAsset.ticker == t).first()
            if not asset:
                asset = DBAsset(ticker=t, company_name=company_name, data=data)
                db.add(asset)
            else:
                asset.data = data
                asset.company_name = company_name
        db.commit()

        # 2. Migrar Portafolio Activo
        print("Migrando Portafolio Activo...")
        active_portfolio = []
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                active_portfolio = json.load(f)
        
        # ELIMINAR TODO ANTES DE EMPEZAR para evitar conflictos UNIQUE si un ticker pasa de activo a cerrado o viceversa
        db.query(DBPortfolioItem).delete()
        
        for p in active_portfolio:
            ticker = p["ticker"]
            asset = db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
            if not asset:
                asset = DBAsset(ticker=ticker)
                db.add(asset)
                db.commit()
            
            db_item = DBPortfolioItem(
                ticker=ticker,
                is_closed=False,
                shares=p.get("shares", 0.0),
                average_price=p.get("average_price", 0.0),
                total_cost=p.get("total_cost", 0.0),
                realized_pnl=p.get("realized_pnl", 0.0),
                total_shares_bought=p.get("total_shares_bought", 0.0),
                total_shares_sold=p.get("total_shares_sold", 0.0),
                total_sell_revenue=p.get("total_sell_revenue", 0.0),
                currency_code=p.get("currency_code", "USD"),
                dividends_collected=p.get("dividends_collected", 0.0),
                average_sell_price=p.get("average_sell_price")
            )
            db.add(db_item)
        db.commit()

        # 3. Migrar Portafolio Cerrado
        print("Migrando Portafolio Cerrado...")
        closed_portfolio = []
        if os.path.exists(CLOSED_PORTFOLIO_FILE):
            with open(CLOSED_PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                closed_portfolio = json.load(f)
        
        for p in closed_portfolio:
            ticker = p["ticker"]
            asset = db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
            if not asset:
                asset = DBAsset(ticker=ticker)
                db.add(asset)
                db.commit()
            
            db_item = DBPortfolioItem(
                ticker=ticker,
                is_closed=True,
                shares=p.get("shares", 0.0),
                average_price=p.get("average_price", 0.0),
                total_cost=p.get("total_cost", 0.0),
                realized_pnl=p.get("realized_pnl", 0.0),
                total_shares_bought=p.get("total_shares_bought", 0.0),
                total_shares_sold=p.get("total_shares_sold", 0.0),
                total_sell_revenue=p.get("total_sell_revenue", 0.0),
                currency_code=p.get("currency_code", "USD"),
                dividends_collected=p.get("dividends_collected", 0.0),
                average_sell_price=p.get("average_sell_price")
            )
            db.add(db_item)
        db.commit()

        # 4. Migrar Transacciones Manuales
        print("Migrando Transacciones Manuales...")
        transactions = []
        if os.path.exists(MANUAL_TX_FILE):
            with open(MANUAL_TX_FILE, "r", encoding="utf-8") as f:
                transactions = json.load(f)
        
        db.query(DBTransaction).delete()
        for t in transactions:
            ticker = t["ticker"]
            asset = db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
            if not asset:
                asset = DBAsset(ticker=ticker)
                db.add(asset)
                db.commit()
            
            db_tx = DBTransaction(
                ticker=ticker,
                date=t["date"],
                type=TransactionType(t["type"]),
                shares=t["shares"],
                price=t["price"],
                currency=t.get("currency", "USD"),
                total=t.get("total")
            )
            db.add(db_tx)
        db.commit()

        # 5. Migrar Ledger
        print("Migrando Ledger...")
        ledger = {}
        if os.path.exists(LEDGER_FILE):
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        
        db.query(DBLedgerEntry).delete()
        for ticker, entries in ledger.items():
            asset = db.query(DBAsset).filter(DBAsset.ticker == ticker).first()
            if not asset:
                asset = DBAsset(ticker=ticker)
                db.add(asset)
                db.commit()
            
            for e in entries:
                db_e = DBLedgerEntry(
                    ticker=ticker,
                    date=e["date"],
                    type=TransactionType(e["type"]),
                    shares=e["shares"],
                    price=e["price"],
                    total=e["total"]
                )
                db.add(db_e)
        db.commit()

        # 6. Migrar Configuraciones
        print("Migrando Settings...")
        settings_data = storage.load_settings()
        db.query(DBSetting).delete()
        for key, value in settings_data.items():
            db_setting = DBSetting(key=key, value=value)
            db.add(db_setting)
        db.commit()

        # 7. Migrar Alertas
        print("Migrando Alertas...")
        alerts_data = storage.load_alerts()
        db.query(DBAlerts).delete()
        db_alert = DBAlerts(id=1, data=alerts_data)
        db.add(db_alert)
        db.commit()
        
        print("¡Migración completada con éxito!")

    except Exception as e:
        print(f"Error durante la migración: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
