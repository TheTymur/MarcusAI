import json
import os
import re
import sys
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI

from agent.utils import get_tools_and_map
from tools import multiply, GmailClient, exit_agent, get_current_date_and_time, VectorMemory

LOCAL_MODEL = "lfm2:24b-a2b"
GEMINI_MODEL = "gemini-3.1-flash-lite"
MAX_LOCAL_MESSAGES = 20  
MAX_GEMINI_HISTORY = 40      

with open("context/identity.md", "r") as f:
    role = f.read()
with open("context/user.md", "r") as f:
    user = f.read()
with open("context/soul.md", "r") as f:
    personality = f.read()

base_instructions = role + "\n" + user + "\n" + personality

gmail_client = GmailClient()
memory_client = VectorMemory()

available_tools = [multiply, gmail_client.read_recent_emails, gmail_client.create_label, gmail_client.apply_label,
    gmail_client.delete_label, gmail_client.remove_label, gmail_client.list_labels, gmail_client.count_messages_in_label,
    gmail_client.search_emails, gmail_client.delete_message, gmail_client.delete_draft, gmail_client.list_drafts, exit_agent,
    gmail_client.create_draft, gmail_client.modify_draft, gmail_client.send_draft, gmail_client.read_email_content, get_current_date_and_time,
    gmail_client.create_response_draft, gmail_client.forward_email, memory_client.search_knowledge_base, memory_client.save_user_preference,
    gmail_client.search_local_database, gmail_client.unsubscribe_from_email, gmail_client.untrash_message]

load_dotenv()
gemini_api_key = os.getenv("API_KEY_GEMINI")

gemini_client = genai.Client(api_key=gemini_api_key)
ollama_client = OpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1",
)

def make_gemini_chat(instructions, history=None):
    return gemini_client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=instructions,
            tools=available_tools,
        ),
        history=history,
    )


def extract_text(response):
    """Pull plain text out of a Gemini response (empty string if none)."""
    text = ""
    if getattr(response, "candidates", None):
        parts = getattr(response.candidates[0].content, "parts", None) or []
        for part in parts:
            if getattr(part, "text", None):
                text += part.text
    elif getattr(response, "text", None):
        text = response.text
    return text


def is_plain_user_turn(content):
    """A user turn that is real user text, not a function_response."""
    if content.role != "user" or not content.parts:
        return False
    return all(getattr(p, "function_response", None) is None for p in content.parts)


def safe_trim(history, keep=4):
    """
    Keep roughly the last `keep` turns, but start on a plain user turn so we never
    begin with a model turn or split a function_call / function_response pair.
    Returns [] (fresh chat) if no safe start exists in the window.
    """
    history = list(history)
    if len(history) <= keep:
        return history
    for i in range(len(history) - keep, len(history)):
        if is_plain_user_turn(history[i]):
            return history[i:]
    return []


