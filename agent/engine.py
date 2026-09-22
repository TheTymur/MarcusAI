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
    gmail_client.search_local_database, gmail_client.unsubscribe_from_email, gmail_client.block_sender, gmail_client.unblock_sender]

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
            if any(term in error_msg for term in ["connection", "network", "socket", "nodename nor servname", "errno 8"]):
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
            return None
    return None




def start_agent():
    recent_memory = memory_client.get_recent_summaries(limit=3)
    current_instructions = base_instructions + "\n" + recent_memory

    gemini_chat = make_gemini_chat(current_instructions)

    local_messages = [{"role": "system", "content": current_instructions}]
    local_tools, local_tool_map = get_tools_and_map([exit_agent, memory_client.search_knowledge_base, gmail_client.search_local_database, gmail_client.read_local_email, get_current_date_and_time])

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
            "However, you DO have access to four local databases/tools via your tools. You MUST USE YOUR TOOLS to search them instead of guessing:\n"
            "1. `search_local_database`: Use this to search the local SQL database. All parameters (sender, subject) are OPTIONAL. If the user asks for 'latest emails' or 'recent emails', YOU MUST call this tool with NO parameters to retrieve the most recent entries. It returns exact metadata (Date, Sender, Subject).\n"
            "2. `read_local_email`: Use this to read the full body text of a specific email from the local SQL database. You must provide the exact Email ID retrieved from `search_local_database`.\n"
            "3. `search_knowledge_base`: Use this to search the local vector database for User Preferences, or past conversation summaries.\n"
            "4. `get_current_date_and_time`: Use this to get the current system date and time if the user asks what time it is.\n\n"
            "CRITICAL AUTONOMY RULES:\n"
            "- NEVER ask the user for 'search criteria' or 'parameters' if they ask for recent emails. Just call `search_local_database` with no arguments.\n"
            "- NEVER mock, fake, or simulate data. If you don't know the answer, call a tool.\n"
            "- NEVER stall, write long disclaimers, or make excuses. Use your tools automatically and autonomously!\n"
        )

        user_msg = {"role": "user", "content": user_input}
        call_messages = local_messages + [{"role": "system", "content": extra}, user_msg]

        try:
            MAX_ITERATIONS = 5
            iterations = 0
            
            messages_to_commit = []

            while iterations < MAX_ITERATIONS:
                response = ollama_client.chat.completions.create(
                    model=LOCAL_MODEL,
                    messages=call_messages,
                    tools=local_tools,
                )
                message = response.choices[0].message
                
                if message.tool_calls:
                    message_dict = {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": t.id,
                                "type": "function",
                                "function": {"name": t.function.name, "arguments": t.function.arguments}
                            }
                            for t in message.tool_calls
                        ]
                    }
                    call_messages.append(message_dict)
                    messages_to_commit.append(message_dict)
                    
                    for tool_call in message.tool_calls:
                        func = local_tool_map.get(tool_call.function.name)
                        if func:
                            result = func(**json.loads(tool_call.function.arguments))
                            tool_msg = {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_call.function.name,
                                "content": str(result)
                            }
                            call_messages.append(tool_msg)
                            messages_to_commit.append(tool_msg)
                        else:
                            return "error"
                    
                    iterations += 1
                    continue
                
                text_response = (message.content or "").strip()
                if not text_response:
                    raise ValueError("local model returned an empty response")
                
                messages_to_commit.append({"role": "assistant", "content": text_response})
                commit_local(user_msg, *messages_to_commit)
                
                print(f"\nAI (Local): {text_response}\n")
                sync_to_gemini(user_input, text_response)
                return "done"
                
            raise ValueError("local model exceeded maximum tool call iterations")

        except Exception as e:
            print(f"[System Warning]: Local model failed: {e}")
            return "error"

    def run_gemini(user_input):
        nonlocal gemini_chat

        response = send_with_retry(gemini_chat, user_input)

        if response is None:
            print("\n[System Info]: Cloud AI unavailable. Switching to offline mode...")
            if run_local(user_input) != "done":
                print("[System Error]: Local model failed. Please try again in a moment.\n")
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
            
            if user_input.lower().strip() == "exit":
                print("[System Info]: Exiting application.")
                sys.exit(0)

        except (KeyboardInterrupt, EOFError):
            sys.exit(0)

        recent_context = ""
        for msg in local_messages[-3:]:
            if msg["role"] != "system":
                recent_context += f"{msg['role'].capitalize()}: {msg['content']}\n"

        run_gemini(user_input)