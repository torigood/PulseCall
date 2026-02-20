from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest


@pytest.fixture()
def app_ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Import backend.main with isolated DB and mocked claude module."""
    db_path = tmp_path / "test_pulsecall.db"
    monkeypatch.setenv("PULSECALL_DB_PATH", str(db_path))
    # Force SQLite for tests — must be set BEFORE load_dotenv runs inside main.py
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    fake_claude = types.ModuleType("claude")

    def fake_respond(user_message: str, history: list[dict[str, str]], system_prompt: str) -> str:
        return f"mocked-reply:{user_message}"

    def fake_process_transcript(transcript: list[dict[str, str]], escalation_keywords: list[str]) -> dict[str, Any]:
        text = " ".join(t.get("content", "") for t in transcript).lower()
        detected = [kw for kw in escalation_keywords if kw.lower() in text]
        return {
            "summary": "Mock transcript summary",
            "sentiment_score": 2 if detected else 4,
            "detected_flags": detected,
            "recommended_action": "Escalate" if detected else "No escalation required",
        }

    fake_claude.respond = fake_respond
    fake_claude.process_transcript = fake_process_transcript
    monkeypatch.setitem(sys.modules, "claude", fake_claude)

    for module_name in ("main", "database", "auth", "scheduler", "notifier"):
        sys.modules.pop(module_name, None)

    main = importlib.import_module("main")

    # Initialize DB tables and seed example data.
    main.init_db()
    main.seed_example_data()

    return main


@pytest.fixture()
def auth_headers(app_ctx):
    """Create a test admin doctor and return Bearer auth headers."""
    import auth as auth_module
    from database import Doctor, SessionLocal

    db = SessionLocal()
    try:
        doctor_id = "doc_test_admin"
        existing = db.query(Doctor).filter(Doctor.id == doctor_id).first()
        if not existing:
            doctor = Doctor(
                id=doctor_id,
                email="test@hospital.com",
                password_hash=auth_module.hash_password("testpass"),
                name="Test Admin",
                role="admin",
            )
            db.add(doctor)
            db.commit()
    finally:
        db.close()

    token = auth_module.create_access_token(doctor_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def api_request(app_ctx, auth_headers):
    """Synchronous request helper for FastAPI app (auth headers included by default)."""

    def _request(method: str, path: str, headers: dict | None = None, **kwargs) -> httpx.Response:
        merged_headers = {**auth_headers, **(headers or {})}

        async def _run() -> httpx.Response:
            transport = httpx.ASGITransport(app=app_ctx.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, headers=merged_headers, **kwargs)

        return asyncio.run(_run())

    return _request
