from google import genai
from google.genai import types
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
gemini_client = genai.Client(api_key=os.getenv("API_KEY_GEMINI"))
ollama_client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")

shared_history = [] # list of types.Content

# 1. Local (Ollama) talks
user_msg = "Hello, my name is Tymur."
shared_history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_msg)]))

# convert to OpenAI
local_messages = []
for msg in shared_history:
    role = "assistant" if msg.role == "model" else msg.role
    text = ""
    if getattr(msg, "parts", None):
        for p in msg.parts:
            if getattr(p, "text", None):
                text += p.text
    local_messages.append({"role": role, "content": text})

resp = ollama_client.chat.completions.create(model="qwen2.5:14b", messages=local_messages)
text_resp = resp.choices[0].message.content
shared_history.append(types.Content(role="model", parts=[types.Part.from_text(text=text_resp)]))

print("Ollama:", text_resp)

# 2. Gemini talks
user_msg_2 = "What is my name?"
# Do NOT append to shared_history here, send_message does it!
chat = gemini_client.chats.create(
    model="gemini-3.1-flash-lite",
    history=shared_history
)

response = chat.send_message(user_msg_2)
print("Gemini:", response.text)

# Pull updated history back
shared_history = chat.get_history()
print("History length:", len(shared_history))
for h in shared_history:
    print(h.role)