def send_with_retry(chat, text, max_retries=5, base_delay=2):
    for attempt in range(max_retries + 1):
        try:
            return chat.send_message(text)
        except Exception as e:
            error_msg = str(e).lower()
            if "connection" in error_msg or "network" in error_msg or "socket" in error_msg:
                print(f"\n[System Error]: Connection error. {e}\n")
                return None
            if ("503" in error_msg or "unavailable" in error_msg) and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                print(f"\n[System Info]: API is busy (503). Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            if ("429" in error_msg or "resource_exhausted" in error_msg) and attempt < max_retries:
                match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg)
                delay = float(match.group(1)) + 1 if match else base_delay * (2 ** attempt)
                print(f"\n[System Info]: Rate limit reached (429). Waiting for {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            print(f"\n[System Error]: Failed to generate response. {e}\n")
            return None
    return None




def start_agent():
    recent_memory = memory_client.get_recent_summaries(limit=3)
    current_instructions = base_instructions + "\n" + recent_memory

    gemini_chat = make_gemini_chat(current_instructions)

    local_messages = [{"role": "system", "content": current_instructions}]
    local_tools, local_tool_map = get_tools_and_map([exit_agent, memory_client.search_knowledge_base, gmail_client.search_local_database])

    def commit_local(*messages):
        """Append to local history and cap its length (system prompt is always kept)."""
        local_messages.extend(messages)
        overflow = len(local_messages) - 1 - MAX_LOCAL_MESSAGES
        if overflow > 0:
            del local_messages[1:1 + overflow]

    def sync_to_gemini(user_input, text_response):
        """Give Gemini the exchange it didn't see by rebuilding the chat with updated history."""
        nonlocal gemini_chat
        history = list(gemini_chat.get_history())
        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=text_response)]))
        gemini_chat = make_gemini_chat(current_instructions, history=history)

    def run_local(user_input):
        """
        Fallback only: Try to answer with the local model using local database tools.
        Returns "done" (handled) or "error".
        Nothing is added to local_messages unless the call succeeds.
        """
        extra = (
            "CRITICAL INSTRUCTION: You are running in OFFLINE FALLBACK MODE because the main cloud AI is down. "
            "You currently have NO access to live email tools (you CANNOT read live emails, send emails, or delete emails). "
            "If the user asks you to take an action on live emails, you MUST NOT pretend to do it. Instead, explicitly state that you cannot perform the action because you are offline.\n\n"
            "However, you DO have access to two local databases via your tools. You MUST USE YOUR TOOLS to search them instead of guessing:\n"
            "1. `search_local_database(sender=..., subject=...)`: Use this to search the local SQL database. It returns EXACT METADATA: Email ID, Date/Timestamp, Sender, Recipient, and Subject. Use this if the user asks 'when was the last email received?' or 'what is the date?'. It does NOT return the email body text.\n"
            "2. `search_knowledge_base(query=...)`: Use this to search the local vector database. It contains the FULL TEXT BODY of recent emails (chunked with their metadata headers), as well as your stored User Preferences, and past conversation summaries. Use this if the user asks 'what did the email say?' or 'what are my preferences?'.\n\n"
            "Always inform the user that you are offline, and then rely strictly on the results returned by these two tools."
        )

        user_msg = {"role": "user", "content": user_input}
        call_messages = local_messages + [{"role": "system", "content": extra}, user_msg]

        try:
            response = ollama_client.chat.completions.create(
                model=LOCAL_MODEL,
                messages=call_messages,
                tools=local_tools,
            )
            message = response.choices[0].message

            if message.tool_calls:
                valid_call = False
                for tool_call in message.tool_calls:
                    func = local_tool_map.get(tool_call.function.name)
                    if func:
                        func(**json.loads(tool_call.function.arguments))
                        valid_call = True
                    else:
                        # Hallucinated a tool it doesn't have
                        return "error"
                
                if valid_call:
                    commit_local(user_msg)
                    return "done"
                return "error"

            text_response = (message.content or "").strip()
            if not text_response:
                raise ValueError("local model returned an empty response")

            commit_local(user_msg, {"role": "assistant", "content": text_response})
            print(f"\nAI (Local): {text_response}\n")
            sync_to_gemini(user_input, text_response)
            return "done"

        except Exception as e:
            print(f"[System Warning]: Local model failed: {e}")
            return "error"

    def run_gemini(user_input):
        nonlocal gemini_chat

        response = send_with_retry(gemini_chat, user_input)

        if response is None:
            print("[System Info]: Gemini unavailable, falling back to the local model (no email tools).")
            if run_local(user_input) != "done":
                print("[System Error]: Both models failed. Please try again in a moment.\n")
            return

        text_response = extract_text(response)
        commit_local({"role": "user", "content": user_input})
        if text_response:
            print("\nAI (Gemini): " + text_response + "\n")
            commit_local({"role": "assistant", "content": text_response})
        else:
            finish_reason = getattr(response.candidates[0], "finish_reason", "UNKNOWN") if getattr(response, "candidates", None) else "UNKNOWN"
            print(f"\n[System Warning]: Gemini returned an empty text response. Finish reason: {finish_reason}")
            print(f"Raw response: {response}\n")

        maybe_summarize_and_trim()

    def maybe_summarize_and_trim():
        nonlocal gemini_chat
        full_history = list(gemini_chat.get_history())
        if len(full_history) <= MAX_GEMINI_HISTORY:
            return

        history_text = ""
        for msg in full_history:
            if getattr(msg, "parts", None) and getattr(msg.parts[0], "text", None):
                history_text += f"{msg.role.capitalize()}: {msg.parts[0].text}\n"

        summary_prompt = (
            "Summarize the following conversation in 2-3 sentences. "
            "Focus strictly on factual information provided by the user, key decisions made, "
            "and tasks completed. Ignore casual greetings or pleasantries.\n\n"
            f"Conversation:\n{history_text}"
        )

        try:
            summary_response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=summary_prompt,
                config=types.GenerateContentConfig(tools=[]),
            )
            if summary_response.text:
                memory_client.add_memory(
                    text_chunk=summary_response.text,
                    source_id="chat_summary",
                    chunk_index=int(time.time()),
                )
        except Exception as e:
            print(f"[System Error]: Failed to generate or save summary: {e}")

        gemini_chat = make_gemini_chat(current_instructions, history=safe_trim(full_history, keep=4))

    while True:
        try:
            user_input = input("User: ")
            if not user_input.strip():
                continue
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)

        recent_context = ""
        for msg in local_messages[-3:]:
            if msg["role"] != "system":
                recent_context += f"{msg['role'].capitalize()}: {msg['content']}\n"

        run_gemini(user_input)