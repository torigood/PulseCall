from dotenv import load_dotenv
import os
from anthropic import Anthropic

load_dotenv()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

MODEL = "claude-haiku-4-5-20251001"

client = Anthropic(api_key=ANTHROPIC_API_KEY)


def respond(user_message: str, history: list, system_prompt: str) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=150,
        system=system_prompt,
        messages=history,
    )
    return response.content[0].text


PROCESS_CALL_TOOL = {
    "name": "process_call",
    "description": "Extract structured insights from a completed call transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-3 sentence summary of the conversation",
            },
            "sentiment_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "1=very distressed, 5=positive and stable",
            },
            "detected_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concerning phrases or topics identified in the conversation",
            },
            "recommended_action": {
                "type": "string",
                "description": "Suggested next step for the human responder",
            },
        },
        "required": [
            "summary",
            "sentiment_score",
            "detected_flags",
            "recommended_action",
        ],
    },
}


def process_transcript(transcript: list[dict[str, str]], escalation_keywords: list[str]) -> dict:
    formatted = "\n".join(
        f"{'Recipient' if t['role'] == 'user' else 'Agent'}: {t['content']}"
        for t in transcript
    )
    keywords_str = ", ".join(escalation_keywords) if escalation_keywords else "none specified"

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[PROCESS_CALL_TOOL],
        tool_choice={"type": "tool", "name": "process_call"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Process this call transcript. The escalation keywords to watch for are: {keywords_str}\n\n"
                    f"Transcript:\n{formatted}"
                ),
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use":
            return block.input

    return {
        "summary": "Unable to process transcript.",
        "sentiment_score": 3,
        "detected_flags": [],
        "recommended_action": "Manual review recommended.",
    }

