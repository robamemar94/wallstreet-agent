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


# --- Seguimiento de tesis de inversión ---

class DBThesis(Base):
    __tablename__ = "theses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ticker = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=False)
    summary = Column(String, nullable=True)
    status = Column(String, default="ACTIVA")        # 'ACTIVA', 'EN_REVISION', 'CERRADA'
    verdict = Column(String, default="INTACTA")      # 'REFORZADA', 'INTACTA', 'DEBILITADA', 'ROTA'
    version = Column(String, nullable=True)
    reference_price = Column(Float, nullable=True)
    reference_date = Column(String, nullable=True)
    source_document = Column(String, nullable=True)
    extra = Column(JSON, default=dict)               # ratings, valoración, checklist, reglas
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    pillars = relationship("DBThesisPillar", back_populates="thesis", cascade="all, delete-orphan", order_by="DBThesisPillar.position")
    kpis = relationship("DBThesisKPI", back_populates="thesis", cascade="all, delete-orphan", order_by="DBThesisKPI.position")
    events = relationship("DBThesisEvent", back_populates="thesis", cascade="all, delete-orphan")

class DBThesisPillar(Base):
    __tablename__ = "thesis_pillars"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    thesis_id = Column(Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False)
    key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    weight = Column(Float, default=1.0)              # 0 = informativo, no puntúa
    position = Column(Integer, default=0)

    thesis = relationship("DBThesis", back_populates="pillars")
    kpis = relationship("DBThesisKPI", back_populates="pillar", order_by="DBThesisKPI.position")

class DBThesisKPI(Base):
    __tablename__ = "thesis_kpis"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    thesis_id = Column(Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False)
    pillar_id = Column(Integer, ForeignKey("thesis_pillars.id", ondelete="CASCADE"), nullable=False)
    key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    how_to_measure = Column(String, nullable=True)
    baseline = Column(String, nullable=True)
    kind = Column(String, default="numeric")         # 'numeric' (semáforo automático) | 'qualitative' (semáforo manual)
    unit = Column(String, nullable=True)
    direction = Column(String, default="higher")     # 'higher' | 'lower' es mejor
    green_threshold = Column(Float, nullable=True)
    red_threshold = Column(Float, nullable=True)
    green_text = Column(String, nullable=True)
    amber_text = Column(String, nullable=True)
    red_text = Column(String, nullable=True)
    frequency = Column(String, nullable=True)
    source = Column(String, nullable=True)
    auto_metric = Column(String, nullable=True)      # métrica calculable desde yfinance
    weight = Column(Float, default=1.0)
    is_kill_switch = Column(Boolean, default=False)
    kill_rule = Column(String, nullable=True)
    position = Column(Integer, default=0)

    thesis = relationship("DBThesis", back_populates="kpis")
    pillar = relationship("DBThesisPillar", back_populates="kpis")
    observations = relationship("DBKPIObservation", back_populates="kpi", cascade="all, delete-orphan", order_by="DBKPIObservation.date")

class DBKPIObservation(Base):
    __tablename__ = "kpi_observations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    kpi_id = Column(Integer, ForeignKey("thesis_kpis.id", ondelete="CASCADE"), nullable=False, index=True)
    period = Column(String, nullable=False)          # ej: 'Q2 FY27'
    date = Column(String, nullable=False)            # ISO, para ordenar
    value = Column(Float, nullable=True)
    status = Column(String, nullable=False)          # 'green' | 'amber' | 'red'
    note = Column(String, nullable=True)
    source = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

    kpi = relationship("DBThesisKPI", back_populates="observations")

class DBThesisEvent(Base):
    __tablename__ = "thesis_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    thesis_id = Column(Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(String, nullable=False)
    type = Column(String, nullable=False)            # 'earnings', 'news', 'verdict', 'decision', 'note', 'thesis'
    title = Column(String, nullable=False)
    body = Column(String, nullable=True)
    impact = Column(String, nullable=True)           # 'positive', 'negative', 'neutral'
    created_at = Column(String, nullable=False)

    thesis = relationship("DBThesis", back_populates="events")

class DBThesisNews(Base):
    __tablename__ = "thesis_news"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    thesis_id = Column(Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False, index=True)
    found_at = Column(String, nullable=False)        # cuándo lo detectó el radar
    date = Column(String, nullable=True)             # fecha de la noticia
    title = Column(String, nullable=False)
    url = Column(String, nullable=True)
    step = Column(Integer, nullable=True)            # paso del cuadro de mando afectado (1..n)
    step_name = Column(String, nullable=True)
    impact = Column(String, nullable=True)           # 'positive', 'negative', 'neutral'
    materiality = Column(Integer, nullable=False)    # 4-5 (solo se guardan las muy relevantes)
    summary = Column(String, nullable=True)
    seen = Column(Boolean, default=False)
    in_timeline = Column(Boolean, default=False)

    thesis = relationship("DBThesis")

class DBThesisReview(Base):
    __tablename__ = "thesis_reviews"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    thesis_id = Column(Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False, index=True)
    period = Column(String, nullable=True)           # trimestre analizado, ej. 'Q3 FY27'
    status = Column(String, nullable=False)          # 'running', 'ready', 'applied', 'discarded', 'error'
    report = Column(JSON, default=dict)              # informe completo: KPIs, call, hipótesis, propuestas, promesas…
    error = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    finished_at = Column(String, nullable=True)

    thesis = relationship("DBThesis")
