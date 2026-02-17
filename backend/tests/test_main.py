from __future__ import annotations

from typing import Any


def make_patient_payload(system_prompt: str = "Patient prompt", keywords: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": "Test Patient",
        "phone": "+1-555-0001",
        "agent_persona": "Helpful support rep",
        "conversation_goal": "Understand issue and help",
        "system_prompt": system_prompt,
        "escalation_keywords": keywords or ["cancel"],
    }


def test_create_patient_returns_id(api_request):
    response = api_request("POST", "/patients", json=make_patient_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["id"].startswith("pt_")
    assert body["name"] == "Test Patient"


def test_create_conversation_initializes_required_fields(api_request):
    create_patient = api_request("POST", "/patients", json=make_patient_payload())
    patient_id = create_patient.json()["id"]

    response = api_request(
        "POST",
        "/patients/conversations/create",
        params={"patient_id": patient_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == patient_id
    assert body["status"] == "active"
    assert body["start_time"]
    assert body["end_time"] is None
    assert body["history"] == []


def test_get_response_updates_history(app_ctx, api_request):
    create_patient = api_request(
        "POST",
        "/patients",
        json=make_patient_payload(system_prompt="SYSTEM_PER_PATIENT"),
    )
    patient_id = create_patient.json()["id"]

    create_conversation = api_request(
        "POST",
        "/patients/conversations/create",
        params={"patient_id": patient_id},
    )
    conversation_id = create_conversation.json()["id"]

    response = api_request(
        "POST",
        f"/patients/{patient_id}/{conversation_id}",
        params={"message": "hello there"},
    )

    assert response.status_code == 200
    assert response.json() == "mocked-reply:hello there"

    # Verify history via conversations list endpoint
    convs = api_request("GET", "/conversations").json()
    conv = next(c for c in convs if c["id"] == conversation_id)
    assert len(conv["history"]) == 2
    assert conv["history"][0]["role"] == "user"
    assert conv["history"][1]["role"] == "assistant"


def test_end_call_sets_inactive_and_end_time(app_ctx, api_request):
    create_patient = api_request("POST", "/patients", json=make_patient_payload())
    patient_id = create_patient.json()["id"]

    create_conversation = api_request(
        "POST",
        "/patients/conversations/create",
        params={"patient_id": patient_id},
    )
    conversation_id = create_conversation.json()["id"]

    api_request(
        "POST",
        f"/patients/{patient_id}/{conversation_id}",
        params={"message": "I might cancel this"},
    )

    end_response = api_request(
        "POST",
        f"/patients/{patient_id}/{conversation_id}/end",
    )

    assert end_response.status_code == 200
    body = end_response.json()
    assert body["conversation_id"] == conversation_id
    assert "cancel" in body["detected_flags"]

    # Verify conversation is now inactive
    convs = api_request("GET", "/conversations").json()
    conv = next(c for c in convs if c["id"] == conversation_id)
    assert conv["end_time"] is not None


def test_get_response_rejects_wrong_patient_for_conversation(api_request):
    p1 = api_request("POST", "/patients", json=make_patient_payload())
    p2 = api_request("POST", "/patients", json=make_patient_payload())
    patient_1 = p1.json()["id"]
    patient_2 = p2.json()["id"]

    create_conversation = api_request(
        "POST",
        "/patients/conversations/create",
        params={"patient_id": patient_1},
    )
    conversation_id = create_conversation.json()["id"]

    response = api_request(
        "POST",
        f"/patients/{patient_2}/{conversation_id}",
        params={"message": "hello"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Conversation does not belong to patient"


def test_list_conversations_returns_created_conversation(api_request):
    create_patient = api_request("POST", "/patients", json=make_patient_payload())
    patient_id = create_patient.json()["id"]

    create_conversation = api_request(
        "POST",
        "/patients/conversations/create",
        params={"patient_id": patient_id},
    )
    conversation_id = create_conversation.json()["id"]

    response = api_request("GET", "/conversations")

    assert response.status_code == 200
    conversations = response.json()
    assert any(c.get("id") == conversation_id for c in conversations)
