import json
import os
import re
import time
import asyncio
import warnings
from typing import Optional, Callable, Awaitable

from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types
from openai import AsyncOpenAI

from tools import VectorMemory
from agent.mcp_client import MCPClientManager, ToolResult

warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")

LOCAL_MODEL = "lfm2:24b-a2b"
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MAX_LOCAL_MESSAGES = 20
MAX_GEMINI_HISTORY = 100
REQUIRE_APPROVAL = True
APPROVAL_TOOLS = {
    "send_draft", "forward_email", "unsubscribe_from_email", "block_sender",
    "send_message", "edit_message", "forward_message",
}

role = ""
if os.path.exists("context/identity.md"):
    with open("context/identity.md", "r", encoding="utf-8") as f:
        role = f.read()

user = ""
if os.path.exists("context/user.md"):
    with open("context/user.md", "r", encoding="utf-8") as f:
        user = f.read()
elif os.path.exists("context/user.md.example"):
    with open("context/user.md.example", "r", encoding="utf-8") as f:
        user = f.read()

personality = ""
if os.path.exists("context/soul.md"):
    with open("context/soul.md", "r", encoding="utf-8") as f:
        personality = f.read()

base_instructions = f"{role}\n{user}\n{personality}".strip()

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
            print(f"\n[System Logs]: Request timed out after {timeout}s. (Attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries:
                continue
            return None
        except Exception as e:
            error_msg = str(e).lower()
            if any(term in error_msg for term in ["connection", "network", "socket", "nodename nor servname", "errno 8"]):
                return None
            if ("503" in error_msg) and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                print(f"\n[System Logs]: API is busy (503). Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            if ("429" in error_msg or "resource_exhausted" in error_msg) and attempt < max_retries:
                match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg)
                delay = float(match.group(1)) + 1 if match else base_delay * (2 ** attempt)
                print(f"\n[System Logs]: Rate limit reached (429). Waiting for {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            return None
    return None


class MarcusEngine:
    """
    Core agent engine for Marcus.
    """

    def __init__(
        self,
        approval_callback: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        require_approval: bool = REQUIRE_APPROVAL,
    ):
        self.approval_callback = approval_callback
        self.status_callback = status_callback
        self.require_approval = require_approval

        self.mcp_manager = MCPClientManager(approval_names=APPROVAL_TOOLS)
        self.gemini_chat = None
        self.gemini_tools = []
        self.local_tools = []
        self.current_model_index = 0
        self.current_instructions = ""
        self.local_messages = []
        self._lock = asyncio.Lock()
        self._is_initialized = False

    async def initialize(self) -> None:
        """Initialize MCP servers and setup chat sessions."""
        if self._is_initialized:
            return

        await self.mcp_manager.connect()
        self.gemini_tools = self.mcp_manager.gemini_tools()
        self.local_tools = self.mcp_manager.openai_tools()

        recent_memory = memory_client.get_recent_summaries(limit=5)
        self.current_instructions = base_instructions + "\n" + recent_memory
        self.current_model_index = 0
        self.gemini_chat = make_gemini_chat(
            self.current_instructions,
            self.gemini_tools,
            model=GEMINI_MODELS[self.current_model_index],
        )
        self.local_messages = [{"role": "system", "content": self.current_instructions}]
        self._is_initialized = True

    @property
    def model(self) -> str:
        """Returns the name of the currently active model."""
        if 0 <= self.current_model_index < len(GEMINI_MODELS):
            return GEMINI_MODELS[self.current_model_index]
        return LOCAL_MODEL

    async def cleanup(self) -> None:
        """Cleanly close MCP client sessions."""
        await self.mcp_manager.cleanup()
        self._is_initialized = False

    async def _ask_approval(self, name: str, args: dict) -> bool:
        display_args = dict(args)
        sender_info = display_args.get("sender")

        if "email_id" in args and not sender_info:
            try:
                from agent.data.database_manager import Emailprocessing, Emails
                db = Emailprocessing()
                em = db.session.query(Emails).filter(Emails.gmail_id == args["email_id"]).first()
                if em:
                    display_args["sender"] = em.sender
                    display_args["subject"] = em.subject
                    sender_info = em.sender
                db.session.close()
            except Exception:
                pass

        if self.approval_callback is not None:
            try:
                return await self.approval_callback(name, display_args)
            except Exception as e:
                print(f"[System Logs]: Approval Callback Error: {e}")
                return False

        # Fallback to terminal input
        target_label = ""
        if sender_info:
            target_label = f" [Sender: {sender_info}]"
        elif "recipient" in display_args:
            target_label = f" [Recipient: {display_args['recipient']}]"
        elif "to_chat_id" in display_args:
            target_label = f" [To: {display_args['to_chat_id']}]"
        elif "chat_id" in display_args:
            target_label = f" [Chat: {display_args['chat_id']}]"

        preview = json.dumps(display_args, ensure_ascii=False, default=str)
        if len(preview) > 500:
            preview = preview[:500] + "..."
        answer = await asyncio.to_thread(
            input, f"\n[Approval needed]{target_label} {name}({preview})\nAllow? [y/N]: "
        )
        return answer.strip().lower() in ("y", "yes")

    async def _execute_tool(self, name: str, args: dict) -> ToolResult:
        """Execute a tool call with approval check if required."""
        if self.status_callback:
            try:
                await self.status_callback(f"Running tool: {name}")
            except Exception:
                pass

        if self.require_approval and self.mcp_manager.needs_approval(name):
            if not await self._ask_approval(name, args):
                return ToolResult(
                    "The user denied this action. Do not retry it; ask what they want instead.",
                    is_error=True,
                )
        print(f"[System Logs]: Executing tool: {name} with args: {args}")
        outcome = await self.mcp_manager.call(name, args)

        # Check if the tool failed or returned an error payload
        err_detail = None
        if outcome.is_error:
            err_detail = outcome.text
        else:
            raw_text = outcome.text.strip()
            if raw_text.startswith("{") and raw_text.endswith("}"):
                try:
                    data = json.loads(raw_text)
                    if isinstance(data, dict) and "error" in data:
                        err_detail = data["error"]
                except Exception:
                    pass
            elif raw_text.lower().startswith("error:") or raw_text.lower().startswith("an error occurred"):
                err_detail = raw_text

        if err_detail:
            print(f"[System Logs]: Tool '{name}' failed with error: {err_detail}", flush=True)

        return outcome

    def _commit_local(self, *messages):
        self.local_messages.extend(messages)
        overflow = len(self.local_messages) - 1 - MAX_LOCAL_MESSAGES
        if overflow > 0:
            del self.local_messages[1:1 + overflow]

    def _sync_to_gemini(self, user_input: str, text_response: str):
        history = list(self.gemini_chat.get_history())
        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=text_response)]))
        self.gemini_chat = make_gemini_chat(
            self.current_instructions,
            self.gemini_tools,
            history=history,
            model=GEMINI_MODELS[self.current_model_index],
        )

    async def _run_local(self, user_input: str) -> str:
        extra = (
            "CRITICAL INSTRUCTION: You are running in OFFLINE FALLBACK MODE because the main cloud AI is down. "
            "However, you STILL HAVE FULL ACCESS to all your Telegram tools via the local MCP server. "
            "You MUST USE YOUR TOOLS to fulfill the user's request just like you normally would. Do not make excuses about being offline.\n\n"
            "CRITICAL AUTONOMY RULES:\n"
            "- NEVER mock, fake, or simulate data. If you need to find a chat, call the search tools.\n"
            "- NEVER stall, write long disclaimers, or make excuses. Use your tools automatically and autonomously!\n"
        )

        user_msg = {"role": "user", "content": user_input}
        call_messages = self.local_messages + [{"role": "system", "content": extra}, user_msg]

        try:
            MAX_ITERATIONS = 5
            iterations = 0
            messages_to_commit = []

            while iterations < MAX_ITERATIONS:
                response = await ollama_client.chat.completions.create(
                    model=LOCAL_MODEL,
                    messages=call_messages,
                    tools=self.local_tools or None,
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
                                "function": {"name": t.function.name, "arguments": t.function.arguments},
                            }
                            for t in message.tool_calls
                        ],
                    }
                    call_messages.append(message_dict)
                    messages_to_commit.append(message_dict)

                    for tool_call in message.tool_calls:
                        name = tool_call.function.name
                        raw_args = tool_call.function.arguments
                        try:
                            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
                        except json.JSONDecodeError as err:
                            outcome = ToolResult(f"Invalid JSON arguments: {err}", is_error=True)
                        else:
                            outcome = await self._execute_tool(name, args)

                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": name,
                            "content": outcome.as_text(),
                        }
                        call_messages.append(tool_msg)
                        messages_to_commit.append(tool_msg)

                    iterations += 1
                    continue

                text_response = (message.content or "").strip()
                if not text_response:
                    raise ValueError("local model returned an empty response")

                messages_to_commit.append({"role": "assistant", "content": text_response})
                self._commit_local(user_msg, *messages_to_commit)
                self._sync_to_gemini(user_input, text_response)
                return text_response

            raise ValueError("local model exceeded maximum tool call iterations")

        except Exception as e:
            err_msg = f"Local model failed: {e}"
            print(f"[System Error]: {err_msg}")
            return err_msg

    async def _maybe_summarize_and_trim(self):
        full_history = list(self.gemini_chat.get_history())

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
                model=GEMINI_MODELS[self.current_model_index],
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

        self.gemini_chat = make_gemini_chat(
            self.current_instructions,
            self.gemini_tools,
            history=safe_trim(full_history, keep=20),
            model=GEMINI_MODELS[self.current_model_index],
        )

    async def process_message(self, user_input: str) -> str:
        """Process a user message and return Marcus's final response text."""
        async with self._lock:
            if not self._is_initialized:
                await self.initialize()

            if self.current_model_index != 0:
                self.current_model_index = 0
                self.gemini_chat = make_gemini_chat(
                    self.current_instructions,
                    self.gemini_tools,
                    history=list(self.gemini_chat.get_history()),
                    model=GEMINI_MODELS[0],
                )

            MAX_TOOL_LOOPS = 15
            loop_count = 0
            current_prompt = user_input
            final_text_response = ""

            while loop_count < MAX_TOOL_LOOPS:
                loop_count += 1
                stream_succeeded = False
                text_response = ""
                function_calls_to_run = []

                for idx in range(self.current_model_index, len(GEMINI_MODELS)):
                    if idx != self.current_model_index or idx > 0:
                        self.gemini_chat = make_gemini_chat(
                            self.current_instructions,
                            self.gemini_tools,
                            history=list(self.gemini_chat.get_history()),
                            model=GEMINI_MODELS[idx],
                        )
                    self.current_model_index = idx
                    model_name = GEMINI_MODELS[idx]

                    text_response = ""
                    function_calls_to_run = []

                    response = await send_with_retry(self.gemini_chat, current_prompt, max_retries=2, base_delay=2)
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
                        print(f"\n[System Logs]: Stream interrupted on model '{model_name}': {e}")
                        error_msg = str(e).lower()

                        if "503" in error_msg or "unavailable" in error_msg:
                            print("[System Logs]: 503 Service Unavailable mid-stream. Waiting 2s before fallback...")
                            await asyncio.sleep(2)

                        if idx < len(GEMINI_MODELS) - 1:
                            print(f"[System Logs]: Switching to fallback model '{GEMINI_MODELS[idx + 1]}'...")

                if not stream_succeeded:
                    print("[System Logs]: All cloud Gemini models unavailable. Switching to local offline mode...")
                    return await self._run_local(user_input)

                if function_calls_to_run:
                    tool_responses = []
                    for call in function_calls_to_run:
                        outcome = await self._execute_tool(call.name, dict(call.args or {}))
                        payload = {"error": outcome.text} if outcome.is_error else {"result": outcome.text}
                        tool_responses.append(
                            types.Part.from_function_response(name=call.name, response=payload)
                        )

                    current_prompt = tool_responses
                    continue

                if text_response:
                    final_text_response = text_response
                break
            else:
                final_text_response = "Stopped after the maximum number of tool rounds without a final answer."

            self._commit_local({"role": "user", "content": user_input})
            if final_text_response:
                self._commit_local({"role": "assistant", "content": final_text_response})

            await self._maybe_summarize_and_trim()
            return final_text_response


async def start_agent():
    """Terminal CLI interface loop."""
    engine = MarcusEngine()
    try:
        await engine.initialize()
        print(f"[System Logs]: Loaded {engine.mcp_manager.tool_count} tools from {engine.mcp_manager.server_count} MCP servers.")

        while True:
            try:
                user_input = await asyncio.to_thread(input, "User: ")

                if not user_input.strip():
                    continue

                if user_input.lower().strip() == "exit":
                    print("[System Logs]: Exiting application.")
                    break

            except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
                print("\n[System Logs]: Exiting application.")
                break

            response = await engine.process_message(user_input)
            print(f"\nAI (Marcus): {response}\n", flush=True)

    finally:
        await engine.cleanup()