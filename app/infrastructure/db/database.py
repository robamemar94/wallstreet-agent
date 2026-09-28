import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./data/alpha_flow.db"

# Asegurar que el directorio data exista
os.makedirs("data", exist_ok=True)

# Copia de seguridad diaria: una copia fechada por día en data/backups/ (se conservan las últimas BACKUP_KEEP).
# Antes se sobrescribía un único .bak en cada arranque, así que un fallo se copiaba encima de la copia buena.
BACKUP_DIR = "./data/backups"
BACKUP_KEEP = 14

def make_rolling_backup():
    import shutil
    from datetime import date
    db_path = "./data/alpha_flow.db"
    if not (os.path.exists(db_path) and os.path.getsize(db_path) > 10000):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    today_path = os.path.join(BACKUP_DIR, f"alpha_flow-{date.today():%Y-%m-%d}.db")
    if os.path.exists(today_path):
        return
    try:
        shutil.copy2(db_path, today_path)
        print(f"Auto-backup: copia diaria en {today_path}")
        backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("alpha_flow-") and f.endswith(".db"))
        for old in backups[:-BACKUP_KEEP]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except Exception as e:
        print("Auto-backup error:", e)

make_rolling_backup()

# Autocorregir esquema agregando columna 'broker' si no existe
def check_and_add_broker_column():
    import sqlite3
    db_path = "./data/alpha_flow.db"
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            # Comprobar si existe la tabla transactions
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'")
            if cursor.fetchone():
                cursor.execute("PRAGMA table_info(transactions)")
                columns = [col[1] for col in cursor.fetchall()]
                if 'broker' not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN broker VARCHAR DEFAULT 'AUTO'")
                    conn.commit()
                    print("Auto-corrección: añadida columna 'broker' a tabla 'transactions'.")
        except Exception as e:
            print("Auto-corrección error al alterar tabla 'transactions':", e)
        finally:
            conn.close()

check_and_add_broker_column()

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
