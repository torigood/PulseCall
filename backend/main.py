from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="PulseCall MVP API", version="0.1.0")


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
    recipient: Recipient


class CampaignOut(CampaignCreate):
    id: str
    created_at: str


class SimulateCallOut(BaseModel):
    conversation_id: str
    campaign_id: str
    started_at: str
    status: Literal["active"]


class TurnIn(BaseModel):
    message: str = Field(min_length=1)


class TurnOut(BaseModel):
    conversation_id: str
    user_message: str
    agent_message: str
    turn_index: int
    status: Literal["active"]


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
    return datetime.now(UTC).isoformat()


def sentiment_from_text(text: str) -> int:
    negative_markers = ("angry", "upset", "cancel", "frustrated", "bad", "hate")
    lowered = text.lower()
    if any(marker in lowered for marker in negative_markers):
        return 2
    if "thank" in lowered or "great" in lowered:
        return 4
    return 3


def summarize_transcript(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "No conversation content captured."
    first_user_msg = next((t["content"] for t in transcript if t["role"] == "user"), "")
    return (
        "Agent and recipient completed a simulated call. "
        f"Recipient's main concern: {first_user_msg[:120]}"
    ).strip()


def detect_flags(transcript: list[dict[str, str]], keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    joined = " ".join(turn["content"] for turn in transcript).lower()
    return [kw for kw in keywords if kw.lower() in joined]


def recommended_action_for_flags(flags: list[str]) -> str:
    if not flags:
        return "No escalation required. Follow up in normal workflow."
    return "Escalate to a human operator within 15 minutes."


def build_agent_reply(campaign: dict[str, Any], user_message: str) -> str:
    persona = campaign["agent_persona"]
    goal = campaign["conversation_goal"]
    return (
        f"[{persona}] Thanks for sharing. I understand: '{user_message}'. "
        f"My goal is {goal}. Could you tell me a bit more so I can help?"
    )


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
        "recipient": {
            "name": "Alex Johnson",
            "phone": "+1-555-0100",
            "email": "alex@example.com",
        },
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


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def read_root() -> dict[str, str]:
    return {"service": "PulseCall MVP API", "status": "ok"}


@app.post("/campaigns", response_model=CampaignOut)
def create_campaign(payload: CampaignCreate) -> CampaignOut:
    campaign_id = f"cmp_{uuid4().hex[:10]}"
    campaign = {
        "id": campaign_id,
        **payload.model_dump(),
        "created_at": now_iso(),
    }
    store["campaigns"][campaign_id] = campaign
    return CampaignOut(**campaign)


@app.post("/campaigns/{campaign_id}/simulate", response_model=SimulateCallOut)
def simulate_call(campaign_id: str) -> SimulateCallOut:
    campaign = store["campaigns"].get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    conversation_id = f"conv_{uuid4().hex[:10]}"
    conversation = {
        "id": conversation_id,
        "campaign_id": campaign_id,
        "status": "active",
        "started_at": now_iso(),
        "ended_at": None,
        "transcript": [],
    }
    store["conversations"][conversation_id] = conversation
    return SimulateCallOut(
        conversation_id=conversation_id,
        campaign_id=campaign_id,
        started_at=conversation["started_at"],
        status="active",
    )


@app.post("/conversations/{conversation_id}/turn", response_model=TurnOut)
def send_turn(conversation_id: str, payload: TurnIn) -> TurnOut:
    conversation = store["conversations"].get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation["status"] != "active":
        raise HTTPException(status_code=400, detail="Conversation already ended")

    campaign = store["campaigns"][conversation["campaign_id"]]
    user_msg = payload.message.strip()
    agent_msg = build_agent_reply(campaign=campaign, user_message=user_msg)

    conversation["transcript"].append({"role": "user", "content": user_msg})
    conversation["transcript"].append({"role": "assistant", "content": agent_msg})
    turn_index = len(conversation["transcript"]) // 2

    return TurnOut(
        conversation_id=conversation_id,
        user_message=user_msg,
        agent_message=agent_msg,
        turn_index=turn_index,
        status="active",
    )


@app.post("/conversations/{conversation_id}/end", response_model=EndCallOut)
def end_call(conversation_id: str) -> EndCallOut:
    conversation = store["conversations"].get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation["status"] != "active":
        raise HTTPException(status_code=400, detail="Conversation already ended")

    conversation["status"] = "ended"
    conversation["ended_at"] = now_iso()

    campaign = store["campaigns"][conversation["campaign_id"]]
    transcript = conversation["transcript"]

    summary = summarize_transcript(transcript)
    full_text = " ".join(turn["content"] for turn in transcript)
    sentiment_score = sentiment_from_text(full_text)
    detected_flags = detect_flags(transcript, campaign["escalation_keywords"])
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
        "id": call_id,
        "conversation_id": conversation_id,
        "campaign_id": campaign["id"],
        "status": "ended",
        "started_at": conversation["started_at"],
        "ended_at": conversation["ended_at"],
        "transcript": transcript,
        "summary": summary,
        "sentiment_score": sentiment_score,
        "detected_flags": detected_flags,
        "recommended_action": recommended_action,
        "escalation_id": escalation_id,
    }
    store["calls"][call_id] = call

    return EndCallOut(**call)


@app.get("/calls")
def list_calls() -> list[dict[str, Any]]:
    calls = list(store["calls"].values())
    calls.sort(key=lambda c: c.get("ended_at", ""), reverse=True)
    return calls


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
