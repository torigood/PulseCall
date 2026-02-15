from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from claude import respond, process_transcript
from database import CallRecord as DBCallRecord, SessionLocal, UserRecord, init_db, get_db
from models import (
    CallState,
    OutboundCallRequest,
    SmallestAIAnalyticsPayload,
    SmallestAIPostCallPayload,
    TriageClassification,
)
from notifier import send_escalation_sms
from scheduler import place_outbound_call, schedule_retry, start_scheduler, stop_scheduler
from triage import analyze_vitals

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="PulseCall MVP API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Pydantic models
# -----------------------------
class Recipient(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None


class CampaignCreate(BaseModel):
    name: str
    agent_persona: str
    conversation_goal: str
    system_prompt: str
    escalation_keywords: list[str] = Field(default_factory=list)
    recipients: list[Recipient]


class CampaignOut(CampaignCreate):
    id: str
    created_at: str


class Conversation(BaseModel):
    conversation_id: str
    campaign_id: str
    status: Literal["active", "inactive"]
    start_time: str
    end_time: str | None = None
    history: list[dict[str, str]]


class EndCallOut(BaseModel):
    call_id: str
    conversation_id: str
    campaign_id: str
    status: Literal["ended"]
    summary: str
    sentiment_score: int
    detected_flags: list[str]
    recommended_action: str
    escalation_id: str | None = None


# -----------------------------
# In-memory store
# -----------------------------
store: dict[str, dict[str, Any]] = {
    "campaigns": {},
    "conversations": {},
    "calls": {},
    "escalations": {},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def fallback_sentiment(text: str) -> int:
    negative_markers = ("angry", "upset", "cancel", "frustrated", "bad", "hate")
    lowered = text.lower()
    if any(marker in lowered for marker in negative_markers):
        return 2
    if "thank" in lowered or "great" in lowered:
        return 4
    return 3


def fallback_summary(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "No conversation content captured."
    first_user_msg = next((t["content"] for t in transcript if t["role"] == "user"), "")
    return (
        "Agent and recipient completed a simulated call. "
        f"Recipient's main concern: {first_user_msg[:120]}"
    ).strip()


def fallback_flags(transcript: list[dict[str, str]], keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    joined = " ".join(turn["content"] for turn in transcript).lower()
    return [kw for kw in keywords if kw.lower() in joined]


def recommended_action_for_flags(flags: list[str]) -> str:
    if not flags:
        return "No escalation required. Follow up in normal workflow."
    return "Escalate to a human operator within 15 minutes."


def seed_example_data() -> None:
    campaign_id = "cmp_demo_001"
    conversation_id = "conv_demo_001"
    call_id = "call_demo_001"
    escalation_id = "esc_demo_001"
    created_at = now_iso()

    campaign = {
        "id": campaign_id,
        "name": "Care Plan Renewal",
        "agent_persona": "Calm healthcare outreach specialist",
        "conversation_goal": "Confirm whether recipient wants help renewing care plan.",
        "system_prompt": "Be concise, empathetic, and clear. Ask one question at a time.",
        "escalation_keywords": ["cancel", "complaint", "lawyer", "fraud"],
        "recipients": [
            {
                "name": "Alex Johnson",
                "phone": "+1-555-0100",
                "email": "alex@example.com",
            }
        ],
        "created_at": created_at,
    }
    store["campaigns"][campaign_id] = campaign

    transcript = [
        {"role": "user", "content": "I want to cancel because this feels confusing."},
        {
            "role": "assistant",
            "content": "I hear you. I can escalate this to a specialist right now.",
        },
    ]
    call = {
        "id": call_id,
        "campaign_id": campaign_id,
        "conversation_id": conversation_id,
        "status": "ended",
        "started_at": created_at,
        "ended_at": now_iso(),
        "transcript": transcript,
        "summary": "Recipient expressed confusion and asked to cancel. Agent offered specialist escalation.",
        "sentiment_score": 2,
        "detected_flags": ["cancel"],
        "recommended_action": "Escalate to a human operator within 15 minutes.",
        "escalation_id": escalation_id,
    }
    store["calls"][call_id] = call

    escalation = {
        "id": escalation_id,
        "call_id": call_id,
        "campaign_id": campaign_id,
        "priority": "high",
        "status": "open",
        "reason": "Detected escalation keywords: cancel",
        "detected_flags": ["cancel"],
        "created_at": now_iso(),
        "acknowledged_at": None,
    }
    store["escalations"][escalation_id] = escalation


seed_example_data()

def get_campaign(campaign_id: str):
    campaign = store["campaigns"].get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign

def get_conversation(conversation_id: str): 
    conversation = store["conversations"].get(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation

def get_client_text(history: list[dict[str, str]]) -> str:
    return " ".join(d["content"] for d in history if d["role"] == "user")

# -----------------------------
# Routes
# -----------------------------

@app.get("/")
def read_root() -> dict[str, str]:
    return {"service": "PulseCall MVP API", "status": "ok"}


@app.get("/campaigns")
def list_campaigns() -> list[dict[str, Any]]:
    campaigns = list(store["campaigns"].values())
    campaigns.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return campaigns


@app.get("/campaigns/{campaign_id}")
def get_campaign_detail(campaign_id: str):
    campaign = store["campaigns"].get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@app.post("/campaigns/create", response_model=CampaignOut)
def create_campaign(payload: CampaignCreate) -> CampaignOut:
    campaign_id = f"cmp_{uuid4().hex[:10]}"
    campaign = {
        "id": campaign_id,
        **payload.model_dump(),
        "created_at": now_iso(),
    }
    store["campaigns"][campaign_id] = campaign
    return CampaignOut(**campaign)

# use Conversation model
@app.post("/campaigns/conversations/create")
def create_conversation(campaign_id: str):
    conversation_id = str(uuid4())
    started_at = now_iso()

    store["conversations"][conversation_id] = {
        "id": conversation_id,
        "campaign_id": campaign_id,
        "status": "active",
        "start_time": started_at,
        "end_time": None,
        "started_at": started_at,
        "ended_at": None,
        "history": [],
    }
    return store["conversations"][conversation_id]

@app.post("/campaigns/{campaign_id}/{conversation_id}")
def get_response(campaign_id: str, conversation_id: str, message: str):
    conversation = get_conversation(conversation_id)
    if conversation["campaign_id"] != campaign_id:
        raise HTTPException(status_code=400, detail="Conversation does not belong to campaign")
    if conversation["status"] != "active":
        raise HTTPException(status_code=400, detail="Conversation is inactive")

    campaign = store["campaigns"].get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    history = conversation["history"]

    try:
        history.append({
            "role": "user",
            "content": message
        })

        response = respond(
            user_message=message,
            history=history,
            system_prompt=campaign["system_prompt"],
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Claude response generation failed unexpectedly")
    
    # Add Claude response to the history
    history.append({
        "role": "assistant",
        "content": response
    })

    store["conversations"][conversation_id]["history"] = history
    return response    

"""
class EndCallOut(BaseModel):
    call_id: str
    conversation_id: str
    campaign_id: str
    status: Literal["ended"]
    summary: str
    sentiment_score: int
    detected_flags: list[str]
    recommended_action: str
    escalation_id: str | None = None
"""

@app.post("/campaigns/{campaign_id}/{conversation_id}/end", response_model=EndCallOut)
def end_call(campaign_id: str, conversation_id: str) -> EndCallOut:
    conversation = get_conversation(conversation_id)
    if conversation["campaign_id"] != campaign_id:
        raise HTTPException(status_code=400, detail="Conversation does not belong to campaign")
    if conversation["status"] != "active":
        raise HTTPException(status_code=400, detail="Conversation already ended")

    ended_at = now_iso()
    conversation["status"] = "inactive"
    conversation["end_time"] = ended_at
    conversation["ended_at"] = ended_at
    campaign = store["campaigns"][conversation["campaign_id"]]
    history = conversation["history"]

    try:
        result = process_transcript(history, campaign["escalation_keywords"])
        summary = result["summary"]
        sentiment_score = result["sentiment_score"]
        detected_flags = result["detected_flags"]
        recommended_action = result["recommended_action"]
    except Exception:
        full_text = get_client_text(history)
        summary = fallback_summary(history)
        sentiment_score = fallback_sentiment(full_text)
        detected_flags = fallback_flags(history, campaign["escalation_keywords"])
        recommended_action = recommended_action_for_flags(detected_flags)

    call_id = f"call_{uuid4().hex[:10]}"
    escalation_id: str | None = None

    if detected_flags:
        escalation_id = f"esc_{uuid4().hex[:10]}"
        escalation = {
            "id": escalation_id,
            "call_id": call_id,
            "campaign_id": campaign["id"],
            "priority": "high" if sentiment_score <= 2 else "medium",
            "status": "open",
            "reason": f"Detected escalation keywords: {', '.join(detected_flags)}",
            "detected_flags": detected_flags,
            "created_at": now_iso(),
            "acknowledged_at": None,
        }
        store["escalations"][escalation_id] = escalation

    call = {
        "call_id": call_id,
        "conversation_id": conversation_id,
        "campaign_id": campaign["id"],
        "status": "ended",
        "started_at": conversation["started_at"],
        "ended_at": conversation["ended_at"],
        "transcript": history,
        "summary": summary,
        "sentiment_score": sentiment_score,
        "detected_flags": detected_flags,
        "recommended_action": recommended_action,
        "escalation_id": escalation_id,
    }
    store["calls"][call_id] = call

    return EndCallOut(**call)


@app.get("/conversations")
def list_conversations() -> list[dict[str, Any]]:
    calls = list(store["conversations"].values())
    calls.sort(key=lambda c: c.get("ended_at", ""), reverse=True)
    return calls


@app.get("/calls")
def list_calls() -> list[dict[str, Any]]:
    calls = list(store["calls"].values())
    calls.sort(key=lambda c: c.get("ended_at", ""), reverse=True)
    return calls


@app.get("/calls/{call_id}")
def get_call_detail(call_id: str):
    call = store["calls"].get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@app.get("/escalations")
def list_escalations() -> list[dict[str, Any]]:
    priority_order = {"high": 0, "medium": 1, "low": 2}
    escalations = list(store["escalations"].values())
    escalations.sort(
        key=lambda e: (
            priority_order.get(e.get("priority", "low"), 3),
            e.get("created_at", ""),
        )
    )
    return escalations


@app.patch("/escalations/{escalation_id}/acknowledge")
def acknowledge_escalation(escalation_id: str):
    escalation = store["escalations"].get(escalation_id)
    if not escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")
    escalation["status"] = "acknowledged"
    escalation["acknowledged_at"] = now_iso()
    return escalation


# =====================================================================
# User management (for outbound call scheduling)
# =====================================================================
class UserCreate(BaseModel):
    name: str
    phone: str
    email: str | None = None
    campaign_id: str | None = None


@app.post("/users")
def create_user(payload: UserCreate):
    db = get_db()
    try:
        user_id = f"usr_{uuid4().hex[:10]}"
        user = UserRecord(
            id=user_id,
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
            campaign_id=payload.campaign_id,
        )
        db.add(user)
        db.commit()
        return {"id": user_id, "name": payload.name, "phone": payload.phone, "campaign_id": payload.campaign_id}
    finally:
        db.close()


@app.get("/users")
def list_users():
    db = get_db()
    try:
        users = db.query(UserRecord).all()
        return [
            {"id": u.id, "name": u.name, "phone": u.phone, "email": u.email, "campaign_id": u.campaign_id}
            for u in users
        ]
    finally:
        db.close()


@app.get("/call-history/{user_id}")
def get_user_call_history(user_id: str):
    db = get_db()
    try:
        records = (
            db.query(DBCallRecord)
            .filter(DBCallRecord.user_id == user_id)
            .order_by(DBCallRecord.created_at.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "state": r.state.value if r.state else None,
                "retry_count": r.retry_count,
                "triage_classification": r.triage_classification,
                "triage_reason": r.triage_reason,
                "summary": r.summary,
                "sentiment_score": r.sentiment_score,
                "detected_flags": json.loads(r.detected_flags) if r.detected_flags else [],
                "escalation_reason": r.escalation_reason,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    finally:
        db.close()


# =====================================================================
# Smallest.ai webhook handlers
# =====================================================================
@app.post("/webhooks/smallest/post-call")
async def webhook_post_call(payload: SmallestAIPostCallPayload):
    """Handle post-conversation webhook from Smallest.ai.

    Runs acoustic triage, updates call state, and triggers
    escalation or retry as needed.
    """
    logger.info("Post-call webhook received: call_id=%s user_id=%s status=%s", payload.call_id, payload.user_id, payload.status)

    db = get_db()
    try:
        # Find the call record by smallest_call_id
        call_record = (
            db.query(DBCallRecord)
            .filter(DBCallRecord.smallest_call_id == payload.call_id)
            .first()
        )

        if call_record is None:
            # Create one if webhook arrived before our record (race condition)
            call_record = DBCallRecord(
                id=f"call_{uuid4().hex[:10]}",
                user_id=payload.user_id,
                campaign_id=payload.campaign_id,
                state=CallState.PENDING,
                smallest_call_id=payload.call_id,
                started_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            db.add(call_record)
            db.commit()

        # Handle non-completed calls (busy, no_answer, failed)
        if payload.status in ("busy", "no_answer", "failed"):
            call_record.state = CallState.BUSY_RETRY
            call_record.triage_reason = f"Call status: {payload.status}"
            schedule_retry(call_record, delay_minutes=10, db=db)
            return {"status": "retry_scheduled", "call_id": call_record.id}

        # Run acoustic triage
        triage_result = analyze_vitals(payload)
        call_record.triage_classification = triage_result.classification.value
        call_record.triage_reason = triage_result.reason
        call_record.ended_at = datetime.now(timezone.utc)

        logger.info(
            "Triage result: call=%s classification=%s action=%s escalate=%s",
            call_record.id, triage_result.classification.value, triage_result.action, triage_result.escalate,
        )

        # Store transcript
        transcript_text = "\n".join(
            f"{seg.speaker}: {seg.text}" for seg in payload.transcript
        )
        call_record.transcript_text = transcript_text

        if triage_result.escalate:
            # IMMEDIATE ESCALATION
            call_record.state = CallState.ESCALATED
            call_record.escalation_reason = triage_result.reason
            db.commit()

            # Look up user name for the SMS
            user = db.query(UserRecord).filter(UserRecord.id == payload.user_id).first()
            user_name = user.name if user else payload.user_id

            send_escalation_sms(
                user_name=user_name,
                triage_reason=triage_result.reason,
                call_id=call_record.id,
            )

            # Also add to in-memory escalation store for dashboard
            esc_id = f"esc_{uuid4().hex[:10]}"
            store["escalations"][esc_id] = {
                "id": esc_id,
                "call_id": call_record.id,
                "campaign_id": payload.campaign_id,
                "priority": "high",
                "status": "open",
                "reason": triage_result.reason,
                "detected_flags": [triage_result.classification.value],
                "created_at": now_iso(),
                "acknowledged_at": None,
            }

            return {"status": "escalated", "call_id": call_record.id, "reason": triage_result.reason}

        elif triage_result.action == "SCHEDULE_RETRY":
            schedule_retry(call_record, delay_minutes=triage_result.retry_delay_minutes or 20, db=db)
            return {"status": "retry_scheduled", "call_id": call_record.id, "delay_minutes": triage_result.retry_delay_minutes}

        elif triage_result.action == "ANALYZE_TRANSCRIPT":
            # Speech detected — run Claude post-call analysis
            history = [
                {"role": "user" if seg.speaker == "user" else "assistant", "content": seg.text}
                for seg in payload.transcript
            ]
            try:
                result = process_transcript(history, [])
                call_record.summary = result["summary"]
                call_record.sentiment_score = result["sentiment_score"]
                call_record.detected_flags = json.dumps(result["detected_flags"])
                call_record.recommended_action = result["recommended_action"]
            except Exception:
                logger.exception("Claude post-call analysis failed for call %s", call_record.id)

            call_record.state = CallState.COMPLETED
            db.commit()

            # Check if Claude found flags that need escalation
            flags = json.loads(call_record.detected_flags) if call_record.detected_flags else []
            if flags:
                user = db.query(UserRecord).filter(UserRecord.id == payload.user_id).first()
                user_name = user.name if user else payload.user_id
                send_escalation_sms(
                    user_name=user_name,
                    triage_reason=f"Distress flags in transcript: {', '.join(flags)}",
                    call_id=call_record.id,
                )
                esc_id = f"esc_{uuid4().hex[:10]}"
                store["escalations"][esc_id] = {
                    "id": esc_id,
                    "call_id": call_record.id,
                    "campaign_id": payload.campaign_id,
                    "priority": "high" if (call_record.sentiment_score or 3) <= 2 else "medium",
                    "status": "open",
                    "reason": f"Transcript flags: {', '.join(flags)}",
                    "detected_flags": flags,
                    "created_at": now_iso(),
                    "acknowledged_at": None,
                }

            return {"status": "completed", "call_id": call_record.id, "summary": call_record.summary}

        # Default: mark completed
        call_record.state = CallState.COMPLETED
        db.commit()
        return {"status": "completed", "call_id": call_record.id}

    except Exception:
        logger.exception("Error processing post-call webhook")
        db.rollback()
        raise HTTPException(status_code=500, detail="Webhook processing failed")
    finally:
        db.close()


@app.post("/webhooks/smallest/analytics")
async def webhook_analytics(payload: SmallestAIAnalyticsPayload):
    """Handle analytics-completed webhook from Smallest.ai.

    This fires after Smallest.ai finishes deeper analysis. We re-run triage
    with the updated metrics and update the call record.
    """
    logger.info("Analytics webhook received: call_id=%s user_id=%s", payload.call_id, payload.user_id)

    db = get_db()
    try:
        call_record = (
            db.query(DBCallRecord)
            .filter(DBCallRecord.smallest_call_id == payload.call_id)
            .first()
        )
        if call_record is None:
            logger.warning("Analytics webhook for unknown call: %s", payload.call_id)
            return {"status": "ignored", "reason": "call_not_found"}

        # If already escalated or completed, just log
        if call_record.state in (CallState.ESCALATED, CallState.COMPLETED):
            logger.info("Call %s already %s — analytics noted", call_record.id, call_record.state.value)
            return {"status": "already_processed", "call_id": call_record.id}

        # Build a post-call payload from analytics data for triage
        post_call = SmallestAIPostCallPayload(
            call_id=payload.call_id,
            user_id=payload.user_id,
            status="completed",
            audio_metrics=payload.audio_metrics,
            transcript=payload.transcript,
            emotions=payload.emotions,
        )
        triage_result = analyze_vitals(post_call)

        call_record.triage_classification = triage_result.classification.value
        call_record.triage_reason = triage_result.reason

        if triage_result.escalate:
            call_record.state = CallState.ESCALATED
            call_record.escalation_reason = triage_result.reason
            db.commit()

            user = db.query(UserRecord).filter(UserRecord.id == payload.user_id).first()
            user_name = user.name if user else payload.user_id
            send_escalation_sms(
                user_name=user_name,
                triage_reason=triage_result.reason,
                call_id=call_record.id,
            )
            return {"status": "escalated", "call_id": call_record.id}

        db.commit()
        return {"status": "updated", "call_id": call_record.id, "classification": triage_result.classification.value}

    except Exception:
        logger.exception("Error processing analytics webhook")
        db.rollback()
        raise HTTPException(status_code=500, detail="Analytics webhook processing failed")
    finally:
        db.close()


# =====================================================================
# Manual trigger: place an outbound call now
# =====================================================================
@app.post("/calls/outbound")
async def trigger_outbound_call(user_id: str, campaign_id: str | None = None):
    """Manually trigger an outbound call for a specific user."""
    db = get_db()
    try:
        user = db.query(UserRecord).filter(UserRecord.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        call_id = f"call_{uuid4().hex[:10]}"
        now = datetime.now(timezone.utc)
        call_record = DBCallRecord(
            id=call_id,
            user_id=user.id,
            campaign_id=campaign_id or user.campaign_id,
            state=CallState.PENDING,
            started_at=now,
            created_at=now,
        )
        db.add(call_record)
        db.commit()

        request = OutboundCallRequest(
            user_id=user.id,
            user_name=user.name,
            phone_number=user.phone,
            campaign_id=campaign_id or user.campaign_id,
        )
        smallest_call_id = await place_outbound_call(request)

        if smallest_call_id:
            call_record.smallest_call_id = smallest_call_id
            db.commit()
            return {"status": "call_placed", "call_id": call_id, "smallest_call_id": smallest_call_id}
        else:
            call_record.state = CallState.BUSY_RETRY
            db.commit()
            raise HTTPException(status_code=502, detail="Failed to place outbound call")
    finally:
        db.close()
