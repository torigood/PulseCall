"""Pydantic models for Smallest.ai webhook payloads and internal state."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Call state machine
# ---------------------------------------------------------------------------
class CallState(str, Enum):
    PENDING = "PENDING"
    BUSY_RETRY = "BUSY_RETRY"
    SILENT_RETRY = "SILENT_RETRY"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"


# ---------------------------------------------------------------------------
# Acoustic triage classification
# ---------------------------------------------------------------------------
class TriageClassification(str, Enum):
    BACKGROUND_NOISE = "BACKGROUND_NOISE"
    CRITICAL_SILENCE = "CRITICAL_SILENCE"
    LIKELY_SLEEPING = "LIKELY_SLEEPING"
    SPEECH_DETECTED = "SPEECH_DETECTED"


# ---------------------------------------------------------------------------
# Smallest.ai Pulse STT word-level detail
# ---------------------------------------------------------------------------
class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Smallest.ai post-call analytics webhook payload
# ---------------------------------------------------------------------------
class AudioMetrics(BaseModel):
    avg_db: float = Field(description="Average decibel level of the call audio")
    peak_db: float = Field(0.0, description="Peak decibel level")
    speech_probability: float = Field(
        description="0.0-1.0 probability that speech was present"
    )
    silence_duration_sec: float = Field(
        0.0, description="Total seconds of silence detected"
    )
    call_duration_sec: float = Field(0.0, description="Total call duration in seconds")


class EmotionDetection(BaseModel):
    label: str = Field(description="Detected emotion: neutral, fear, pain, joy, anger, sadness")
    confidence: float = Field(0.0, description="0.0-1.0 confidence of the detection")


class TranscriptSegment(BaseModel):
    speaker: str = Field(description="'agent' or 'user'")
    text: str
    start: float = 0.0
    end: float = 0.0
    word_timestamps: list[WordTimestamp] = Field(default_factory=list)
    emotion: EmotionDetection | None = None


class SmallestAIPostCallPayload(BaseModel):
    """Webhook payload sent by Smallest.ai after a call ends."""
    call_id: str
    user_id: str
    campaign_id: str | None = None
    status: str = Field(description="completed, no_answer, busy, failed")
    audio_metrics: AudioMetrics
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    emotions: list[EmotionDetection] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SmallestAIAnalyticsPayload(BaseModel):
    """Webhook payload sent after Smallest.ai analytics processing completes."""
    call_id: str
    user_id: str
    audio_metrics: AudioMetrics
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    emotions: list[EmotionDetection] = Field(default_factory=list)
    summary: str | None = None
    sentiment: str | None = None


# ---------------------------------------------------------------------------
# Triage result
# ---------------------------------------------------------------------------
class TriageResult(BaseModel):
    classification: TriageClassification
    reason: str
    action: str
    retry_delay_minutes: int | None = None
    escalate: bool = False


# ---------------------------------------------------------------------------
# Outbound call request (sent to Smallest.ai)
# ---------------------------------------------------------------------------
class OutboundCallRequest(BaseModel):
    user_id: str
    user_name: str
    phone_number: str
    campaign_id: str | None = None
    system_prompt: str = ""
    voice_id: str = "emily"
