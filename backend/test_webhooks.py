"""Mock webhook payloads for testing triage logic.

Usage:
    python test_webhooks.py                  # Run all scenarios against localhost:8000
    python test_webhooks.py --unit           # Run unit tests (no server needed)
    python test_webhooks.py --url http://...  # Target a different server
"""

from __future__ import annotations

import argparse
import sys

import httpx

from models import (
    AudioMetrics,
    EmotionDetection,
    SmallestAIPostCallPayload,
    TranscriptSegment,
    WordTimestamp,
)
from triage import analyze_vitals, TriageClassification


# ---------------------------------------------------------------------------
# Mock payloads
# ---------------------------------------------------------------------------
def make_shower_noise_payload() -> SmallestAIPostCallPayload:
    """High ambient noise, low speech probability → BACKGROUND_NOISE."""
    return SmallestAIPostCallPayload(
        call_id="test_shower_001",
        user_id="usr_test_001",
        campaign_id="cmp_demo_001",
        status="completed",
        audio_metrics=AudioMetrics(
            avg_db=-15.0,
            peak_db=-8.0,
            speech_probability=0.08,
            silence_duration_sec=2.0,
            call_duration_sec=30.0,
        ),
        transcript=[],
        emotions=[],
    )


def make_silent_emergency_payload() -> SmallestAIPostCallPayload:
    """Near-zero audio, zero speech → CRITICAL_SILENCE."""
    return SmallestAIPostCallPayload(
        call_id="test_silence_001",
        user_id="usr_test_001",
        campaign_id="cmp_demo_001",
        status="completed",
        audio_metrics=AudioMetrics(
            avg_db=-65.0,
            peak_db=-60.0,
            speech_probability=0.01,
            silence_duration_sec=28.0,
            call_duration_sec=30.0,
        ),
        transcript=[],
        emotions=[],
    )


def make_sleeping_payload() -> SmallestAIPostCallPayload:
    """Low rhythmic noise with minor peaks → LIKELY_SLEEPING."""
    return SmallestAIPostCallPayload(
        call_id="test_sleeping_001",
        user_id="usr_test_001",
        campaign_id="cmp_demo_001",
        status="completed",
        audio_metrics=AudioMetrics(
            avg_db=-42.0,
            peak_db=-30.0,
            speech_probability=0.05,
            silence_duration_sec=20.0,
            call_duration_sec=30.0,
        ),
        transcript=[],
        emotions=[],
    )


def make_normal_speech_payload() -> SmallestAIPostCallPayload:
    """Normal conversation with speech detected → SPEECH_DETECTED."""
    return SmallestAIPostCallPayload(
        call_id="test_speech_001",
        user_id="usr_test_001",
        campaign_id="cmp_demo_001",
        status="completed",
        audio_metrics=AudioMetrics(
            avg_db=-25.0,
            peak_db=-10.0,
            speech_probability=0.85,
            silence_duration_sec=5.0,
            call_duration_sec=60.0,
        ),
        transcript=[
            TranscriptSegment(
                speaker="agent",
                text="Hi, this is Claire from PulseCall. How are you feeling today?",
                start=0.0,
                end=3.5,
            ),
            TranscriptSegment(
                speaker="user",
                text="I'm doing okay, just a bit tired. My medication is working fine.",
                start=4.0,
                end=8.0,
                emotion=EmotionDetection(label="neutral", confidence=0.9),
            ),
        ],
        emotions=[EmotionDetection(label="neutral", confidence=0.85)],
    )


def make_distress_keyword_payload() -> SmallestAIPostCallPayload:
    """Speech with distress keywords → SPEECH_DETECTED + escalate."""
    return SmallestAIPostCallPayload(
        call_id="test_distress_kw_001",
        user_id="usr_test_001",
        campaign_id="cmp_demo_001",
        status="completed",
        audio_metrics=AudioMetrics(
            avg_db=-22.0,
            peak_db=-10.0,
            speech_probability=0.75,
            silence_duration_sec=3.0,
            call_duration_sec=45.0,
        ),
        transcript=[
            TranscriptSegment(
                speaker="agent",
                text="How are you feeling today?",
                start=0.0,
                end=2.0,
            ),
            TranscriptSegment(
                speaker="user",
                text="I fell this morning and I'm in a lot of pain. I need help.",
                start=2.5,
                end=6.0,
                emotion=EmotionDetection(label="pain", confidence=0.7),
            ),
        ],
        emotions=[EmotionDetection(label="pain", confidence=0.7)],
    )


