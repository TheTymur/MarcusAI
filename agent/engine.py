import json
import os
import re
import time
import asyncio

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import AsyncOpenAI

from agent.utils import get_tools_and_map
from tools import VectorMemory
from agent.mcp_client import MCPClientManager
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")
warnings.filterwarnings("ignore", message="Direct use of automatic function calling")

LOCAL_MODEL = "lfm2:24b-a2b"
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MAX_LOCAL_MESSAGES = 20  
MAX_GEMINI_HISTORY = 40   

with open("context/identity.md", "r") as f:
    role = f.read()
with open("context/user.md", "r") as f:
    user = f.read()
with open("context/soul.md", "r") as f:
    personality = f.read()

base_instructions = role + "\n" + user + "\n" + personality

memory_client = VectorMemory()

load_dotenv()
gemini_api_key = os.getenv("API_KEY_GEMINI")

gemini_client = genai.Client(api_key=gemini_api_key)
ollama_client = AsyncOpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1",
)

def make_gemini_chat(instructions, tools, history=None, model=None):
    if model is None:
        model = GEMINI_MODELS[0]
    return gemini_client.aio.chats.create(
        model=model,
        config=types.GenerateContentConfig(
            system_instruction=instructions,
            tools=tools,
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


def safe_trim(history, keep=8):
    """
    Keep at least the last `keep` turns, but start on a plain user turn so we never
    begin with a model turn or split a function_call / function_response pair.
    Returns [] (fresh chat) if no safe start exists in the history.
    """
    history = list(history)
    if len(history) <= keep:
        return history
    for i in range(len(history) - keep, -1, -1):
        if is_plain_user_turn(history[i]):
            return history[i:]
    return []


async def send_with_retry(chat, text, max_retries=2, base_delay=2, timeout=60): 
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(chat.send_message_stream(text), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"\n[System Info]: Request timed out after {timeout}s. (Attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries:
                continue
            return None
        except Exception as e:
            import traceback
            traceback.print_exc()
            error_msg = str(e).lower()
            if any(term in error_msg for term in ["connection", "network", "socket", "nodename nor servname", "errno 8"]):
                return None
            if ("503" in error_msg) and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                print(f"\n[System Info]: API is busy (503). Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            if ("429" in error_msg or "resource_exhausted" in error_msg) and attempt < max_retries:
                match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg)
                delay = float(match.group(1)) + 1 if match else base_delay * (2 ** attempt)
                print(f"\n[System Info]: Rate limit reached (429). Waiting for {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            return None
    return None


async def start_agent():
    mcp_manager = MCPClientManager()
    
    try:
        mcp_wrappers = await mcp_manager.connect_and_get_tools()
        print(f"[System Info]: Loaded {len(mcp_wrappers)} tools from MCP server.")
        
        available_tools = mcp_wrappers

        recent_memory = memory_client.get_recent_summaries(limit=3)
        current_instructions = base_instructions + "\n" + recent_memory

        current_model_index = 0
        gemini_chat = make_gemini_chat(current_instructions, available_tools, model=GEMINI_MODELS[current_model_index])

        local_messages = [{"role": "system", "content": current_instructions}]
        local_tools, local_tool_map = get_tools_and_map(mcp_wrappers)

        def commit_local(*messages):
            local_messages.extend(messages)
            overflow = len(local_messages) - 1 - MAX_LOCAL_MESSAGES
            if overflow > 0:
                del local_messages[1:1 + overflow]

        def sync_to_gemini(user_input, text_response):
            nonlocal gemini_chat, current_model_index
            history = list(gemini_chat.get_history())
            history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))
            history.append(types.Content(role="model", parts=[types.Part.from_text(text=text_response)]))
            gemini_chat = make_gemini_chat(current_instructions, available_tools, history=history, model=GEMINI_MODELS[current_model_index])

        async def run_local(user_input):
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
                    response = await ollama_client.chat.completions.create(
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
                                args = json.loads(tool_call.function.arguments)
                                if asyncio.iscoroutinefunction(func):
                                    result = await func(**args)
                                else:
                                    result = func(**args)
                                
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

        async def run_gemini(user_input):
            nonlocal gemini_chat, current_model_index

            if current_model_index != 0:
                current_model_index = 0
                gemini_chat = make_gemini_chat(current_instructions, available_tools, history=list(gemini_chat.get_history()), model=GEMINI_MODELS[0])

            previous_history = list(gemini_chat.get_history())
            response = None

            for _ in range(len(GEMINI_MODELS)):
                response = await send_with_retry(gemini_chat, user_input, max_retries=2)
                if response is not None:
                    break
                
                if current_model_index < len(GEMINI_MODELS) - 1:
                    current_model_index += 1
                    new_model = GEMINI_MODELS[current_model_index]
                    print(f"\n[System Info]: Model failed. Switching to {new_model}...")
                    gemini_chat = make_gemini_chat(current_instructions, available_tools, history=previous_history, model=new_model)
                else:
                    break

            if response is None:
                print("\n[System Info]: Cloud AI unavailable. Switching to offline mode...")
                if (await run_local(user_input)) != "done":
                    print("[System Error]: Local model failed. Please try again in a moment.\n")
                return

            commit_local({"role": "user", "content": user_input})
            text_response = ""
            has_function_calls = False
            output_started = False
            
            async for chunk in response:
                if getattr(chunk, "function_calls", None):
                    has_function_calls = True
                    continue
                    
                if getattr(chunk, "text", None):
                    if not output_started:
                        print("\nAI (Gemini): ", end="", flush=True)
                        output_started = True
                    
                    print(chunk.text, end="", flush=True)
                    text_response += chunk.text

            commit_local({"role": "assistant", "content": text_response})
            
            if text_response:
                print("\n\n", flush=True)
            else:
                if has_function_calls:
                    print("\n[System Warning]: The AI reached its maximum internal loop limit for tool calls and was stopped to prevent an infinite loop. Please provide more specific instructions or increase limits.")
                else:
                    print(f"\n[System Warning]: Gemini returned an empty text response or stream was aborted.")

            await maybe_summarize_and_trim()

        async def maybe_summarize_and_trim():
            nonlocal gemini_chat, current_model_index
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
                summary_response = await gemini_client.aio.models.generate_content(
                    model=GEMINI_MODELS[current_model_index],
                    contents=summary_prompt,
                    config=types.GenerateContentConfig(),
                )
                if summary_response.text:
                    memory_client.add_memory(
                        text_chunk=summary_response.text,
                        source_id="chat_summary",
                        chunk_index=int(time.time()),
                    )
            except Exception as e:
                print(f"[System Error]: Failed to generate or save summary: {e}")

            gemini_chat = make_gemini_chat(current_instructions, available_tools, history=safe_trim(full_history, keep=8), model=GEMINI_MODELS[current_model_index])

        while True:
            try:
                user_input = await asyncio.to_thread(input, "User: ")

                if not user_input.strip():
                    continue
                
                if user_input.lower().strip() == "exit":
                    print("[System Info]: Exiting application.")
                    break

            except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
                print("\n[System Info]: Exiting application.")
                break

            await run_gemini(user_input)
            
    finally:
        await mcp_manager.cleanup()
