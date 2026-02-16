from __future__ import annotations

import base64
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
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

# Load env
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
SMALLEST_AI_API_KEY = os.getenv("SMALLEST_AI_API_KEY", "")
VOICE_LLM_MODEL = "openai/gpt-oss-20b:free"  # openai/gpt-4o-mini

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="PulseCall MVP API", version="0.1.0", lifespan=lifespan)

origins = [
    "http://localhost:3000",             
    "https://pulsecall.onrender.com",  
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Pydantic models
# -----------------------------
class Recipient(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None


class CampaignCreate(BaseModel):
    name: str
    agent_persona: str
    conversation_goal: str
    system_prompt: str
    escalation_keywords: list[str] = Field(default_factory=list)
    recipients: list[Recipient]
    patient_context: Optional[str] = None
    patient_data: Optional[dict] = None
    voice_id: Optional[str] = "rachel"


class CampaignOut(CampaignCreate):
    id: str
    created_at: str


class Conversation(BaseModel):
    conversation_id: str
    campaign_id: str
    status: Literal["active", "inactive"]
    start_time: str
    end_time: Optional[str] = None
    history: list[dict[str, str]]


class ChatRequest(BaseModel):
    message: str


class EndCallOut(BaseModel):
    call_id: str
    conversation_id: str
    campaign_id: str
    status: Literal["ended"]
    summary: str
    sentiment_score: int
    detected_flags: list[str]
    recommended_action: str
    escalation_id: Optional[str] = None


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


def _build_patient_context(pd: dict) -> str:
    """Build LLM-ready patient context string from patient_data dict."""
    lines = [
        "PATIENT PROFILE:",
        f"- Name: {pd.get('name', 'Unknown')}, Age: {pd.get('age', 'N/A')}, Gender: {pd.get('gender', 'N/A')}",
        f"- Patient ID: {pd.get('id', 'N/A')}",
        f"- Primary Diagnosis: {pd.get('primaryDiagnosis', 'N/A')}",
        "",
        "SURGICAL HISTORY:",
    ]
    for s in pd.get("surgicalHistory", []):
        lines.append(f"- {s.get('procedure', '')} on {s.get('date', '')} by {s.get('surgeon', '')} at {s.get('hospital', '')}. {s.get('notes', '')}")
    lines.append("")
    lines.append("CURRENT MEDICATIONS:")
    for m in pd.get("medications", []):
        lines.append(f"- {m.get('name', '')} {m.get('dosage', '')} — {m.get('frequency', '')}")
    allergies = pd.get("allergies", [])
    lines.append(f"\nALLERGIES: {', '.join(allergies) if allergies else 'None known'}")
    vs = pd.get("vitalSigns", {})
    if vs:
        lines.append(f"\nVITAL SIGNS (Last Recorded):")
        lines.append(f"- BP: {vs.get('bloodPressure', 'N/A')}, HR: {vs.get('heartRate', 'N/A')}, Temp: {vs.get('temperature', 'N/A')}")
    poi = pd.get("postOpInstructions", [])
    if poi:
        lines.append("\nPOST-OP INSTRUCTIONS:")
        for i in poi:
            lines.append(f"- {i}")
    lines.append(f"\nNEXT APPOINTMENT: {pd.get('nextAppointment', 'N/A')}")
    prev = pd.get("previousCalls", [])
    if prev:
        lines.append("\nPREVIOUS CALL LOGS (most recent first):")
        for c in reversed(prev):
            lines.append(f"- {c.get('date', '')}: Pain {c.get('painLevel', '?')}/10 — {c.get('summary', '')}")
    ec = pd.get("emergencyContact", {})
    if ec:
        lines.append(f"\nEMERGENCY CONTACT: {ec.get('name', 'N/A')}, {ec.get('phone', 'N/A')}")
    return "\n".join(lines)


def _build_system_prompt(campaign: dict) -> str:
    """Build the full system prompt for voice chat from campaign data."""
    pd = campaign.get("patient_data", {})
    patient_ctx = campaign.get("patient_context") or ""
    if pd and not patient_ctx:
        patient_ctx = _build_patient_context(pd)

    base_prompt = campaign.get("system_prompt", "You are a helpful AI assistant.")

    if not patient_ctx:
        return base_prompt

    name_first = pd.get("name", "the patient").split()[0] if pd else "the patient"
    allergies = pd.get("allergies", [])
    meds = pd.get("medications", [])
    surgery = pd.get("surgicalHistory", [{}])[0] if pd.get("surgicalHistory") else {}
    next_appt = pd.get("nextAppointment", "TBD")
    ec = pd.get("emergencyContact", {})
    poi = pd.get("postOpInstructions", [])

    return f"""
ROLE AND CONTEXT:
You are PulseCall, a professional and empathetic friendly medical AI assistant conducting a post-operative check-in call. 
Your goal is to assess the patient's recovery, provide instructions from their records, and identify potential complications.

PATIENT RECORDS:
{patient_ctx}

COMMUNICATION CONSTRAINTS (CRITICAL):
1. Brevity: Limit responses to 1-2 concise sentences. This is a real-time phone call.
2. Single Task: Ask exactly ONE question per turn. Never stack multiple questions.
3. No Repetition: Do not re-introduce yourself after the first turn. Do not ask questions already answered.
4. Voice Optimization: Use natural, conversational language. Avoid complex formatting.
5. Termination: Append [END_CALL] only when the conversation is fully concluded.

MEDICAL SAFETY & GUARDRAILS:
- Allergy Alert: Patient is allergic to {', '.join(allergies) if allergies else 'nothing known'}. Never suggest products involving these.
- Scope: Never diagnose new conditions or prescribe medications. Reference only the provided "Post-Op Instructions" and "Medications".
- Escalation: If pain is 7/10 or higher, advise calling the doctor's office immediately.

URGENT SYMPTOMS (PRIORITY OVERRIDE):
- Possible Blood Clot (Calf pain, swelling, shortness of breath): "That sounds serious. Please go to the ER or call 911 immediately. Can {ec.get('name', 'someone').split()[0]} drive you there now?"
- Possible Infection (Fever > 38.3°C, drainage, redness): "Please call your doctor's office right away, as those symptoms need to be evaluated today."
- Chest Pain: "Please hang up and call 911 immediately."

FALLBACK & PHONETIC INFERENCE (STT Error Handling):
1. Phonetic Number Inference: If a patient's response sounds like a number but is transcribed incorrectly (e.g., "sense" for "six", "too" for "two", "ate" for "eight", "ten" for "then"), clarify specifically: "Just to be sure, did you say your pain level is [Inferred Number]?"
2. Silence or Unclear Audio: If the input is silent, unintelligible, or purely background noise, say: "I'm sorry, I didn't quite catch that. Could you say that again?"
3. Irrelevant Response: If the patient speaks about unrelated topics, politely redirect back to the medical check-in: "I understand. To make sure your recovery is on track, could you tell me more about [Current Phase Question]?"

CONVERSATION FLOW (PHASE-BASED):
Identify the current phase based on the history and proceed:
- PHASE 1 (Initial Greeting): "Hi {name_first}, this is PulseCall checking in after your {surgery.get('procedure', 'surgery')}. How are you feeling today?"
- PHASE 2 (Symptom Assessment): If a symptom is reported, ask: "On a scale of 1 to 10, how would you rate that pain or discomfort?"
- PHASE 3 (Care Verification): Ask about compliance: "Are you following the instructions for {poi[0] if poi else 'your recovery'}?"
- PHASE 4 (Guidance): Provide ONE recommendation. "Based on your records, you should {poi[1] if len(poi)>1 else 'continue your prescribed care'}. Does that make sense, or is there anything else?"
- PHASE 5 (New Issue): If a new concern is raised, return to PHASE 2.
- PHASE 6 (Conclusion): Briefly summarize, remind them of their appointment on {next_appt} (speak the date in full words), and end. [END_CALL]

PREVIOUS CALL INTEGRATION:
- Reference "PREVIOUS CALL LOGS" to show continuity. Example: "I see your pain was a 7 last time, so I'm glad to hear it's down to a 4 today."

{base_prompt}"""


# --- Seed Data: Multiple patient campaigns ---

SEED_PATIENTS = [
    {
        "campaign_id": "cmp_demo_001",
        "name": "Post-Op Check-in: Michael Thompson",
        "agent_persona": "PulseCall Medical Assistant",
        "conversation_goal": "Check on Michael's recovery after total knee replacement surgery.",
        "escalation_keywords": ["blood clot", "infection", "fever", "chest pain", "911", "emergency"],
        "voice_id": "rachel",
        "patient_data": {
            "id": "PT-20240312",
            "name": "Michael Thompson",
            "age": 58,
            "gender": "Male",
            "primaryDiagnosis": "Osteoarthritis of the right knee",
            "surgicalHistory": [
                {
                    "procedure": "Total Right Knee Replacement (TKR)",
                    "date": "2026-01-28",
                    "surgeon": "Dr. Sarah Chen",
                    "hospital": "St. Mary's General Hospital",
                    "notes": "Uneventful surgery. Cemented prosthesis implanted.",
                }
            ],
            "medications": [
                {"name": "Acetaminophen", "dosage": "500mg", "frequency": "Every 6 hours as needed"},
                {"name": "Celecoxib", "dosage": "200mg", "frequency": "Once daily"},
                {"name": "Enoxaparin", "dosage": "40mg SC", "frequency": "Once daily for 14 days (blood clot prevention)"},
                {"name": "Lisinopril", "dosage": "10mg", "frequency": "Once daily (blood pressure)"},
            ],
            "allergies": ["Penicillin (rash)", "Latex (mild irritation)"],
            "vitalSigns": {"bloodPressure": "132/84 mmHg", "heartRate": "76 bpm", "temperature": "36.8°C", "weight": "88 kg", "height": "178 cm"},
            "postOpInstructions": [
                "Perform prescribed physical therapy exercises 3 times daily",
                "Keep surgical wound clean and dry",
                "Use ice packs for 20 minutes every 2-3 hours to reduce swelling",
                "Use walker or crutches for ambulation",
                "Elevate leg when sitting or lying down",
                "Report any signs of infection: increased redness, warmth, drainage, or fever above 38.3°C",
            ],
            "nextAppointment": "2026-02-21",
            "emergencyContact": {"name": "Linda Thompson (Wife)", "phone": "+1-555-0192"},
            "previousCalls": [
                {
                    "date": "2026-02-03",
                    "summary": "First post-op check-in. Pain 7/10 around the knee. Significant swelling. Has not started PT exercises yet. Advised to begin gentle range-of-motion exercises and ice 20 min every 2-3 hours.",
                    "painLevel": 7,
                    "symptoms": ["severe knee pain", "swelling", "difficulty bending knee"],
                },
                {
                    "date": "2026-02-10",
                    "summary": "Second check-in. Pain improved to 5/10. Started PT exercises 2 days ago. Reports morning stiffness that loosens up after walking. Mild swelling remains. Reminded to keep elevating leg and continue icing.",
                    "painLevel": 5,
                    "symptoms": ["moderate knee pain", "morning stiffness", "mild swelling"],
                },
            ],
        },
    },
    {
        "campaign_id": "cmp_demo_002",
        "name": "Post-Op Check-in: Sarah Kim",
        "agent_persona": "PulseCall Medical Assistant",
        "conversation_goal": "Check on Sarah's recovery after ACL reconstruction surgery.",
        "escalation_keywords": ["blood clot", "infection", "fever", "chest pain", "911", "emergency", "popping"],
        "voice_id": "rachel",
        "patient_data": {
            "id": "PT-20240415",
            "name": "Sarah Kim",
            "age": 34,
            "gender": "Female",
            "primaryDiagnosis": "Complete ACL tear, left knee (sports injury)",
            "surgicalHistory": [
                {
                    "procedure": "ACL Reconstruction (Hamstring Autograft)",
                    "date": "2026-02-01",
                    "surgeon": "Dr. James Park",
                    "hospital": "Waterloo Sports Medicine Center",
                    "notes": "Arthroscopic procedure. Meniscus intact. Successful graft fixation.",
                }
            ],
            "medications": [
                {"name": "Ibuprofen", "dosage": "400mg", "frequency": "Every 8 hours with food"},
                {"name": "Acetaminophen", "dosage": "500mg", "frequency": "Every 6 hours as needed (alternate with ibuprofen)"},
                {"name": "Aspirin", "dosage": "81mg", "frequency": "Once daily for 14 days (blood clot prevention)"},
            ],
            "allergies": ["Sulfa drugs (hives)"],
            "vitalSigns": {"bloodPressure": "118/72 mmHg", "heartRate": "68 bpm", "temperature": "36.6°C", "weight": "62 kg", "height": "165 cm"},
            "postOpInstructions": [
                "Wear knee brace locked at 0° extension for first 2 weeks",
                "Begin gentle quad sets and straight leg raises on day 2",
                "Use crutches — weight-bear as tolerated",
                "Ice knee 20 minutes every 2 hours while awake",
                "Keep incision sites clean and dry for 10 days",
                "No pivoting, twisting, or running for 6 months",
                "Attend PT sessions 3x/week starting week 2",
            ],
            "nextAppointment": "2026-02-18",
            "emergencyContact": {"name": "David Kim (Husband)", "phone": "+1-555-0234"},
            "previousCalls": [
                {
                    "date": "2026-02-05",
                    "summary": "First check-in. Pain 6/10, mostly when straightening leg. Swelling moderate. Started quad sets. Ice helping. Crutch use good. Reminded about brace at 0° rule.",
                    "painLevel": 6,
                    "symptoms": ["knee pain on extension", "moderate swelling", "stiffness"],
                },
            ],
        },
    },
    {
        "campaign_id": "cmp_demo_003",
        "name": "Post-Op Check-in: James Rodriguez",
        "agent_persona": "PulseCall Medical Assistant",
        "conversation_goal": "Check on James's recovery after total hip replacement surgery.",
        "escalation_keywords": ["blood clot", "infection", "fever", "chest pain", "911", "emergency", "dislocation", "pop"],
        "voice_id": "rachel",
        "patient_data": {
            "id": "PT-20240528",
            "name": "James Rodriguez",
            "age": 72,
            "gender": "Male",
            "primaryDiagnosis": "Severe osteoarthritis of the left hip",
            "surgicalHistory": [
                {
                    "procedure": "Total Left Hip Replacement (Anterior Approach)",
                    "date": "2026-02-05",
                    "surgeon": "Dr. Emily Watson",
                    "hospital": "Grand River Hospital",
                    "notes": "Ceramic-on-polyethylene bearing. No intraoperative complications.",
                }
            ],
            "medications": [
                {"name": "Acetaminophen", "dosage": "1000mg", "frequency": "Every 8 hours"},
                {"name": "Tramadol", "dosage": "50mg", "frequency": "Every 6 hours as needed for breakthrough pain"},
                {"name": "Enoxaparin", "dosage": "40mg SC", "frequency": "Once daily for 28 days (blood clot prevention)"},
                {"name": "Metformin", "dosage": "500mg", "frequency": "Twice daily (diabetes)"},
                {"name": "Amlodipine", "dosage": "5mg", "frequency": "Once daily (blood pressure)"},
            ],
            "allergies": ["Codeine (nausea/vomiting)", "Shellfish (anaphylaxis)"],
            "vitalSigns": {"bloodPressure": "140/88 mmHg", "heartRate": "82 bpm", "temperature": "37.0°C", "weight": "95 kg", "height": "175 cm"},
            "postOpInstructions": [
                "Follow hip precautions: no crossing legs, no bending hip past 90°, no twisting",
                "Use walker for 4-6 weeks",
                "Perform ankle pumps and gentle hip exercises 3x daily",
                "Sleep on back or non-operative side with pillow between knees",
                "Keep incision clean and dry — steri-strips will fall off on their own",
                "Monitor blood sugar closely — surgery can affect levels",
                "Report any sudden increase in pain, leg shortening, or inability to bear weight",
            ],
            "nextAppointment": "2026-02-25",
            "emergencyContact": {"name": "Maria Rodriguez (Wife)", "phone": "+1-555-0371"},
            "previousCalls": [
                {
                    "date": "2026-02-08",
                    "summary": "First check-in. Pain 8/10 at surgical site, worse with movement. Using walker consistently. Has not started exercises yet. Blood sugar slightly elevated at 180. Advised gentle ankle pumps and to contact endocrinologist about sugar levels.",
                    "painLevel": 8,
                    "symptoms": ["severe hip pain", "difficulty walking", "elevated blood sugar"],
                },
                {
                    "date": "2026-02-12",
                    "summary": "Second check-in. Pain improved to 6/10. Started ankle pumps. Blood sugar stabilized at 140. Sleeping on back with pillow. Reports some bruising around incision — normal healing. Reminded about hip precautions.",
                    "painLevel": 6,
                    "symptoms": ["moderate hip pain", "bruising around incision", "limited mobility"],
                },
            ],
        },
    },
]


def seed_example_data() -> None:
    for sp in SEED_PATIENTS:
        campaign_id = sp["campaign_id"]
        pd = sp["patient_data"]
        patient_context = _build_patient_context(pd)

        campaign = {
            "id": campaign_id,
            "name": sp["name"],
            "agent_persona": sp["agent_persona"],
            "conversation_goal": sp["conversation_goal"],
            "system_prompt": "Be concise, empathetic, and clear. Ask one question at a time.",
            "escalation_keywords": sp["escalation_keywords"],
            "recipients": [{"name": pd["name"], "phone": pd.get("emergencyContact", {}).get("phone", "")}],
            "patient_context": patient_context,
            "patient_data": pd,
            "voice_id": sp.get("voice_id", "rachel"),
            "created_at": now_iso(),
        }
        store["campaigns"][campaign_id] = campaign

    # Add a sample call for the first campaign
    call_id = "call_demo_001"
    escalation_id = "esc_demo_001"
    store["calls"][call_id] = {
        "id": call_id,
        "call_id": call_id,
        "campaign_id": "cmp_demo_001",
        "conversation_id": "conv_demo_001",
        "status": "ended",
        "started_at": now_iso(),
        "ended_at": now_iso(),
        "transcript": [
            {"role": "user", "content": "I'm having some pain around my knee, about a 4 out of 10."},
            {"role": "assistant", "content": "That's good to hear it's improving. Are you keeping up with your PT exercises?"},
        ],
        "summary": "Patient reports pain at 4/10, improving from previous 5/10. Continuing PT exercises.",
        "sentiment_score": 4,
        "detected_flags": [],
        "recommended_action": "No escalation required. Follow up in normal workflow.",
        "escalation_id": None,
    }


seed_example_data()

def get_campaign(campaign_id: str):
    campaign = store["campaigns"].get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign

def get_conversation(conversation_id: str): 
    conversation = store["conversations"].get(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found. The server might have restarted.")
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
    """
    Get a response from Claude for a given conversation.
    """
    conversation = get_conversation(conversation_id)
    if conversation["campaign_id"] != campaign_id:
        raise HTTPException(status_code=400, detail="Conversation does not belong to campaign")
    if conversation["status"] != "active":
        raise HTTPException(status_code=400, detail="Conversation is inactive")

    campaign = store["campaigns"].get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Create a temporary history to avoid corrupting the store if the API call fails
    current_history = list(conversation["history"])
    current_history.append({
        "role": "user",
        "content": message
    })

    try:
        response = respond(
            user_message=message,
            history=current_history,
            system_prompt=campaign["system_prompt"],
        )
    except Exception as e:
        logger.exception(f"Claude response generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Claude API Error: {str(e)}")
    
    # Only update the actual history if the API call was successful
    conversation["history"] = current_history + [{
        "role": "assistant",
        "content": response
    }]

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
    escalation_id: Optional[str] = None
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
    escalation_id: Optional[str] = None

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
    email: Optional[str] = None
    campaign_id: Optional[str] = None


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
async def trigger_outbound_call(user_id: str, campaign_id: Optional[str] = None):
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


# =====================================================================
# Voice API endpoints (STT, LLM + TTS, Summary)
# =====================================================================

SUMMARY_PROMPT = """You are a medical call summarizer. Analyze the conversation below and return ONLY valid JSON with this exact structure:

{
  "painLevel": <number 1-10 or null if not mentioned>,
  "symptoms": ["symptom1", "symptom2"],
  "ptExercise": <true/false/null>,
  "medications": "any medication updates or compliance notes",
  "concerns": "what the patient asked about or was worried about",
  "recommendation": "key advice given during the call",
  "followUp": "any follow-up actions needed",
  "summary": "2-3 sentence overall summary of the call"
}

Return ONLY the JSON object. No markdown, no explanation."""


class VoiceChatRequest(BaseModel):
    campaign_id: str
    transcription: Optional[str] = None
    history: list[dict[str, str]] = Field(default_factory=list)
    trigger: Optional[str] = None


class VoiceSummaryRequest(BaseModel):
    history: list[dict[str, str]]


@app.post("/voice/chat")
async def voice_chat(payload: VoiceChatRequest):
    """LLM + TTS: get AI text response and synthesized audio."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not configured")
    if not SMALLEST_AI_API_KEY:
        raise HTTPException(status_code=500, detail="SMALLEST_AI_API_KEY not configured")

    campaign = store["campaigns"].get(payload.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if not payload.transcription and payload.trigger != "initial":
        raise HTTPException(status_code=400, detail="No transcription provided")

    system_prompt = _build_system_prompt(campaign)
    past_messages = payload.history or []
    turn_number = len([m for m in past_messages if m.get("role") == "user"]) + 1

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(past_messages)

    if payload.trigger == "initial":
        messages.append({
            "role": "system",
            "content": "The patient has picked up the phone. Start the conversation with STEP 1 immediately.",
        })
    else:
        messages.append({
            "role": "user",
            "content": payload.transcription or "",
        })
        messages.append({
            "role": "system",
            "content": f"This is turn {turn_number}. Continue the flow naturally.",
        })

    # 1. LLM call via OpenRouter (free → paid fallback)
    or_headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "PulseCall",
    }
    llm_payload = {
        "model": VOICE_LLM_MODEL,
        "max_tokens": 300,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        llm_res = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=or_headers, json=llm_payload)
        if llm_res.status_code != 200:
            logger.error("OpenRouter error: %s", llm_res.text)
            raise HTTPException(status_code=llm_res.status_code, detail=llm_res.text)

        reply = llm_res.json().get("choices", [{}])[0].get("message", {}).get("content", "")

    # Detect ending via [END_CALL] marker or fallback regex
    is_ending = "[END_CALL]" in reply
    # Clean the marker from the reply text
    clean_reply = reply.replace("[END_CALL]", "").strip()
    if not is_ending:
        ending_patterns = re.compile(r"\b(goodbye|good bye|bye|take care|have a (good|great|nice) (day|evening|night|one))\b", re.IGNORECASE)
        is_ending = bool(ending_patterns.search(clean_reply))

    # 2. TTS via Smallest.ai
    voice_id = campaign.get("voice_id", "rachel")
    audio_base64 = None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            tts_res = await client.post(
                "https://waves-api.smallest.ai/api/v1/lightning-v3.1/get_speech",
                headers={
                    "Authorization": f"Bearer {SMALLEST_AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "text": clean_reply,
                    "voice_id": voice_id,
                    "sample_rate": 24000,
                    "speed": 1,
                    "output_format": "mp3",
                },
            )
            if tts_res.status_code == 200:
                audio_base64 = base64.b64encode(tts_res.content).decode("utf-8")
            else:
                logger.error("TTS error: %s", tts_res.text)
    except Exception as e:
        logger.error("TTS request failed: %s", e)

    return {"reply": clean_reply, "audio": audio_base64, "isEnding": is_ending}


@app.post("/voice/transcribe")
async def voice_transcribe(request: Request):
    """STT: convert audio to text via Smallest.ai."""
    if not SMALLEST_AI_API_KEY:
        raise HTTPException(status_code=500, detail="SMALLEST_AI_API_KEY not configured")

    audio_buffer = await request.body()
    content_type = request.headers.get("content-type", "audio/webm")

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            "https://waves-api.smallest.ai/api/v1/lightning/get_text?model=lightning&language=en",
            headers={
                "Authorization": f"Bearer {SMALLEST_AI_API_KEY}",
                "Content-Type": content_type,
            },
            content=audio_buffer,
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=res.text)
        return res.json()


@app.post("/voice/summary")
async def voice_summary(payload: VoiceSummaryRequest):
    """Post-call summary extraction."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not configured")

    if not payload.history:
        raise HTTPException(status_code=400, detail="No conversation history provided")

    conversation_text = "\n".join(
        f"{'Patient' if msg['role'] == 'user' else 'AI'}: {msg['content']}"
        for msg in payload.history
    )

    or_headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "PulseCall",
    }
    summary_payload = {
        "model": VOICE_LLM_MODEL,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": conversation_text},
        ],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=or_headers, json=summary_payload)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=res.text)

        raw = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")

    # Parse JSON from LLM response
    clean_raw = raw.replace("```json", "").replace("```", "").strip()
    json_match = re.search(r"\{[\s\S]*\}", clean_raw)
    if not json_match:
        raise HTTPException(status_code=500, detail="Failed to parse summary")

    return json.loads(json_match.group())
