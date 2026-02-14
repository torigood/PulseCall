from dotenv import load_dotenv
import os
from anthropic import Anthropic

# Async (better for FastAPI)
# from anthropic import AsyncAnthropic
# client = AsyncAnthropic(api_key="sk-ant-...")

load_dotenv()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# switch to claude-sonnet-4-5-20250929 for demo
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=100,
    messages=[{"role": "user", "content": "Say hello!"}]
)

print(response.content[0].text)
