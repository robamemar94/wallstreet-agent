from sqlalchemy import Column, Integer, String, Float, Boolean, JSON, Enum as SQLEnum, ForeignKey, Table
from sqlalchemy.orm import relationship
from app.infrastructure.db.database import Base
from app.domain.models import TransactionType

class DBAsset(Base):
    __tablename__ = "assets"
    
    ticker = Column(String, primary_key=True, index=True)
    company_name = Column(String, nullable=True)
    data = Column(JSON, default=dict) # Almacena el diccionario con los datos fundamentales/análisis
    is_favorite = Column(Boolean, default=False)
    
    transactions = relationship("DBTransaction", back_populates="asset", cascade="all, delete-orphan")
    ledger_entries = relationship("DBLedgerEntry", back_populates="asset", cascade="all, delete-orphan")
    portfolio_item = relationship("DBPortfolioItem", back_populates="asset", uselist=False, cascade="all, delete-orphan")

class DBTransaction(Base):
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ticker = Column(String, ForeignKey("assets.ticker"), nullable=False)
    date = Column(String, nullable=False)
    type = Column(SQLEnum(TransactionType), nullable=False)
    shares = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    total = Column(Float, nullable=True)
    fee = Column(Float, nullable=True, default=0.0)
    fx_rate_at_purchase = Column(Float, nullable=True)  # EUR por unidad de divisa (ej: 0.92 para USD)
    broker = Column(String, default="IBKR", nullable=True)

    asset = relationship("DBAsset", back_populates="transactions")

class DBLedgerEntry(Base):
    __tablename__ = "ledger_entries"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ticker = Column(String, ForeignKey("assets.ticker"), nullable=False)
    date = Column(String, nullable=False)
    type = Column(SQLEnum(TransactionType), nullable=False)
    shares = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    
    asset = relationship("DBAsset", back_populates="ledger_entries")

class DBPortfolioItem(Base):
    __tablename__ = "portfolio_items"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ticker = Column(String, ForeignKey("assets.ticker"), nullable=False, unique=True)
    is_closed = Column(Boolean, default=False)
    
    shares = Column(Float, default=0.0)
    average_price = Column(Float, default=0.0)
    total_cost = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    total_shares_bought = Column(Float, default=0.0)
    total_shares_sold = Column(Float, default=0.0)
    total_sell_revenue = Column(Float, default=0.0)
    currency_code = Column(String, default="USD")
    dividends_collected = Column(Float, default=0.0)
    average_sell_price = Column(Float, nullable=True)
    total_fees = Column(Float, default=0.0)        # comisiones acumuladas en divisa nativa
    total_cost_eur = Column(Float, default=0.0)    # coste histórico en EUR (FX del momento de compra)
    
    asset = relationship("DBAsset", back_populates="portfolio_item")

class DBSetting(Base):
    __tablename__ = "settings"
    
    key = Column(String, primary_key=True, index=True)
    value = Column(JSON, nullable=False)

class DBAlerts(Base):
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True, default=1)
    data = Column(JSON, nullable=False)

class DBTask(Base):
    __tablename__ = "analysis_tasks"
    
    task_id = Column(String, primary_key=True, index=True)
    ticker = Column(String, ForeignKey("assets.ticker"), nullable=False)
    report_type = Column(String, nullable=False) # 'fin', 'cap', 'moat', 'verdict', 'val'
    status = Column(String, default="processing") # 'processing', 'success', 'error'
    result_html = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    is_legacy = Column(Boolean, default=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    
    asset = relationship("DBAsset")

# Tabla de asociación Muchos a Muchos para listas personalizadas
company_list_association = Table(
    "company_list_associations",
    Base.metadata,
    Column("list_id", Integer, ForeignKey("company_lists.id", ondelete="CASCADE"), primary_key=True),
    Column("ticker", String, ForeignKey("assets.ticker", ondelete="CASCADE"), primary_key=True)
)

class DBCompanyList(Base):
    __tablename__ = "company_lists"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(String, nullable=True)
    
    assets = relationship("DBAsset", secondary=company_list_association, backref="custom_lists")
