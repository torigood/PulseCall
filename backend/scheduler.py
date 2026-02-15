"""Outbound call scheduling via APScheduler and Smallest.ai API."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database import CallRecord, SessionLocal, UserRecord, init_db
from models import CallState, OutboundCallRequest

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SMALLEST_API_KEY = os.getenv("SMALLEST_API_KEY", "")
SMALLEST_API_BASE = os.getenv("SMALLEST_API_BASE", "https://api.smallest.ai/v1")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000")
CHECK_INTERVAL_HOURS = float(os.getenv("CHECK_INTERVAL_HOURS", "2"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
DEFAULT_SYSTEM_PROMPT = (
    "You are a caring wellness check-in agent. Your tone is warm, empathetic, "
    "and concise. Ask one question at a time. Check on the person's wellbeing, "
    "any pain or discomfort, and whether they need assistance."
)

scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Smallest.ai outbound call
# ---------------------------------------------------------------------------
async def place_outbound_call(request: OutboundCallRequest) -> str | None:
    """Place an outbound call via Smallest.ai API.

    Returns the Smallest.ai call_id on success, or None on failure.
    """
    if not SMALLEST_API_KEY:
        logger.warning("[MOCK CALL] Would call %s for user %s", request.phone_number, request.user_name)
        return f"mock_call_{uuid4().hex[:8]}"

    payload = {
        "phone_number": request.phone_number,
        "system_prompt": request.system_prompt or DEFAULT_SYSTEM_PROMPT,
        "voice_id": request.voice_id,
        "webhook_url": f"{WEBHOOK_BASE_URL}/webhooks/smallest/post-call",
        "analytics_webhook_url": f"{WEBHOOK_BASE_URL}/webhooks/smallest/analytics",
        "metadata": {
            "user_id": request.user_id,
            "user_name": request.user_name,
            "campaign_id": request.campaign_id or "",
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{SMALLEST_API_BASE}/calls/outbound",
                json=payload,
                headers={
                    "Authorization": f"Bearer {SMALLEST_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            call_id = data.get("call_id", data.get("id"))
            logger.info("Outbound call placed: smallest_call_id=%s user=%s", call_id, request.user_id)
            return call_id
        except httpx.HTTPStatusError as e:
            logger.error("Smallest.ai API error %s: %s", e.response.status_code, e.response.text)
            return None
        except Exception:
            logger.exception("Failed to place outbound call for user %s", request.user_id)
            return None


# ---------------------------------------------------------------------------
# Scheduled job: process pending calls
# ---------------------------------------------------------------------------
async def process_pending_calls() -> None:
    """Check for users due for a call and place outbound calls."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Find users who need a call: either no call record, or retry is due
        users = db.query(UserRecord).all()

        for user in users:
            # Get the latest call record for this user
            latest_call = (
                db.query(CallRecord)
                .filter(CallRecord.user_id == user.id)
                .order_by(CallRecord.created_at.desc())
                .first()
            )

            should_call = False

            if latest_call is None:
                # Never called before
                should_call = True
            elif latest_call.state == CallState.COMPLETED:
                # Completed — check if enough time has passed for next check-in
                if latest_call.ended_at:
                    next_due = latest_call.ended_at + timedelta(hours=CHECK_INTERVAL_HOURS)
                    should_call = now >= next_due
            elif latest_call.state in (CallState.BUSY_RETRY, CallState.SILENT_RETRY):
                # Retry scheduled — check if retry time has arrived
                if latest_call.next_retry_at and now >= latest_call.next_retry_at:
                    if latest_call.retry_count < latest_call.max_retries:
                        should_call = True
                    else:
                        # Max retries exceeded — escalate
                        latest_call.state = CallState.ESCALATED
                        latest_call.escalation_reason = f"Max retries ({latest_call.max_retries}) exceeded"
                        db.commit()
                        logger.warning("User %s exceeded max retries — escalated", user.id)
            elif latest_call.state in (CallState.ESCALATED, CallState.PENDING):
                # Already escalated or pending — skip
                pass

            if not should_call:
                continue

            # Create a new call record
            call_id = f"call_{uuid4().hex[:10]}"
            call_record = CallRecord(
                id=call_id,
                user_id=user.id,
                campaign_id=user.campaign_id,
                state=CallState.PENDING,
                retry_count=0 if latest_call is None else latest_call.retry_count,
                max_retries=MAX_RETRIES,
                started_at=now,
                created_at=now,
            )
            db.add(call_record)
            db.commit()

            # Place the call
            request = OutboundCallRequest(
                user_id=user.id,
                user_name=user.name,
                phone_number=user.phone,
                campaign_id=user.campaign_id,
                system_prompt=DEFAULT_SYSTEM_PROMPT,
            )
            smallest_call_id = await place_outbound_call(request)

            if smallest_call_id:
                call_record.smallest_call_id = smallest_call_id
                db.commit()
                logger.info("Call queued: id=%s user=%s smallest_id=%s", call_id, user.id, smallest_call_id)
            else:
                call_record.state = CallState.BUSY_RETRY
                call_record.next_retry_at = now + timedelta(minutes=5)
                db.commit()
                logger.warning("Call placement failed for user %s — will retry", user.id)

    except Exception:
        logger.exception("Error in process_pending_calls")
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Schedule a retry for a specific call
# ---------------------------------------------------------------------------
def schedule_retry(call_record: CallRecord, delay_minutes: int, db) -> None:
    """Update a call record to schedule a retry after the given delay."""
    now = datetime.now(timezone.utc)
    call_record.retry_count += 1
    call_record.next_retry_at = now + timedelta(minutes=delay_minutes)

    if call_record.retry_count >= call_record.max_retries:
        call_record.state = CallState.ESCALATED
        call_record.escalation_reason = f"Max retries ({call_record.max_retries}) exceeded after triage: {call_record.triage_classification}"
        logger.warning("Call %s max retries reached — escalating", call_record.id)
    else:
        call_record.state = CallState.SILENT_RETRY if "SILENCE" in (call_record.triage_classification or "") else CallState.BUSY_RETRY
        logger.info("Call %s retry #%d scheduled in %d minutes", call_record.id, call_record.retry_count, delay_minutes)

    db.commit()


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------
def start_scheduler() -> None:
    """Start the APScheduler with the periodic check-in job."""
    init_db()
    scheduler.add_job(
        process_pending_calls,
        "interval",
        hours=CHECK_INTERVAL_HOURS,
        id="pulsecall_checkin",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),  # first run shortly after startup
    )
    scheduler.start()
    logger.info("Scheduler started — check-in interval: %.1f hours", CHECK_INTERVAL_HOURS)


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
