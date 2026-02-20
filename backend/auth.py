"""JWT-based doctor authentication: token issuance, validation, and auth endpoints."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import Doctor, get_db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "changeme-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def create_access_token(doctor_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": doctor_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Return doctor_id from token, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# FastAPI dependency — protects routes
# ---------------------------------------------------------------------------
def get_current_doctor(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Doctor:
    """Extract and validate the Bearer token; return the authenticated Doctor row."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    doctor_id = decode_access_token(credentials.credentials)
    if doctor_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Doctor not found")
    return doctor


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class DoctorCreate(BaseModel):
    email: str
    password: str
    name: str
    specialty: Optional[str] = None


class DoctorLogin(BaseModel):
    email: str
    password: str


class DoctorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    specialty: Optional[str]
    role: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/register", response_model=DoctorResponse, status_code=201)
def register(data: DoctorCreate, db: Session = Depends(get_db)):
    """Create a new doctor account. Email must be unique."""
    existing = db.query(Doctor).filter(Doctor.email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    doctor = Doctor(
        id=f"doc_{uuid4().hex[:12]}",
        email=data.email,
        password_hash=hash_password(data.password),
        name=data.name,
        specialty=data.specialty,
        role="doctor",
    )
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor


@router.post("/login", response_model=TokenResponse)
def login(data: DoctorLogin, db: Session = Depends(get_db)):
    """Authenticate and return a JWT access token."""
    doctor = db.query(Doctor).filter(Doctor.email == data.email).first()
    if doctor is None or not verify_password(data.password, doctor.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    token = create_access_token(doctor.id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=DoctorResponse)
def me(current_doctor: Doctor = Depends(get_current_doctor)):
    """Return the currently authenticated doctor's profile."""
    return current_doctor
