from openai import OpenAI
import os

ollama_client = OpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1"
)

prompt = """You are a router for an email assistant. 
Does the following user request require accessing tools (like reading emails, sending emails, searching emails, deleting, labeling, using memory) or complex reasoning? 
Keep the recent chat history in mind to understand context (e.g. if the user is providing a fact to save, route to GEMINI).

Recent Chat History:


User request: hey
Reply with exactly 'GEMINI' if it requires tools, complex logic, or if the user is saying goodbye/exiting (so it can use the exit tool).
Reply with exactly 'LOCAL' if it's just a general chat, greeting, or simple conversational question.

CRITICAL INSTRUCTION: You MUST output ONLY the word "GEMINI" or "LOCAL". Do not include ANY other text, explanation, or punctuation.
Route:"""

response = ollama_client.chat.completions.create(
    model="lfm2:24b-a2b",
    messages=[{"role": "system", "content": prompt}]
)
print("RAW RESPONSE:", repr(response.choices[0].message.content))
