import json
import os
import re
import time
import asyncio

from dotenv import load_dotenv

load_dotenv() 

from google import genai
from google.genai import types
from openai import AsyncOpenAI

from agent.utils import get_tools_and_map
from tools import VectorMemory
from agent.mcp_client import MCPClientManager
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")

LOCAL_MODEL = "lfm2:24b-a2b"
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MAX_LOCAL_MESSAGES = 20  
MAX_GEMINI_HISTORY = 40   

with open("context/identity.md", "r", encoding="utf-8") as f:
    role = f.read()
with open("context/user.md", "r", encoding="utf-8") as f:
    user = f.read()
with open("context/soul.md", "r", encoding="utf-8") as f:
    personality = f.read()

base_instructions = role + "\n" + user + "\n" + personality

memory_client = VectorMemory()

gemini_api_key = os.getenv("API_KEY_GEMINI")
if not gemini_api_key:
    raise RuntimeError("API_KEY_GEMINI is missing - check your .env file")

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
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
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
                "However, you STILL HAVE FULL ACCESS to all your Telegram tools via the local MCP server. "
                "You MUST USE YOUR TOOLS to fulfill the user's request just like you normally would. Do not make excuses about being offline.\n\n"
                "CRITICAL AUTONOMY RULES:\n"
                "- NEVER mock, fake, or simulate data. If you need to find a chat, call the search tools.\n"
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
                                try:
                                    args = json.loads(tool_call.function.arguments or "{}")
                                    if asyncio.iscoroutinefunction(func):
                                        result = await func(**args)
                                    else:
                                        result = func(**args)
                                except Exception as err:
                                    result = {"error": str(err)}
                            else:
                                result = {"error": f"Tool '{tool_call.function.name}' not found."}

                            tool_msg = {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_call.function.name,
                                "content": str(result),
                            }
                            call_messages.append(tool_msg)
                            messages_to_commit.append(tool_msg)

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
                gemini_chat = make_gemini_chat(
                    current_instructions, 
                    available_tools, 
                    history=list(gemini_chat.get_history()), 
                    model=GEMINI_MODELS[0]
                )

            MAX_TOOL_LOOPS = 5
            loop_count = 0
            current_prompt = user_input

            while loop_count < MAX_TOOL_LOOPS:
                loop_count += 1
                stream_succeeded = False
                text_response = ""
                function_calls_to_run = []

                for idx in range(current_model_index, len(GEMINI_MODELS)):
                    if idx != current_model_index or idx > 0:
                        gemini_chat = make_gemini_chat(
                            current_instructions,
                            available_tools,
                            history=list(gemini_chat.get_history()),
                            model=GEMINI_MODELS[idx],
                        )
                    current_model_index = idx
                    model_name = GEMINI_MODELS[idx]

                    text_response = ""
                    function_calls_to_run = []

                    response = await send_with_retry(gemini_chat, current_prompt, max_retries=2, base_delay=2)
                    if response is None:
                        continue

                    try:
                        async for chunk in response:
                            candidates = getattr(chunk, "candidates", None)
                            if not candidates or not candidates[0].content:
                                continue
                            for part in candidates[0].content.parts or []:
                                if getattr(part, "function_call", None):
                                    function_calls_to_run.append(part.function_call)
                                elif getattr(part, "text", None):
                                    text_response += part.text

                        stream_succeeded = True
                        break

                    except Exception as e:
                        print(f"\n[System Info]: Stream interrupted on model '{model_name}': {e}")
                        error_msg = str(e).lower()

                        if "503" in error_msg or "unavailable" in error_msg:
                            print("[System Info]: 503 Service Unavailable mid-stream. Waiting 2s before fallback...")
                            await asyncio.sleep(2)

                        if idx < len(GEMINI_MODELS) - 1:
                            print(f"[System Info]: Switching to fallback model '{GEMINI_MODELS[idx + 1]}'...")

                if not stream_succeeded:
                    print("[System Info]: All cloud Gemini models unavailable. Switching to local offline mode...")
                    await run_local(user_input)
                    return

                if function_calls_to_run:
                    tool_responses = []
                    for call in function_calls_to_run:
                        tool_name = call.name
                        tool_args = call.args or {}
                        
                        matching_wrapper = next(
                            (w for w in available_tools if getattr(w, '__name__', '') == tool_name or getattr(w, 'name', '') == tool_name), 
                            None
                        )
                        
                        if matching_wrapper:
                            try:
                                if asyncio.iscoroutinefunction(matching_wrapper):
                                    result = await matching_wrapper(**tool_args)
                                else:
                                    result = matching_wrapper(**tool_args)
                            except Exception as err:
                                result = {"error": str(err)}
                        else:
                            result = {"error": f"Tool '{tool_name}' not found."}

                        tool_responses.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": result}
                            )
                        )

                    current_prompt = tool_responses  
                    continue

                if text_response:
                    print(f"\nAI (Gemini): {text_response}\n", flush=True)

                break

            commit_local({"role": "user", "content": user_input})
            commit_local({"role": "assistant", "content": text_response})
            
            await maybe_summarize_and_trim()

        async def maybe_summarize_and_trim():
            nonlocal gemini_chat, current_model_index
            full_history = list(gemini_chat.get_history())
            
            for msg in full_history:
                if getattr(msg, "role", None) is None:
                    msg.role = "user"
            
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