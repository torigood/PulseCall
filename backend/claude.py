from dotenv import load_dotenv
import os
import httpx
import json
from pathlib import Path

# Set .env file path based on current file location
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Use OPENROUTER_API_KEY since that is what you have configured
API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

if not API_KEY:
    raise ValueError("Neither OPENROUTER_API_KEY nor ANTHROPIC_API_KEY is set in .env file.")

# OpenRouter model string for Claude 3.5 Haiku
MODEL = "google/gemini-2.0-flash-exp:free"
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

def respond(user_message: str, history: list, system_prompt: str) -> str:
    # OpenRouter uses OpenAI format: system prompt goes inside the messages list
    messages = [{"role": "system", "content": system_prompt}] + history
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000", # Optional, for OpenRouter rankings
        "X-Title": "PulseCall",
    }
    
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 150,
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(BASE_URL, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


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

    # Simplified tool-like behavior for OpenRouter/OpenAI format
    prompt = (
        f"Process this call transcript. The escalation keywords to watch for are: {keywords_str}\n\n"
        f"Transcript:\n{formatted}\n\n"
        "Return ONLY a JSON object matching this schema:\n"
        f"{json.dumps(PROCESS_CALL_TOOL['input_schema'])}"
    )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "max_tokens": 1024,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(BASE_URL, headers=headers, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as e:
        print(f"Error processing transcript: {e}")
        return {
            "summary": "Unable to process transcript.",
            "sentiment_score": 3,
            "detected_flags": [],
            "recommended_action": "Manual review recommended.",
        }
