from __future__ import annotations


def test_seeded_patients_are_listed(app_ctx, api_request):
    response = api_request("GET", "/patients")
    assert response.status_code == 200
    patients = response.json()
    assert len(patients) >= 3


def test_create_patient_conversation_and_end_with_escalation(app_ctx, api_request):
    create_patient = api_request(
        "POST",
        "/patients",
        json={
            "name": "Test Patient",
            "phone": "+1-555-0100",
            "agent_persona": "Agent",
            "conversation_goal": "Check recovery",
            "system_prompt": "Be concise",
            "escalation_keywords": ["chest pain", "bleeding"],
        },
    )
    assert create_patient.status_code == 200
    patient_id = create_patient.json()["id"]

    create_conversation = api_request(
        "POST",
        "/patients/conversations/create",
        params={"patient_id": patient_id},
    )
    assert create_conversation.status_code == 200
    conversation_id = create_conversation.json()["id"]

    turn = api_request(
        "POST",
        f"/patients/{patient_id}/{conversation_id}",
        params={"message": "I have chest pain today"},
    )
    assert turn.status_code == 200
    assert turn.json().startswith("mocked-reply:")

    end = api_request("POST", f"/patients/{patient_id}/{conversation_id}/end")
    assert end.status_code == 200
    payload = end.json()
    assert payload["status"] == "ended"
    assert "chest pain" in payload["detected_flags"]
    assert payload["escalation_id"] is not None

    escalations = api_request("GET", "/escalations")
    assert escalations.status_code == 200
    assert any(e["id"] == payload["escalation_id"] for e in escalations.json())


def test_acknowledge_escalation(app_ctx, api_request):
    # Create a patient and trigger an escalation via call end.
    patient_id = "pt_demo_001"
    conv = api_request("POST", "/patients/conversations/create", params={"patient_id": patient_id}).json()
    conversation_id = conv["id"]

    api_request(
        "POST",
        f"/patients/{patient_id}/{conversation_id}",
        params={"message": "Emergency and chest pain"},
    )
    ended = api_request("POST", f"/patients/{patient_id}/{conversation_id}/end").json()
    escalation_id = ended["escalation_id"]

    ack = api_request("PATCH", f"/escalations/{escalation_id}/acknowledge")
    assert ack.status_code == 200
    body = ack.json()
    assert body["status"] == "acknowledged"
    assert body["acknowledged_at"] is not None
