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
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import get_current_doctor, router as auth_router
from claude import respond, process_transcript
from database import (
    CallRecord as DBCallRecord,
    ConversationRecord,
    Doctor,
    EscalationRecord,
    Patient,
    SessionLocal,
    init_db,
    get_db,
)
from models import (
    CallState,
    OutboundCallRequest,
    PatientStatus,
    SeverityGrade,
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
VOICE_LLM_MODEL = os.getenv("VOICE_LLM_MODEL", "openai/gpt-oss-20b:free")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    admin = _ensure_admin()
    seed_example_data(admin.id)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="PulseCall API", version="0.2.0", lifespan=lifespan)

origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "https://pulsecall.onrender.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------
class PatientCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    primary_diagnosis: Optional[str] = None
    surgical_history: Optional[list[dict]] = None
    medications: Optional[list[dict]] = None
    allergies: Optional[list[str]] = None
    vital_signs: Optional[dict] = None
    post_op_instructions: Optional[list[str]] = None
    emergency_contact: Optional[dict] = None
    previous_calls_context: Optional[list[dict]] = None
    next_appointment: Optional[str] = None
    severity_grade: Optional[str] = None
    agent_persona: Optional[str] = None
    conversation_goal: Optional[str] = None
    system_prompt: Optional[str] = None
    escalation_keywords: Optional[list[str]] = None
    voice_id: Optional[str] = "rachel"


class PatientOut(BaseModel):
    id: str
    name: str
    phone: str
    email: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    primary_diagnosis: Optional[str] = None
    surgical_history: Optional[list[dict]] = None
    medications: Optional[list[dict]] = None
    allergies: Optional[list[str]] = None
    vital_signs: Optional[dict] = None
    post_op_instructions: Optional[list[str]] = None
    emergency_contact: Optional[dict] = None
    previous_calls_context: Optional[list[dict]] = None
    next_appointment: Optional[str] = None
    severity_grade: Optional[str] = None
    status: str
    agent_persona: Optional[str] = None
    conversation_goal: Optional[str] = None
    system_prompt: Optional[str] = None
    escalation_keywords: Optional[list[str]] = None
    voice_id: Optional[str] = None
    created_at: str


class ChatRequest(BaseModel):
    message: str


class EndCallOut(BaseModel):
    call_id: str
    conversation_id: str
    patient_id: str
    status: Literal["ended"]
    summary: str
    sentiment_score: int
    detected_flags: list[str]
    recommended_action: str
    escalation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(text: Optional[str], default=None):
    """Safely load a JSON column value."""
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _patient_to_dict(p: Patient) -> dict[str, Any]:
    """Convert a Patient ORM object to a serializable dict."""
    return {
        "id": p.id,
        "name": p.name,
        "phone": p.phone,
        "email": p.email,
        "age": p.age,
        "gender": p.gender,
        "primary_diagnosis": p.primary_diagnosis,
        "surgical_history": _json_loads(p.surgical_history),
        "medications": _json_loads(p.medications),
        "allergies": _json_loads(p.allergies),
        "vital_signs": _json_loads(p.vital_signs, default={}),
        "post_op_instructions": _json_loads(p.post_op_instructions),
        "emergency_contact": _json_loads(p.emergency_contact, default={}),
        "previous_calls_context": _json_loads(p.previous_calls_context),
        "next_appointment": p.next_appointment,
        "severity_grade": p.severity_grade.value if p.severity_grade else None,
        "status": p.status.value if p.status else "PENDING_REVIEW",
        "agent_persona": p.agent_persona,
        "conversation_goal": p.conversation_goal,
        "system_prompt": p.system_prompt,
        "escalation_keywords": _json_loads(p.escalation_keywords),
        "voice_id": p.voice_id,
        "created_at": p.created_at.isoformat() if p.created_at else now_iso(),
    }


def _patient_data_dict(p: Patient) -> dict[str, Any]:
    """Build the patient_data dict used by the LLM system prompt builder."""
    return {
        "id": p.id,
        "name": p.name,
        "age": p.age,
        "gender": p.gender,
        "primaryDiagnosis": p.primary_diagnosis,
        "surgicalHistory": _json_loads(p.surgical_history),
        "medications": _json_loads(p.medications),
        "allergies": _json_loads(p.allergies),
        "vitalSigns": _json_loads(p.vital_signs, default={}),
        "postOpInstructions": _json_loads(p.post_op_instructions),
        "nextAppointment": p.next_appointment,
        "emergencyContact": _json_loads(p.emergency_contact, default={}),
        "previousCalls": _json_loads(p.previous_calls_context),
    }


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
        "Agent and patient completed a check-in call. "
        f"Patient's main concern: {first_user_msg[:120]}"
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


def _build_system_prompt(patient: Patient) -> str:
    """Build the full system prompt for voice chat from patient data."""
    pd = _patient_data_dict(patient)
    patient_ctx = _build_patient_context(pd)

    base_prompt = patient.system_prompt or "You are a helpful AI assistant."

    if not pd.get("name"):
        return base_prompt

    name_first = pd.get("name", "the patient").split()[0]
    allergies = pd.get("allergies", [])
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


# ---------------------------------------------------------------------------
# Admin bootstrap
# ---------------------------------------------------------------------------

def _ensure_admin() -> Doctor:
    """서버 시작 시 기본 admin 계정이 없으면 생성하고 반환."""
    from auth import hash_password  # 순환 import 방지를 위해 지역 import
    db = SessionLocal()
    try:
        admin = db.query(Doctor).filter(Doctor.email == "admin@pulsecall.dev").first()
        if not admin:
            admin = Doctor(
                id="doc_admin_000",
                email="admin@pulsecall.dev",
                password_hash=hash_password("admin1234"),
                name="PulseCall Admin",
                role="admin",
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            logger.info("Default admin account created: admin@pulsecall.dev / admin1234")
        return admin
    except Exception:
        db.rollback()
        logger.exception("Failed to create admin account")
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_PATIENTS = [
    {
        "id": "pt_demo_001",
        "name": "Michael Thompson",
        "phone": "+1-555-0191",
        "age": 58,
        "gender": "Male",
        "primary_diagnosis": "Osteoarthritis of the right knee",
        "agent_persona": "PulseCall Medical Assistant",
        "conversation_goal": "Check on Michael's recovery after total knee replacement surgery.",
        "escalation_keywords": ["blood clot", "infection", "fever", "chest pain", "911", "emergency"],
        "voice_id": "rachel",
        "next_appointment": "2026-02-21",
        "surgical_history": [
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
        "vital_signs": {"bloodPressure": "132/84 mmHg", "heartRate": "76 bpm", "temperature": "36.8°C", "weight": "88 kg", "height": "178 cm"},
        "post_op_instructions": [
            "Perform prescribed physical therapy exercises 3 times daily",
            "Keep surgical wound clean and dry",
            "Use ice packs for 20 minutes every 2-3 hours to reduce swelling",
            "Use walker or crutches for ambulation",
            "Elevate leg when sitting or lying down",
            "Report any signs of infection: increased redness, warmth, drainage, or fever above 38.3°C",
        ],
        "emergency_contact": {"name": "Linda Thompson (Wife)", "phone": "+1-555-0192"},
        "previous_calls_context": [
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
    {
        "id": "pt_demo_002",
        "name": "Sarah Kim",
        "phone": "+1-555-0233",
        "age": 34,
        "gender": "Female",
        "primary_diagnosis": "Complete ACL tear, left knee (sports injury)",
        "agent_persona": "PulseCall Medical Assistant",
        "conversation_goal": "Check on Sarah's recovery after ACL reconstruction surgery.",
        "escalation_keywords": ["blood clot", "infection", "fever", "chest pain", "911", "emergency", "popping"],
        "voice_id": "rachel",
        "next_appointment": "2026-02-18",
        "surgical_history": [
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
        "vital_signs": {"bloodPressure": "118/72 mmHg", "heartRate": "68 bpm", "temperature": "36.6°C", "weight": "62 kg", "height": "165 cm"},
        "post_op_instructions": [
            "Wear knee brace locked at 0° extension for first 2 weeks",
            "Begin gentle quad sets and straight leg raises on day 2",
            "Use crutches — weight-bear as tolerated",
            "Ice knee 20 minutes every 2 hours while awake",
            "Keep incision sites clean and dry for 10 days",
            "No pivoting, twisting, or running for 6 months",
            "Attend PT sessions 3x/week starting week 2",
        ],
        "emergency_contact": {"name": "David Kim (Husband)", "phone": "+1-555-0234"},
        "previous_calls_context": [
            {
                "date": "2026-02-05",
                "summary": "First check-in. Pain 6/10, mostly when straightening leg. Swelling moderate. Started quad sets. Ice helping. Crutch use good. Reminded about brace at 0° rule.",
                "painLevel": 6,
                "symptoms": ["knee pain on extension", "moderate swelling", "stiffness"],
            },
        ],
    },
    {
        "id": "pt_demo_003",
        "name": "James Rodriguez",
        "phone": "+1-555-0370",
        "age": 72,
        "gender": "Male",
        "primary_diagnosis": "Severe osteoarthritis of the left hip",
        "agent_persona": "PulseCall Medical Assistant",
        "conversation_goal": "Check on James's recovery after total hip replacement surgery.",
        "escalation_keywords": ["blood clot", "infection", "fever", "chest pain", "911", "emergency", "dislocation", "pop"],
        "voice_id": "rachel",
        "next_appointment": "2026-02-25",
        "surgical_history": [
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
        "vital_signs": {"bloodPressure": "140/88 mmHg", "heartRate": "82 bpm", "temperature": "37.0°C", "weight": "95 kg", "height": "175 cm"},
        "post_op_instructions": [
            "Follow hip precautions: no crossing legs, no bending hip past 90°, no twisting",
            "Use walker for 4-6 weeks",
            "Perform ankle pumps and gentle hip exercises 3x daily",
            "Sleep on back or non-operative side with pillow between knees",
            "Keep incision clean and dry — steri-strips will fall off on their own",
            "Monitor blood sugar closely — surgery can affect levels",
            "Report any sudden increase in pain, leg shortening, or inability to bear weight",
        ],
        "emergency_contact": {"name": "Maria Rodriguez (Wife)", "phone": "+1-555-0371"},
        "previous_calls_context": [
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
]


def seed_example_data(admin_id: str) -> None:
    """Seed demo patients into the database (skip if already present)."""
    db = SessionLocal()
    try:
        for sp in SEED_PATIENTS:
            existing = db.query(Patient).filter(Patient.id == sp["id"]).first()
            if existing:
                # 기존 시드 환자의 doctor_id가 None이면 admin으로 업데이트
                if existing.doctor_id is None:
                    existing.doctor_id = admin_id
                continue

            patient = Patient(
                id=sp["id"],
                name=sp["name"],
                phone=sp["phone"],
                age=sp.get("age"),
                gender=sp.get("gender"),
                primary_diagnosis=sp.get("primary_diagnosis"),
                surgical_history=json.dumps(sp.get("surgical_history", [])),
                medications=json.dumps(sp.get("medications", [])),
                allergies=json.dumps(sp.get("allergies", [])),
                vital_signs=json.dumps(sp.get("vital_signs", {})),
                post_op_instructions=json.dumps(sp.get("post_op_instructions", [])),
                emergency_contact=json.dumps(sp.get("emergency_contact", {})),
                previous_calls_context=json.dumps(sp.get("previous_calls_context", [])),
                next_appointment=sp.get("next_appointment"),
                severity_grade=None,
                status=PatientStatus.ACTIVE,
                agent_persona=sp.get("agent_persona"),
                conversation_goal=sp.get("conversation_goal"),
                system_prompt="Be concise, empathetic, and clear. Ask one question at a time.",
                escalation_keywords=json.dumps(sp.get("escalation_keywords", [])),
                voice_id=sp.get("voice_id", "rachel"),
                doctor_id=admin_id,
            )
            db.add(patient)

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to seed example data")
    finally:
        db.close()


def _get_patient(db, patient_id: str) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def _get_conversation(db, conversation_id: str) -> ConversationRecord:
    conv = db.query(ConversationRecord).filter(ConversationRecord.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found.")
    return conv


def get_client_text(history: list[dict[str, str]]) -> str:
    return " ".join(d["content"] for d in history if d["role"] == "user")


# =====================================================================
# Patient CRUD
# =====================================================================

@app.get("/")
def read_root() -> dict[str, str]:
    return {"service": "PulseCall API", "status": "ok"}


@app.get("/patients")
def list_patients(
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(Patient).order_by(Patient.created_at.desc())
    # RBAC: admin sees all patients; doctors see only their own
    if current_doctor.role != "admin":
        q = q.filter(Patient.doctor_id == current_doctor.id)
    return [_patient_to_dict(p) for p in q.all()]


@app.get("/patients/{patient_id}")
def get_patient_detail(
    patient_id: str,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    patient = _get_patient(db, patient_id)
    # RBAC: doctors can only access their own patients
    if current_doctor.role != "admin" and patient.doctor_id != current_doctor.id:
        raise HTTPException(status_code=403, detail="Access denied")
    result = _patient_to_dict(patient)
    # Include patient_data for backwards compat with frontend voice UI
    result["patient_data"] = _patient_data_dict(patient)
    return result


@app.post("/patients")
def create_patient(
    payload: PatientCreate,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    try:
        patient_id = f"pt_{uuid4().hex[:10]}"
        patient = Patient(
            id=patient_id,
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
            age=payload.age,
            gender=payload.gender,
            primary_diagnosis=payload.primary_diagnosis,
            surgical_history=json.dumps(payload.surgical_history or []),
            medications=json.dumps(payload.medications or []),
            allergies=json.dumps(payload.allergies or []),
            vital_signs=json.dumps(payload.vital_signs or {}),
            post_op_instructions=json.dumps(payload.post_op_instructions or []),
            emergency_contact=json.dumps(payload.emergency_contact or {}),
            previous_calls_context=json.dumps(payload.previous_calls_context or []),
            next_appointment=payload.next_appointment,
            severity_grade=SeverityGrade(payload.severity_grade) if payload.severity_grade else None,
            status=PatientStatus.PENDING_REVIEW,
            agent_persona=payload.agent_persona,
            conversation_goal=payload.conversation_goal,
            system_prompt=payload.system_prompt,
            escalation_keywords=json.dumps(payload.escalation_keywords or []),
            voice_id=payload.voice_id,
            doctor_id=current_doctor.id,  # auto-assign to the registering doctor
        )
        db.add(patient)
        db.commit()
        return _patient_to_dict(patient)
    except Exception:
        db.rollback()
        raise


@app.patch("/patients/{patient_id}/confirm")
def confirm_patient(
    patient_id: str,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """Doctor confirms a patient for active AI follow-up calls."""
    try:
        patient = _get_patient(db, patient_id)
        if current_doctor.role != "admin" and patient.doctor_id != current_doctor.id:
            raise HTTPException(status_code=403, detail="Access denied")
        patient.status = PatientStatus.CONFIRMED
        db.commit()
        return _patient_to_dict(patient)
    except Exception:
        db.rollback()
        raise


# =====================================================================
# Conversations
# =====================================================================

@app.post("/patients/conversations/create")
def create_conversation(
    patient_id: str,
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    try:
        _get_patient(db, patient_id)  # validate patient exists
        conversation_id = str(uuid4())
        started_at = datetime.now(timezone.utc)

        conv = ConversationRecord(
            id=conversation_id,
            patient_id=patient_id,
            status="active",
            history=json.dumps([]),
            started_at=started_at,
        )
        db.add(conv)
        db.commit()

        return {
            "id": conversation_id,
            "patient_id": patient_id,
            "status": "active",
            "start_time": started_at.isoformat(),
            "end_time": None,
            "started_at": started_at.isoformat(),
            "ended_at": None,
            "history": [],
        }
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@app.post("/patients/{patient_id}/{conversation_id}")
def get_response(
    patient_id: str,
    conversation_id: str,
    message: str,
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """Get a response from the LLM for a given conversation."""
    try:
        conv = _get_conversation(db, conversation_id)
        if conv.patient_id != patient_id:
            raise HTTPException(status_code=400, detail="Conversation does not belong to patient")
        if conv.status != "active":
            raise HTTPException(status_code=400, detail="Conversation is inactive")

        patient = _get_patient(db, patient_id)
        current_history = _json_loads(conv.history)
        current_history.append({"role": "user", "content": message})

        try:
            response = respond(
                user_message=message,
                history=current_history,
                system_prompt=patient.system_prompt or "",
            )
        except Exception as e:
            logger.exception("LLM response generation failed: %s", str(e))
            raise HTTPException(status_code=500, detail=f"LLM API Error: {str(e)}")

        current_history.append({"role": "assistant", "content": response})
        conv.history = json.dumps(current_history)
        db.commit()

        return response
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@app.post("/patients/{patient_id}/{conversation_id}/end", response_model=EndCallOut)
def end_call(
    patient_id: str,
    conversation_id: str,
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
) -> EndCallOut:
    try:
        conv = _get_conversation(db, conversation_id)
        if conv.patient_id != patient_id:
            raise HTTPException(status_code=400, detail="Conversation does not belong to patient")
        if conv.status != "active":
            raise HTTPException(status_code=400, detail="Conversation already ended")

        ended_at = datetime.now(timezone.utc)
        conv.status = "inactive"
        conv.ended_at = ended_at

        patient = _get_patient(db, patient_id)
        history = _json_loads(conv.history)
        keywords = _json_loads(patient.escalation_keywords)

        try:
            result = process_transcript(history, keywords)
            summary = result["summary"]
            sentiment_score = result["sentiment_score"]
            detected_flags = result["detected_flags"]
            recommended_action = result["recommended_action"]
        except Exception:
            full_text = get_client_text(history)
            summary = fallback_summary(history)
            sentiment_score = fallback_sentiment(full_text)
            detected_flags = fallback_flags(history, keywords)
            recommended_action = recommended_action_for_flags(detected_flags)

        call_id = f"call_{uuid4().hex[:10]}"
        escalation_id: Optional[str] = None

        if detected_flags:
            escalation_id = f"esc_{uuid4().hex[:10]}"
            esc = EscalationRecord(
                id=escalation_id,
                call_id=call_id,
                patient_id=patient_id,
                priority="high" if sentiment_score <= 2 else "medium",
                status="open",
                reason=f"Detected escalation keywords: {', '.join(detected_flags)}",
                detected_flags=json.dumps(detected_flags),
            )
            db.add(esc)

        call_record = DBCallRecord(
            id=call_id,
            patient_id=patient_id,
            conversation_id=conversation_id,
            state=CallState.COMPLETED,
            transcript_text=json.dumps(history),
            summary=summary,
            sentiment_score=sentiment_score,
            detected_flags=json.dumps(detected_flags),
            recommended_action=recommended_action,
            escalation_reason=f"Keywords: {', '.join(detected_flags)}" if detected_flags else None,
            started_at=conv.started_at,
            ended_at=ended_at,
        )
        db.add(call_record)
        db.commit()

        return EndCallOut(
            call_id=call_id,
            conversation_id=conversation_id,
            patient_id=patient_id,
            status="ended",
            summary=summary,
            sentiment_score=sentiment_score,
            detected_flags=detected_flags,
            recommended_action=recommended_action,
            escalation_id=escalation_id,
        )
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@app.get("/conversations")
def list_conversations(
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    convs = db.query(ConversationRecord).order_by(ConversationRecord.started_at.desc()).all()
    return [
        {
            "id": c.id,
            "patient_id": c.patient_id,
            "status": c.status,
            "start_time": c.started_at.isoformat() if c.started_at else None,
            "end_time": c.ended_at.isoformat() if c.ended_at else None,
            "history": _json_loads(c.history),
        }
        for c in convs
    ]


# =====================================================================
# Calls
# =====================================================================

def _call_to_dict(r: DBCallRecord, db=None) -> dict[str, Any]:
    escalation_id = None
    if db is not None:
        esc = (
            db.query(EscalationRecord)
            .filter(EscalationRecord.call_id == r.id)
            .first()
        )
        escalation_id = esc.id if esc else None

    return {
        "id": r.id,
        "call_id": r.id,
        "patient_id": r.patient_id,
        "conversation_id": r.conversation_id,
        "status": "ended" if r.state == CallState.COMPLETED else r.state.value if r.state else "unknown",
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        "transcript": _json_loads(r.transcript_text),
        "summary": r.summary or "",
        "sentiment_score": r.sentiment_score or 3,
        "detected_flags": _json_loads(r.detected_flags),
        "recommended_action": r.recommended_action or "",
        "escalation_id": escalation_id,
    }


@app.get("/calls")
def list_calls(
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    records = db.query(DBCallRecord).order_by(DBCallRecord.created_at.desc()).all()
    return [_call_to_dict(r, db) for r in records]


@app.get("/calls/{call_id}")
def get_call_detail(
    call_id: str,
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    record = db.query(DBCallRecord).filter(DBCallRecord.id == call_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")
    return _call_to_dict(record, db)


# =====================================================================
# Escalations
# =====================================================================

def _escalation_to_dict(e: EscalationRecord) -> dict[str, Any]:
    return {
        "id": e.id,
        "call_id": e.call_id,
        "patient_id": e.patient_id,
        "priority": e.priority,
        "status": e.status,
        "reason": e.reason,
        "detected_flags": _json_loads(e.detected_flags),
        "created_at": e.created_at.isoformat() if e.created_at else now_iso(),
        "acknowledged_at": e.acknowledged_at.isoformat() if e.acknowledged_at else None,
    }


@app.get("/escalations")
def list_escalations(
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    escalations = db.query(EscalationRecord).order_by(EscalationRecord.created_at.desc()).all()
    priority_order = {"high": 0, "medium": 1, "low": 2}
    escalations.sort(key=lambda e: priority_order.get(e.priority, 3))
    return [_escalation_to_dict(e) for e in escalations]


@app.patch("/escalations/{escalation_id}/acknowledge")
def acknowledge_escalation(
    escalation_id: str,
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    try:
        esc = db.query(EscalationRecord).filter(EscalationRecord.id == escalation_id).first()
        if not esc:
            raise HTTPException(status_code=404, detail="Escalation not found")
        esc.status = "acknowledged"
        esc.acknowledged_at = datetime.now(timezone.utc)
        db.commit()
        return _escalation_to_dict(esc)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


# =====================================================================
# Call history per patient
# =====================================================================

@app.get("/call-history/{patient_id}")
def get_patient_call_history(
    patient_id: str,
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    records = (
        db.query(DBCallRecord)
        .filter(DBCallRecord.patient_id == patient_id)
        .order_by(DBCallRecord.created_at.desc())
        .all()
    )
    return [_call_to_dict(r, db) for r in records]


# =====================================================================
# Smallest.ai webhook handlers
# =====================================================================

@app.post("/webhooks/smallest/post-call")
async def webhook_post_call(
    payload: SmallestAIPostCallPayload,
    db: Session = Depends(get_db),
):
    """Handle post-conversation webhook from Smallest.ai."""
    logger.info("Post-call webhook received: call_id=%s user_id=%s status=%s", payload.call_id, payload.user_id, payload.status)

    try:
        # Find the call record by smallest_call_id
        call_record = (
            db.query(DBCallRecord)
            .filter(DBCallRecord.smallest_call_id == payload.call_id)
            .first()
        )

        if call_record is None:
            call_record = DBCallRecord(
                id=f"call_{uuid4().hex[:10]}",
                patient_id=payload.user_id,
                state=CallState.PENDING,
                smallest_call_id=payload.call_id,
                started_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            db.add(call_record)
            db.commit()

        if payload.status in ("busy", "no_answer", "failed"):
            call_record.state = CallState.BUSY_RETRY
            call_record.triage_reason = f"Call status: {payload.status}"
            schedule_retry(call_record, delay_minutes=10, db=db)
            return {"status": "retry_scheduled", "call_id": call_record.id}

        triage_result = analyze_vitals(payload)
        call_record.triage_classification = triage_result.classification.value
        call_record.triage_reason = triage_result.reason
        call_record.ended_at = datetime.now(timezone.utc)

        logger.info(
            "Triage result: call=%s classification=%s action=%s escalate=%s",
            call_record.id, triage_result.classification.value, triage_result.action, triage_result.escalate,
        )

        transcript_text = "\n".join(
            f"{seg.speaker}: {seg.text}" for seg in payload.transcript
        )
        call_record.transcript_text = transcript_text

        if triage_result.escalate:
            call_record.state = CallState.ESCALATED
            call_record.escalation_reason = triage_result.reason
            db.commit()

            patient = db.query(Patient).filter(Patient.id == payload.user_id).first()
            patient_name = patient.name if patient else payload.user_id

            send_escalation_sms(
                user_name=patient_name,
                triage_reason=triage_result.reason,
                call_id=call_record.id,
            )

            esc = EscalationRecord(
                id=f"esc_{uuid4().hex[:10]}",
                call_id=call_record.id,
                patient_id=payload.user_id,
                priority="high",
                status="open",
                reason=triage_result.reason,
                detected_flags=json.dumps([triage_result.classification.value]),
            )
            db.add(esc)
            db.commit()

            return {"status": "escalated", "call_id": call_record.id, "reason": triage_result.reason}

        elif triage_result.action == "SCHEDULE_RETRY":
            schedule_retry(call_record, delay_minutes=triage_result.retry_delay_minutes or 20, db=db)
            return {"status": "retry_scheduled", "call_id": call_record.id, "delay_minutes": triage_result.retry_delay_minutes}

        elif triage_result.action == "ANALYZE_TRANSCRIPT":
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
                logger.exception("Post-call analysis failed for call %s", call_record.id)

            call_record.state = CallState.COMPLETED
            db.commit()

            flags = _json_loads(call_record.detected_flags)
            if flags:
                patient = db.query(Patient).filter(Patient.id == payload.user_id).first()
                patient_name = patient.name if patient else payload.user_id
                send_escalation_sms(
                    user_name=patient_name,
                    triage_reason=f"Distress flags in transcript: {', '.join(flags)}",
                    call_id=call_record.id,
                )
                esc = EscalationRecord(
                    id=f"esc_{uuid4().hex[:10]}",
                    call_id=call_record.id,
                    patient_id=payload.user_id,
                    priority="high" if (call_record.sentiment_score or 3) <= 2 else "medium",
                    status="open",
                    reason=f"Transcript flags: {', '.join(flags)}",
                    detected_flags=json.dumps(flags),
                )
                db.add(esc)
                db.commit()

            return {"status": "completed", "call_id": call_record.id, "summary": call_record.summary}

        call_record.state = CallState.COMPLETED
        db.commit()
        return {"status": "completed", "call_id": call_record.id}

    except Exception:
        logger.exception("Error processing post-call webhook")
        db.rollback()
        raise HTTPException(status_code=500, detail="Webhook processing failed")


@app.post("/webhooks/smallest/analytics")
async def webhook_analytics(
    payload: SmallestAIAnalyticsPayload,
    db: Session = Depends(get_db),
):
    """Handle analytics-completed webhook from Smallest.ai."""
    logger.info("Analytics webhook received: call_id=%s user_id=%s", payload.call_id, payload.user_id)

    try:
        call_record = (
            db.query(DBCallRecord)
            .filter(DBCallRecord.smallest_call_id == payload.call_id)
            .first()
        )
        if call_record is None:
            logger.warning("Analytics webhook for unknown call: %s", payload.call_id)
            return {"status": "ignored", "reason": "call_not_found"}

        if call_record.state in (CallState.ESCALATED, CallState.COMPLETED):
            logger.info("Call %s already %s — analytics noted", call_record.id, call_record.state.value)
            return {"status": "already_processed", "call_id": call_record.id}

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

            patient = db.query(Patient).filter(Patient.id == payload.user_id).first()
            patient_name = patient.name if patient else payload.user_id
            send_escalation_sms(
                user_name=patient_name,
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


# =====================================================================
# Manual trigger: place an outbound call now
# =====================================================================

@app.post("/calls/outbound")
async def trigger_outbound_call(
    patient_id: str,
    _: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """Manually trigger an outbound call for a specific patient."""
    try:
        patient = _get_patient(db, patient_id)

        call_id = f"call_{uuid4().hex[:10]}"
        now = datetime.now(timezone.utc)
        call_record = DBCallRecord(
            id=call_id,
            patient_id=patient.id,
            state=CallState.PENDING,
            started_at=now,
            created_at=now,
        )
        db.add(call_record)
        db.commit()

        request = OutboundCallRequest(
            patient_id=patient.id,
            patient_name=patient.name,
            phone_number=patient.phone,
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
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


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
    patient_id: str
    transcription: Optional[str] = None
    history: list[dict[str, str]] = Field(default_factory=list)
    trigger: Optional[str] = None
    # Legacy field for backwards compat during frontend migration
    campaign_id: Optional[str] = None


class VoiceSummaryRequest(BaseModel):
    history: list[dict[str, str]]


@app.post("/voice/chat")
async def voice_chat(
    payload: VoiceChatRequest,
    db: Session = Depends(get_db),
):
    """LLM + TTS: get AI text response and synthesized audio."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not configured")
    if not SMALLEST_AI_API_KEY:
        raise HTTPException(status_code=500, detail="SMALLEST_AI_API_KEY not configured")

    # Support both patient_id and legacy campaign_id
    lookup_id = payload.patient_id or payload.campaign_id
    if not lookup_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    patient = db.query(Patient).filter(Patient.id == lookup_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if not payload.transcription and payload.trigger != "initial":
        raise HTTPException(status_code=400, detail="No transcription provided")

    system_prompt = _build_system_prompt(patient)
    voice_id = patient.voice_id or "rachel"
    # Release DB connection before slow external HTTP calls (OpenRouter + TTS)
    db.close()

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

    is_ending = "[END_CALL]" in reply
    clean_reply = reply.replace("[END_CALL]", "").strip()
    if not is_ending:
        ending_patterns = re.compile(r"\b(goodbye|good bye|bye|take care|have a (good|great|nice) (day|evening|night|one))\b", re.IGNORECASE)
        is_ending = bool(ending_patterns.search(clean_reply))

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

    clean_raw = raw.replace("```json", "").replace("```", "").strip()
    json_match = re.search(r"\{[\s\S]*\}", clean_raw)
    if not json_match:
        raise HTTPException(status_code=500, detail="Failed to parse summary")

    return json.loads(json_match.group())