def make_fear_emotion_payload() -> SmallestAIPostCallPayload:
    """Fear emotion detected even with low speech → immediate escalation."""
    return SmallestAIPostCallPayload(
        call_id="test_fear_001",
        user_id="usr_test_001",
        campaign_id="cmp_demo_001",
        status="completed",
        audio_metrics=AudioMetrics(
            avg_db=-30.0,
            peak_db=-18.0,
            speech_probability=0.20,
            silence_duration_sec=15.0,
            call_duration_sec=30.0,
        ),
        transcript=[
            TranscriptSegment(
                speaker="user",
                text="...",
                start=10.0,
                end=12.0,
                emotion=EmotionDetection(label="fear", confidence=0.8),
            ),
        ],
        emotions=[EmotionDetection(label="fear", confidence=0.8)],
    )


def make_busy_payload() -> SmallestAIPostCallPayload:
    """Call was busy / not answered."""
    return SmallestAIPostCallPayload(
        call_id="test_busy_001",
        user_id="usr_test_001",
        campaign_id="cmp_demo_001",
        status="busy",
        audio_metrics=AudioMetrics(
            avg_db=0.0,
            peak_db=0.0,
            speech_probability=0.0,
            silence_duration_sec=0.0,
            call_duration_sec=0.0,
        ),
        transcript=[],
        emotions=[],
    )


# ---------------------------------------------------------------------------
# All scenarios
# ---------------------------------------------------------------------------
SCENARIOS = [
    ("Shower / Background Noise", make_shower_noise_payload),
    ("Silent Emergency", make_silent_emergency_payload),
    ("Likely Sleeping", make_sleeping_payload),
    ("Normal Speech", make_normal_speech_payload),
    ("Distress Keywords (fall, pain, help)", make_distress_keyword_payload),
    ("Fear Emotion (low speech)", make_fear_emotion_payload),
    ("Busy / No Answer", make_busy_payload),
]


# ---------------------------------------------------------------------------
# Unit tests (no server needed)
# ---------------------------------------------------------------------------
def run_unit_tests() -> None:
    print("=" * 60)
    print("UNIT TESTS — Triage Logic")
    print("=" * 60)

    passed = 0
    failed = 0

    tests = [
        ("Shower Noise → BACKGROUND_NOISE", make_shower_noise_payload, TriageClassification.BACKGROUND_NOISE, False),
        ("Silent Emergency → CRITICAL_SILENCE + escalate", make_silent_emergency_payload, TriageClassification.CRITICAL_SILENCE, True),
        ("Sleeping → LIKELY_SLEEPING", make_sleeping_payload, TriageClassification.LIKELY_SLEEPING, False),
        ("Normal Speech → SPEECH_DETECTED", make_normal_speech_payload, TriageClassification.SPEECH_DETECTED, False),
        ("Distress Keywords → escalate", make_distress_keyword_payload, TriageClassification.SPEECH_DETECTED, True),
        ("Fear Emotion → escalate", make_fear_emotion_payload, TriageClassification.SPEECH_DETECTED, True),
    ]

    for name, factory, expected_class, expected_escalate in tests:
        payload = factory()
        result = analyze_vitals(payload)
        class_ok = result.classification == expected_class
        escalate_ok = result.escalate == expected_escalate

        if class_ok and escalate_ok:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            if not class_ok:
                print(f"     Expected classification={expected_class.value}, got={result.classification.value}")
            if not escalate_ok:
                print(f"     Expected escalate={expected_escalate}, got={result.escalate}")
            failed += 1

        print(f"     → {result.classification.value} | {result.action} | {result.reason[:80]}")

    print()
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    return failed == 0


# ---------------------------------------------------------------------------
# Integration tests (sends to running server)
# ---------------------------------------------------------------------------
def run_integration_tests(base_url: str) -> None:
    print("=" * 60)
    print(f"INTEGRATION TESTS — Sending webhooks to {base_url}")
    print("=" * 60)

    for name, factory in SCENARIOS:
        payload = factory()
        print(f"\n--- {name} ---")
        print(f"  call_id: {payload.call_id}")
        print(f"  status:  {payload.status}")
        print(f"  avg_db:  {payload.audio_metrics.avg_db}")
        print(f"  speech:  {payload.audio_metrics.speech_probability}")

        try:
            resp = httpx.post(
                f"{base_url}/webhooks/smallest/post-call",
                json=payload.model_dump(),
                timeout=10.0,
            )
            print(f"  HTTP {resp.status_code}: {resp.json()}")
        except Exception as e:
            print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test PulseCall triage webhooks")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only (no server needed)")
    parser.add_argument("--url", default="http://localhost:8000", help="Server URL for integration tests")
    args = parser.parse_args()

    if args.unit:
        success = run_unit_tests()
        sys.exit(0 if success else 1)
    else:
        # Run both
        success = run_unit_tests()
        print()
        run_integration_tests(args.url)
        sys.exit(0 if success else 1)
