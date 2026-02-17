from __future__ import annotations

import json


class FakeResponse:
    def __init__(self, status_code: int, json_body=None, text: str = "", content: bytes = b""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text
        self.content = content

    def json(self):
        if self._json_body is None:
            raise ValueError("No JSON body")
        return self._json_body


class FakeAsyncClient:
    def __init__(self, queue):
        self.queue = queue

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None, content=None):
        assert self.queue, f"No fake response queued for URL: {url}"
        return self.queue.pop(0)


def patch_async_client(app_ctx, monkeypatch, queued):
    """Patch httpx.AsyncClient without breaking ASGI test transport usage."""
    real_async_client = app_ctx.httpx.AsyncClient

    def factory(*args, **kwargs):
        # Keep the real ASGI transport client used by api_request fixture.
        if "transport" in kwargs:
            return real_async_client(*args, **kwargs)
        # Use fake client for outbound HTTP made by the endpoint itself.
        return FakeAsyncClient(queued)

    monkeypatch.setattr(app_ctx.httpx, "AsyncClient", factory)


def test_voice_chat_initial_success(app_ctx, api_request, monkeypatch):
    app_ctx.OPENROUTER_API_KEY = "openrouter-test"
    app_ctx.SMALLEST_AI_API_KEY = "smallest-test"

    queued = [
        FakeResponse(
            200,
            json_body={"choices": [{"message": {"content": "Hi there"}}]},
        ),
        FakeResponse(200, content=b"fake-mp3-bytes"),
    ]

    patch_async_client(app_ctx, monkeypatch, queued)

    # Use a seeded patient ID
    patient_id = "pt_demo_001"
    response = api_request(
        "POST",
        "/voice/chat",
        json={"patient_id": patient_id, "trigger": "initial", "history": []},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Hi there"
    assert body["audio"] is not None
    assert body["isEnding"] is False


def test_voice_chat_requires_transcription_when_not_initial(app_ctx, api_request):
    app_ctx.OPENROUTER_API_KEY = "openrouter-test"
    app_ctx.SMALLEST_AI_API_KEY = "smallest-test"

    patient_id = "pt_demo_001"
    response = api_request(
        "POST",
        "/voice/chat",
        json={"patient_id": patient_id, "history": []},
    )
    assert response.status_code == 400


def test_voice_transcribe_success(app_ctx, api_request, monkeypatch):
    app_ctx.SMALLEST_AI_API_KEY = "smallest-test"

    queued = [FakeResponse(200, json_body={"transcription": "hello world"})]
    patch_async_client(app_ctx, monkeypatch, queued)

    response = api_request(
        "POST",
        "/voice/transcribe",
        headers={"content-type": "audio/webm"},
        content=b"fake-audio",
    )
    assert response.status_code == 200
    assert response.json()["transcription"] == "hello world"


def test_voice_summary_parses_json_from_model(app_ctx, api_request, monkeypatch):
    app_ctx.OPENROUTER_API_KEY = "openrouter-test"

    model_json = {
        "painLevel": 4,
        "symptoms": ["knee pain"],
        "ptExercise": True,
        "medications": "taking as prescribed",
        "concerns": "none",
        "recommendation": "continue PT",
        "followUp": "routine",
        "summary": "Patient improving.",
    }

    queued = [
        FakeResponse(
            200,
            json_body={
                "choices": [
                    {"message": {"content": "```json\n" + json.dumps(model_json) + "\n```"}}
                ]
            },
        )
    ]
    patch_async_client(app_ctx, monkeypatch, queued)

    response = api_request(
        "POST",
        "/voice/summary",
        json={
            "history": [
                {"role": "assistant", "content": "How are you feeling?"},
                {"role": "user", "content": "Better today."},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["painLevel"] == 4
    assert body["summary"] == "Patient improving."
