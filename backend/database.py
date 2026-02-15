"""SQLite database for call history and retry tracking via SQLAlchemy."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from models import CallState

DB_PATH = os.getenv("PULSECALL_DB_PATH", "pulsecall.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=True)
    campaign_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CallRecord(Base):
    __tablename__ = "call_history"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    campaign_id = Column(String, nullable=True)
    state = Column(
        Enum(CallState),
        nullable=False,
        default=CallState.PENDING,
    )
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    triage_classification = Column(String, nullable=True)
    triage_reason = Column(String, nullable=True)
    transcript_text = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    sentiment_score = Column(Integer, nullable=True)
    detected_flags = Column(Text, nullable=True)  # JSON-encoded list
    recommended_action = Column(Text, nullable=True)
    escalation_reason = Column(Text, nullable=True)
    smallest_call_id = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """Yield a database session."""
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise
