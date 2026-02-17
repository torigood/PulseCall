from __future__ import annotations

from database import CallRecord
from models import CallState, TriageClassification, TriageResult


def _create_patient(api_request, name="Pat", phone="+1-555-4444"):
    return api_request(
        "POST",
        "/patients",
        json={"name": name, "phone": phone},
    ).json()


def _create_db_call(app_ctx, patient_id: str, smallest_call_id: str):
    db = app_ctx.get_db()
    try:
        rec = CallRecord(
            id="call_db_001",
            patient_id=patient_id,
            state=CallState.PENDING,
            smallest_call_id=smallest_call_id,
        )
        db.add(rec)
        db.commit()
    finally:
        db.close()


def test_post_call_busy_schedules_retry(app_ctx, api_request):
    patient = _create_patient(api_request)
    _create_db_call(app_ctx, patient["id"], "smallest_busy_001")

    response = api_request(
        "POST",
        "/webhooks/smallest/post-call",
        json={
            "call_id": "smallest_busy_001",
            "user_id": patient["id"],
            "status": "busy",
            "audio_metrics": {
                "avg_db": 0,
                "peak_db": 0,
                "speech_probability": 0,
                "silence_duration_sec": 0,
                "call_duration_sec": 0,
            },
            "transcript": [],
            "emotions": [],
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "retry_scheduled"


def test_post_call_immediate_escalation_sends_sms(app_ctx, api_request, monkeypatch):
    patient = _create_patient(api_request, name="Sam", phone="+1-555-5555")
    _create_db_call(app_ctx, patient["id"], "smallest_esc_001")

    sent = {"count": 0}

    def fake_send_sms(user_name: str, triage_reason: str, call_id: str, to_number=None):
        sent["count"] += 1
        return True

    monkeypatch.setattr(app_ctx, "send_escalation_sms", fake_send_sms)

    monkeypatch.setattr(
        app_ctx,
        "analyze_vitals",
        lambda payload: TriageResult(
            classification=TriageClassification.CRITICAL_SILENCE,
            reason="Critical silence",
            action="IMMEDIATE_ESCALATION",
            escalate=True,
        ),
    )

    response = api_request(
        "POST",
        "/webhooks/smallest/post-call",
        json={
            "call_id": "smallest_esc_001",
            "user_id": patient["id"],
            "status": "completed",
            "audio_metrics": {
                "avg_db": -60,
                "peak_db": -55,
                "speech_probability": 0.01,
                "silence_duration_sec": 25,
                "call_duration_sec": 30,
            },
            "transcript": [],
            "emotions": [],
            "metadata": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "escalated"
    assert sent["count"] == 1

    # Verify escalation was created in DB
    esc_list = api_request("GET", "/escalations").json()
    assert len(esc_list) >= 1


def test_post_call_analyze_transcript_creates_escalation_on_flags(app_ctx, api_request, monkeypatch):
    patient = _create_patient(api_request, name="Rin", phone="+1-555-6666")
    _create_db_call(app_ctx, patient["id"], "smallest_speech_001")

    monkeypatch.setattr(
        app_ctx,
        "analyze_vitals",
        lambda payload: TriageResult(
            classification=TriageClassification.SPEECH_DETECTED,
            reason="Speech present",
            action="ANALYZE_TRANSCRIPT",
            escalate=False,
        ),
    )

    monkeypatch.setattr(
        app_ctx,
        "process_transcript",
        lambda history, keywords: {
            "summary": "Patient reports severe pain",
            "sentiment_score": 2,
            "detected_flags": ["severe pain"],
            "recommended_action": "Escalate now",
        },
    )

    response = api_request(
        "POST",
        "/webhooks/smallest/post-call",
        json={
            "call_id": "smallest_speech_001",
            "user_id": patient["id"],
            "status": "completed",
            "audio_metrics": {
                "avg_db": -20,
                "peak_db": -8,
                "speech_probability": 0.8,
                "silence_duration_sec": 1,
                "call_duration_sec": 45,
            },
            "transcript": [
                {"speaker": "user", "text": "I am in severe pain", "start": 0, "end": 1, "word_timestamps": []}
            ],
            "emotions": [{"label": "pain", "confidence": 0.8}],
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    # Verify escalation was created
    esc_list = api_request("GET", "/escalations").json()
    assert len(esc_list) >= 1


def test_analytics_webhook_ignored_for_completed_call(app_ctx, api_request):
    patient = _create_patient(api_request, name="Lee", phone="+1-555-7777")
    db = app_ctx.get_db()
    try:
        rec = CallRecord(
            id="call_done_001",
            patient_id=patient["id"],
            state=CallState.COMPLETED,
            smallest_call_id="smallest_done_001",
        )
        db.add(rec)
        db.commit()
    finally:
        db.close()

    response = api_request(
        "POST",
        "/webhooks/smallest/analytics",
        json={
            "call_id": "smallest_done_001",
            "user_id": patient["id"],
            "audio_metrics": {
                "avg_db": -24,
                "peak_db": -10,
                "speech_probability": 0.7,
                "silence_duration_sec": 2,
                "call_duration_sec": 40,
            },
            "transcript": [],
            "emotions": [],
            "summary": None,
            "sentiment": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "already_processed"
