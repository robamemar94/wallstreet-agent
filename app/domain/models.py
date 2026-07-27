from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum
from datetime import date, datetime

class TransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    SPLIT = "SPLIT"

class Transaction(BaseModel):
    date: str
    type: TransactionType
    ticker: str
    shares: float
    price: float
    currency: str = "USD"
    total: Optional[float] = None
    fee: float = 0.0
    fx_rate_at_purchase: Optional[float] = None  # EUR por unidad de divisa
    broker: str = "IBKR"                         # "DEGIRO", "IBKR" o "AUTO"

class LedgerEntry(BaseModel):
    date: str
    type: TransactionType
    shares: float
    price: float
    total: float
    fee: float = 0.0
    fx_rate_at_purchase: Optional[float] = None
    broker: str = "IBKR"

class PortfolioItem(BaseModel):
    ticker: str
    shares: float = 0.0
    average_price: float = 0.0
    total_cost: float = 0.0
    realized_pnl: float = 0.0
    total_shares_bought: float = 0.0
    total_shares_sold: float = 0.0
    total_sell_revenue: float = 0.0
    currency_code: str = "USD"
    dividends_collected: float = 0.0
    average_sell_price: Optional[float] = None
    total_fees: float = 0.0       # comisiones acumuladas en divisa nativa
    total_cost_eur: float = 0.0   # coste histórico en EUR

class AssetAnalysis(BaseModel):
    ticker: str
    data: dict

class MarketData(BaseModel):
    current_price: float
    currency: str
    forward_pe: Optional[float] = None
    historical_prices: Optional[dict] = None
