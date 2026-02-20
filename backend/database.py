"""Database models and session management via SQLAlchemy."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from models import CallState, PatientStatus, SeverityGrade

DB_PATH = os.getenv("PULSECALL_DB_PATH", "pulsecall.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# check_same_thread is SQLite-only — omit for PostgreSQL
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    """Core patient record — replaces both the old UserRecord and in-memory campaign store."""

    __tablename__ = "patients"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(String, nullable=True)
    primary_diagnosis = Column(String, nullable=True)
    surgical_history = Column(Text, nullable=True)      # JSON list
    medications = Column(Text, nullable=True)            # JSON list
    allergies = Column(Text, nullable=True)              # JSON list
    vital_signs = Column(Text, nullable=True)            # JSON dict
    post_op_instructions = Column(Text, nullable=True)   # JSON list
    emergency_contact = Column(Text, nullable=True)      # JSON dict
    previous_calls_context = Column(Text, nullable=True) # JSON list (historical context for LLM)
    next_appointment = Column(String, nullable=True)

    severity_grade = Column(
        Enum(SeverityGrade), nullable=True, default=None
    )
    status = Column(
        Enum(PatientStatus), nullable=False, default=PatientStatus.PENDING_REVIEW
    )

    # AI agent configuration (per-patient)
    agent_persona = Column(String, nullable=True)
    conversation_goal = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    escalation_keywords = Column(Text, nullable=True)    # JSON list
    voice_id = Column(String, default="rachel")

    doctor_id = Column(String, nullable=True)  # FK added in Phase 1-C
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ConversationRecord(Base):
    """Persistent conversation storage — replaces the in-memory conversations dict."""

    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="active")  # active / inactive
    history = Column(Text, nullable=True)  # JSON list of {role, content}
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class CallRecord(Base):
    """Record of each AI phone call to a patient."""

    __tablename__ = "call_history"

    id = Column(String, primary_key=True)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False, index=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=True)
    state = Column(Enum(CallState), nullable=False, default=CallState.PENDING)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    triage_classification = Column(String, nullable=True)
    triage_reason = Column(String, nullable=True)
    transcript_text = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    sentiment_score = Column(Integer, nullable=True)
    detected_flags = Column(Text, nullable=True)         # JSON list
    recommended_action = Column(Text, nullable=True)
    escalation_reason = Column(Text, nullable=True)
    smallest_call_id = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class EscalationRecord(Base):
    """Escalation alerts requiring doctor attention."""

    __tablename__ = "escalations"

    id = Column(String, primary_key=True)
    call_id = Column(String, ForeignKey("call_history.id"), nullable=True)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False, index=True)
    priority = Column(String, nullable=False, default="medium")  # high / medium / low
    status = Column(String, nullable=False, default="open")      # open / acknowledged
    reason = Column(Text, nullable=True)
    detected_flags = Column(Text, nullable=True)  # JSON list
    created_at = Column(DateTime, default=_utcnow)
    acknowledged_at = Column(DateTime, nullable=True)


class Doctor(Base):
    """Authenticated doctor / admin account."""

    __tablename__ = "doctors"

    id = Column(String, primary_key=True)                            # "doc_xxxx"
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    specialty = Column(String, nullable=True)
    role = Column(String, nullable=False, default="doctor")          # "doctor" | "admin"
    created_at = Column(DateTime, default=_utcnow)


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yield a database session and close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
