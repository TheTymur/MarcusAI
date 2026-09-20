from openai import OpenAI
import json

def multiply(a: int, b: int) -> int:
    return a * b

client = OpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1"
)

try:
    response = client.chat.completions.create(
        model="lfm2:24b-a2b",
        messages=[{"role": "user", "content": "hi"}],
        tools=[multiply]
    )
    print("SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")
