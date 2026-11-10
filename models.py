from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user ID
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="Игрок")
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    referred_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    referrals_count: Mapped[int] = mapped_column(Integer, default=0)
    free_spins: Mapped[int] = mapped_column(Integer, default=0)
    free_spins_granted: Mapped[int] = mapped_column(Integer, default=0)
    paid_spins: Mapped[int] = mapped_column(Integer, default=0)
    quest_points: Mapped[int] = mapped_column(Integer, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NftInventory(Base):
    __tablename__ = "nft_inventory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(160))
    gift_url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)  # available/reserved/fulfilled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Spin(Base):
    __tablename__ = "spins"
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uq_spin_user_request"), Index("ix_spins_user_created", "user_id", "created_at"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    wheel_type: Mapped[str] = mapped_column(String(16))
    prize_type: Mapped[str] = mapped_column(String(16))
    prize_title: Mapped[str] = mapped_column(String(160))
    points_awarded: Mapped[int] = mapped_column(Integer, default=0)
    nft_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("nft_inventory.id"), nullable=True)
    payment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("payments.id"), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NftClaim(Base):
    __tablename__ = "nft_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    spin_id: Mapped[int] = mapped_column(Integer, ForeignKey("spins.id"), unique=True)
    nft_id: Mapped[int] = mapped_column(Integer, ForeignKey("nft_inventory.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/requested/fulfilled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskClaim(Base):
    __tablename__ = "task_claims"
    __table_args__ = (UniqueConstraint("user_id", "task_key", name="uq_task_user_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    task_key: Mapped[str] = mapped_column(String(64))
    points_awarded: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    payload: Mapped[str] = mapped_column(String(128), unique=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="XTR")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    charge_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
