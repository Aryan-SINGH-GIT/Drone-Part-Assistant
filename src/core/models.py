from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class StockStatus(str, Enum):
    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PRE_ORDER = "PRE_ORDER"
    DISCONTINUED = "DISCONTINUED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, val: str) -> "StockStatus":
        if not val:
            return cls.UNKNOWN
        val_clean = val.strip().upper().replace(" ", "_").replace("-", "_")
        for member in cls:
            if member.value == val_clean or member.name == val_clean:
                return member
        if "IN" in val_clean and "OUT" not in val_clean:
            return cls.IN_STOCK
        if "OUT" in val_clean or "SOLD" in val_clean:
            return cls.OUT_OF_STOCK
        if "PRE" in val_clean or "BACKORDER" in val_clean:
            return cls.PRE_ORDER
        return cls.UNKNOWN


class Retailer(Base):
    __tablename__ = "retailers"

    id = Column(String(50), primary_key=True)  # e.g. "robu", "thinkrobotics", "zbotic", "evelta"
    name = Column(String(100), nullable=False)
    base_url = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    products = relationship("Product", back_populates="retailer", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    retailer_id = Column(String(50), ForeignKey("retailers.id"), nullable=False)
    title = Column(String(255), nullable=False)
    url = Column(String(500), unique=True, nullable=False)
    category = Column(String(100), nullable=True)  # flight_controller, companion_computer, hat_carrier, etc.
    last_price = Column(Float, nullable=True)
    currency = Column(String(10), default="INR")
    last_status = Column(String(50), default=StockStatus.UNKNOWN.value, nullable=False)
    pre_order_eta = Column(String(255), nullable=True)
    content_hash = Column(String(64), nullable=True)  # SHA-256 hash of extracted text to skip LLM calls
    last_checked_at = Column(DateTime, nullable=True)
    last_alerted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    retailer = relationship("Retailer", back_populates="products")
    watchlist_entries = relationship("WatchlistItem", back_populates="product", cascade="all, delete-orphan")
    history_entries = relationship("ProductHistory", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_product_category", "category"),
        Index("idx_product_status", "last_status"),
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False)  # Telegram Chat ID or user identifier
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    target_price = Column(Float, nullable=True)  # Optional threshold
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    product = relationship("Product", back_populates="watchlist_entries")

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_user_product"),
        Index("idx_watchlist_user", "user_id"),
    )


class ProductHistory(Base):
    __tablename__ = "product_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    price = Column(Float, nullable=True)
    status = Column(String(50), nullable=False)
    pre_order_eta = Column(String(255), nullable=True)
    content_hash = Column(String(64), nullable=True)
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    product = relationship("Product", back_populates="history_entries")

    __table_args__ = (
        Index("idx_history_product_time", "product_id", "recorded_at"),
    )
