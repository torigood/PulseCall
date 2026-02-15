"""Escalation notifications via Twilio SMS and email placeholders."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Twilio configuration
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
ESCALATION_TO_NUMBER = os.getenv("ESCALATION_TO_NUMBER", "")
TRANSCRIPT_BASE_URL = os.getenv("TRANSCRIPT_BASE_URL", "http://localhost:8000")

_twilio_client: TwilioClient | None = None


def _get_twilio_client() -> TwilioClient | None:
    global _twilio_client
    if _twilio_client is not None:
        return _twilio_client
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        _twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        return _twilio_client
    logger.warning("Twilio credentials not configured — SMS will be logged only")
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def send_escalation_sms(
    user_name: str,
    triage_reason: str,
    call_id: str,
    to_number: str | None = None,
) -> bool:
    """Send an emergency SMS via Twilio.

    Returns True if the message was sent (or logged), False on error.
    """
    recipient = to_number or ESCALATION_TO_NUMBER
    transcript_link = f"{TRANSCRIPT_BASE_URL}/calls/{call_id}"

    body = (
        f"🚨 PulseCall ESCALATION\n"
        f"Patient: {user_name}\n"
        f"Reason: {triage_reason}\n"
        f"Transcript: {transcript_link}\n"
        f"Immediate attention required."
    )

    client = _get_twilio_client()
    if client is None or not recipient or not TWILIO_FROM_NUMBER:
        logger.info("[MOCK SMS] To=%s | %s", recipient or "NO_NUMBER", body)
        return True

    try:
        message = client.messages.create(
            body=body,
            from_=TWILIO_FROM_NUMBER,
            to=recipient,
        )
        logger.info("SMS sent: sid=%s to=%s", message.sid, recipient)
        return True
    except Exception:
        logger.exception("Failed to send escalation SMS to %s", recipient)
        return False


def send_escalation_email(
    user_name: str,
    triage_reason: str,
    call_id: str,
    to_email: str | None = None,
) -> bool:
    """Placeholder for email escalation — logs the alert for now."""
    transcript_link = f"{TRANSCRIPT_BASE_URL}/calls/{call_id}"
    logger.info(
        "[EMAIL ESCALATION] To=%s | Patient=%s | Reason=%s | Link=%s",
        to_email or "NOT_CONFIGURED",
        user_name,
        triage_reason,
        transcript_link,
    )
    return True
