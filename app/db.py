import os
from datetime import date, datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Float, Date, Boolean, Text, UniqueConstraint, DateTime

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./budget.db")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    tz: Mapped[str] = mapped_column(String(64), default="UTC")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    daily_reminders: Mapped[bool] = mapped_column(Boolean, default=False)
    last_chat_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

class Budget(Base):
    __tablename__ = "budgets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    category: Mapped[str] = mapped_column(String(80), index=True)
    parent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    limit_amount: Mapped[float] = mapped_column(Float, default=0.0)

class WeeklyCap(Base):
    __tablename__ = "weekly_caps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    parent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cap_amount: Mapped[float] = mapped_column(Float, default=0.0)

class Freeze(Base):
    __tablename__ = "freezes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(80))
    parent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Txn(Base):
    __tablename__ = "txns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[int] = mapped_column(Integer, index=True)
    occurred_at: Mapped[date] = mapped_column(Date, index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    type: Mapped[str] = mapped_column(String(12))  # Income / Expense / Transfer
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    category: Mapped[str] = mapped_column(String(80))
    parent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
