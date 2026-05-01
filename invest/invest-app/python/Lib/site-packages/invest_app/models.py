"""Data models for portfolio holdings and transactions."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AssetType(str, Enum):
    """Type of financial asset."""

    STOCK = "stock"
    ETF = "etf"
    MUTUAL_FUND = "mutual_fund"


class TransactionType(str, Enum):
    """Type of transaction."""

    BUY = "buy"
    SELL = "sell"


class Transaction(BaseModel):
    """A single buy or sell transaction."""

    type: TransactionType
    ticker: str
    shares: float = Field(gt=0)
    price_per_share: float = Field(gt=0)
    date: datetime = Field(default_factory=datetime.now)

    @property
    def total_cost(self) -> float:
        return self.shares * self.price_per_share


class Holding(BaseModel):
    """A current portfolio holding."""

    ticker: str
    shares: float = Field(ge=0)
    avg_cost_basis: float = Field(ge=0)
    asset_type: AssetType = AssetType.STOCK
    transactions: list[Transaction] = Field(default_factory=list)

    @property
    def total_invested(self) -> float:
        return self.shares * self.avg_cost_basis


class Portfolio(BaseModel):
    """The complete user portfolio."""

    name: str = "My Portfolio"
    holdings: dict[str, Holding] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
