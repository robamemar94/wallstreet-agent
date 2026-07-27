import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./data/alpha_flow.db"

# Asegurar que el directorio data exista
os.makedirs("data", exist_ok=True)

# Crear copia de seguridad automática rodante al iniciar para evitar pérdida de datos
def make_rolling_backup():
    import shutil
    db_path = "./data/alpha_flow.db"
    backup_path = "./data/alpha_flow.db.bak"
    if os.path.exists(db_path) and os.path.getsize(db_path) > 10000:
        try:
            shutil.copy2(db_path, backup_path)
            print("Auto-backup: Creada copia de seguridad rodante de la base de datos.")
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
