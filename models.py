from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import JSON, Float, String, BigInteger, Integer, DateTime
from datetime import datetime

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class User(db.Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str] = mapped_column(String, nullable=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    playthrough_required: Mapped[float] = mapped_column(Float, default=0.0)
    last_bonus_claim: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    total_wagered: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    games_won: Mapped[int] = mapped_column(Integer, default=0)
    win_streak: Mapped[int] = mapped_column(Integer, default=0)
    best_win_streak: Mapped[int] = mapped_column(Integer, default=0)
    total_won: Mapped[float] = mapped_column(Float, default=0.0)
    rakeback_balance: Mapped[float] = mapped_column(Float, default=0.0)
    wagered_since_last_withdrawal: Mapped[float] = mapped_column(Float, default=0.0)
    first_wager_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    referral_code: Mapped[str] = mapped_column(String, nullable=True)
    referred_by: Mapped[int] = mapped_column(BigInteger, nullable=True)
    referral_count: Mapped[int] = mapped_column(Integer, default=0)
    referral_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    unclaimed_referral_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    achievements: Mapped[dict] = mapped_column(JSON, default=dict)

class Game(db.Model):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Transaction(db.Model):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String)
    amount: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class GlobalState(db.Model):
    __tablename__ = "global_state"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
