from __future__ import annotations

from database import CallRecord
from models import CallState


def _create_patient(api_request, name="Alex", phone="+1-555-1111"):
    """Helper: create a patient via API and return the response dict."""
    return api_request(
        "POST",
        "/patients",
        json={"name": name, "phone": phone},
    ).json()


def test_create_and_list_patients(api_request):
    created = _create_patient(api_request)
    patient_id = created["id"]

    listed = api_request("GET", "/patients")
    assert listed.status_code == 200
    patients = listed.json()
    assert any(p["id"] == patient_id for p in patients)


def test_trigger_outbound_call_success(app_ctx, api_request, monkeypatch):
    created = _create_patient(api_request)

    async def fake_place_outbound_call(payload):
        return "smallest_call_123"

    monkeypatch.setattr(app_ctx, "place_outbound_call", fake_place_outbound_call)

    response = api_request(
        "POST",
        "/calls/outbound",
        params={"patient_id": created["id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "call_placed"
    assert body["smallest_call_id"] == "smallest_call_123"


def test_trigger_outbound_call_failure_sets_busy_retry(app_ctx, api_request, monkeypatch):
    created = _create_patient(api_request, name="Jamie", phone="+1-555-2222")

    async def fake_place_outbound_call(payload):
        return None

    monkeypatch.setattr(app_ctx, "place_outbound_call", fake_place_outbound_call)

    response = api_request("POST", "/calls/outbound", params={"patient_id": created["id"]})
    assert response.status_code == 502

    db = app_ctx.get_db()
    try:
        latest = db.query(CallRecord).filter(CallRecord.patient_id == created["id"]).order_by(CallRecord.created_at.desc()).first()
        assert latest is not None
        assert latest.state == CallState.BUSY_RETRY
    finally:
        db.close()


def test_call_history_returns_db_records(app_ctx, api_request):
    created = _create_patient(api_request, name="Morgan", phone="+1-555-3333")

    db = app_ctx.get_db()
    try:
        rec = CallRecord(
            id="call_hist_001",
            patient_id=created["id"],
            state=CallState.COMPLETED,
            summary="Recovered well",
            sentiment_score=4,
        )
        db.add(rec)
        db.commit()
    finally:
        db.close()

    history = api_request("GET", f"/call-history/{created['id']}")
    assert history.status_code == 200
    rows = history.json()
    assert len(rows) >= 1
    assert rows[0]["patient_id"] == created["id"]
