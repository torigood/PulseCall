"""Acoustic triage logic — the 'Asleep vs. Dead' filter.

Parses Smallest.ai post-call analytics and classifies the audio environment
to decide whether to retry, escalate, or proceed with transcript analysis.
"""

from __future__ import annotations

import logging

from models import (
    AudioMetrics,
    EmotionDetection,
    SmallestAIPostCallPayload,
    TriageClassification,
    TriageResult,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (tuneable)
# ---------------------------------------------------------------------------
SILENCE_DB_THRESHOLD = -50.0        # avg_db below this → near-zero audio
BACKGROUND_NOISE_DB_THRESHOLD = -20.0  # avg_db above this with low speech → noisy env
SPEECH_PROBABILITY_THRESHOLD = 0.3  # above this → speech detected
SLEEPING_PEAK_DB = -35.0            # low avg_db with minor peaks → rhythmic/sleeping

DISTRESS_KEYWORDS = {"help", "fall", "fell", "pain", "hurt", "emergency", "can't breathe", "bleeding"}
DISTRESS_EMOTIONS = {"fear", "pain"}


# ---------------------------------------------------------------------------
# Core triage function
# ---------------------------------------------------------------------------
def analyze_vitals(payload: SmallestAIPostCallPayload) -> TriageResult:
    """Classify the call environment and decide on next action.

    Priority order:
    1. Emotion-based immediate escalation (fear/pain detected)
    2. Distress keyword detection in transcript
    3. Acoustic environment classification
    """
    metrics = payload.audio_metrics

    # -----------------------------------------------------------------------
    # 1. Emotion bypass — if fear/pain detected, escalate immediately
    # -----------------------------------------------------------------------
    distress_emotion = _check_distress_emotions(payload.emotions, payload.transcript)
    if distress_emotion is not None:
        return TriageResult(
            classification=TriageClassification.SPEECH_DETECTED,
            reason=f"Distress emotion detected: {distress_emotion.label} (confidence {distress_emotion.confidence:.2f})",
            action="IMMEDIATE_ESCALATION",
            escalate=True,
        )

    # -----------------------------------------------------------------------
    # 2. Distress keyword scan in transcript
    # -----------------------------------------------------------------------
    keyword_hit = _check_distress_keywords(payload.transcript)
    if keyword_hit:
        return TriageResult(
            classification=TriageClassification.SPEECH_DETECTED,
            reason=f"Distress keyword detected in transcript: '{keyword_hit}'",
            action="IMMEDIATE_ESCALATION",
            escalate=True,
        )

    # -----------------------------------------------------------------------
    # 3. Acoustic environment classification
    # -----------------------------------------------------------------------

    # Case A: High noise, low speech → background noise (shower, TV, etc.)
    if (
        metrics.avg_db > BACKGROUND_NOISE_DB_THRESHOLD
        and metrics.speech_probability < SPEECH_PROBABILITY_THRESHOLD
    ):
        return TriageResult(
            classification=TriageClassification.BACKGROUND_NOISE,
            reason=f"High ambient noise (avg_db={metrics.avg_db:.1f}) with low speech probability ({metrics.speech_probability:.2f})",
            action="SCHEDULE_RETRY",
            retry_delay_minutes=20,
            escalate=False,
        )

    # Case B: Total silence → critical, possible emergency
    if (
        metrics.avg_db < SILENCE_DB_THRESHOLD
        and metrics.speech_probability < 0.05
    ):
        return TriageResult(
            classification=TriageClassification.CRITICAL_SILENCE,
            reason=f"Total silence detected (avg_db={metrics.avg_db:.1f}, speech_prob={metrics.speech_probability:.2f})",
            action="IMMEDIATE_ESCALATION",
            escalate=True,
        )

    # Case C: Low rhythmic noise → likely sleeping
    if (
        metrics.avg_db < SLEEPING_PEAK_DB
        and metrics.peak_db > metrics.avg_db + 5  # minor peaks above baseline
        and metrics.speech_probability < SPEECH_PROBABILITY_THRESHOLD
    ):
        return TriageResult(
            classification=TriageClassification.LIKELY_SLEEPING,
            reason=f"Low rhythmic noise pattern (avg_db={metrics.avg_db:.1f}, peak_db={metrics.peak_db:.1f})",
            action="SCHEDULE_RETRY",
            retry_delay_minutes=60,
            escalate=False,
        )

    # Case D: Speech detected → proceed with LLM transcript analysis
    if metrics.speech_probability >= SPEECH_PROBABILITY_THRESHOLD:
        return TriageResult(
            classification=TriageClassification.SPEECH_DETECTED,
            reason=f"Speech detected (probability={metrics.speech_probability:.2f})",
            action="ANALYZE_TRANSCRIPT",
            escalate=False,
        )

    # Fallback: ambiguous — schedule a short retry
    return TriageResult(
        classification=TriageClassification.BACKGROUND_NOISE,
        reason=f"Ambiguous audio (avg_db={metrics.avg_db:.1f}, speech_prob={metrics.speech_probability:.2f})",
        action="SCHEDULE_RETRY",
        retry_delay_minutes=15,
        escalate=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_distress_emotions(
    emotions: list[EmotionDetection],
    transcript: list[TranscriptSegment],
) -> EmotionDetection | None:
    """Return the first distress emotion found, or None."""
    # Check top-level emotions list
    for em in emotions:
        if em.label.lower() in DISTRESS_EMOTIONS and em.confidence > 0.5:
            return em

    # Check per-segment emotions from Pulse STT
    for seg in transcript:
        if seg.emotion and seg.emotion.label.lower() in DISTRESS_EMOTIONS and seg.emotion.confidence > 0.5:
            return seg.emotion

    return None


def _check_distress_keywords(transcript: list[TranscriptSegment]) -> str | None:
    """Return the first distress keyword found in the transcript, or None."""
    for seg in transcript:
        text_lower = seg.text.lower()
        for kw in DISTRESS_KEYWORDS:
            if kw in text_lower:
                return kw
    return None
