import os
import sys
import time
import re
from dotenv import load_dotenv
from openai import OpenAI
from google import genai
from google.genai import types
from tools import multiply, GmailClient, exit_agent, get_current_date_and_time, VectorMemory
from agent.utils import get_tools_and_map

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
    gmail_client.create_response_draft, gmail_client.forward_email, memory_client.search_knowledge_base, memory_client.save_user_preference]

load_dotenv()
gemini_api_key = os.getenv("API_KEY_GEMINI")

gemini_client = genai.Client(api_key=gemini_api_key)
ollama_client = OpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1",
)

def route_query(user_input: str, chat_history: str = ""):
    """Uses Ollama to determine if a request needs tools (GEMINI) or not (LOCAL)."""
    prompt = f"""You are a router for an email assistant. 
Does the following user request require accessing tools (like reading emails, sending emails, searching emails, deleting, labeling, using memory) or complex reasoning? 
Keep the recent chat history in mind to understand context (e.g. if the user is providing a fact to save, route to GEMINI).

Recent Chat History:
{chat_history}

User request: {user_input}
Reply with exactly 'GEMINI' if it requires tools, complex logic, or if the user is saying goodbye/exiting (so it can use the exit tool).
Reply with exactly 'LOCAL' if it's just a general chat, greeting, or simple conversational question.

CRITICAL INSTRUCTION: You MUST output ONLY the word "GEMINI" or "LOCAL". Do not include ANY other text, explanation, or punctuation.
Route:"""
    try:
        response = ollama_client.chat.completions.create(
            model="lfm2:24b-a2b",
            messages=[{"role": "system", "content": prompt}]
        )
        route = response.choices[0].message.content.strip().upper()
        if route.startswith("GEMINI") or "GEMINI" in route.split("\n")[0]:
            return "GEMINI"
        return "LOCAL"
    except Exception as e:
        print(f"[System Error]: Routing failed, defaulting to GEMINI. {e}")
        return "GEMINI"

def start_agent():
    recent_memory = memory_client.get_recent_summaries(limit=3)
    current_instructions = base_instructions + "\n" + recent_memory

    # Initialize Gemini chat session
    gemini_chat = gemini_client.chats.create(
        model="gemini-3.1-flash-lite",
        config=types.GenerateContentConfig(
            system_instruction=current_instructions,
            tools=available_tools
        )
    )

    # Initialize Ollama local messages (no tools)
    local_messages = [
        {"role": "system", "content": current_instructions}
    ]
    
    local_tools, local_tool_map = get_tools_and_map([exit_agent])

    while True:
        try:
            user_input = input("User: ")
            if not user_input.strip():
                continue
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            sys.exit(0)

        recent_context = ""
        for msg in local_messages[-3:]:
            if msg["role"] != "system":
                recent_context += f"{msg['role'].capitalize()}: {msg['content']}\n"

        # Hardcoded exit routing for reliability
        user_input_lower = user_input.strip().lower()
        if user_input_lower in ["bye", "goodbye", "exit", "quit", "byeeeee"]:
            route = "GEMINI"
        else:
            route = route_query(user_input, recent_context)
        
        if route == "LOCAL":
            # Search memory first
            retrieved_info = memory_client.search_knowledge_base(user_input)
            
            # Prepare local prompt with RAG
            rag_instruction = f"Retrieved Database Info:\n{retrieved_info}\nIf the user asks a factual question and the answer is not in the Database Info, or if the user is explicitly telling you a new fact to remember, reply exactly with '[ESCALATE]'."
            
            local_messages.append({"role": "system", "content": rag_instruction})
            local_messages.append({"role": "user", "content": user_input})
            
            try:
                response = ollama_client.chat.completions.create(
                    model="lfm2:24b-a2b",
                    messages=local_messages,
                    tools=local_tools
                )
                
                # Remove the temporary RAG instruction
                local_messages.pop(-2)
                
                message = response.choices[0].message
                
                if message.tool_calls:
                    import json
                    for tool_call in message.tool_calls:
                        func_name = tool_call.function.name
                        if func_name in local_tool_map:
                            args = json.loads(tool_call.function.arguments)
                            local_tool_map[func_name](**args)
                    continue
                
                text_response = message.content
                
                if "ESCALATE" in text_response.upper():
                    print("\n[System Info]: Local model couldn't find the answer in DB. Escalating to Gemini...")
                    local_messages.pop() # Remove user input so GEMINI can process it cleanly
                    route = "GEMINI"
                else:
                    local_messages.append({"role": "assistant", "content": text_response})
                    print("\nAI (Local): " + text_response + "\n")
                    
                    # Synchronize memory to Gemini by recreating the chat session with updated history
                    current_history = list(gemini_chat.get_history())
                    current_history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))
                    current_history.append(types.Content(role="model", parts=[types.Part.from_text(text=text_response)]))
                    
                    gemini_chat = gemini_client.chats.create(
                        model="gemini-3.1-flash-lite",
                        config=types.GenerateContentConfig(
                            system_instruction=current_instructions,
                            tools=available_tools
                        ),
                        history=current_history
                    )
            except Exception as e:
                print(f"\n[System Info]: Local LLM request failed: {e}")
                # Cleanup local_messages on error
                local_messages.pop()
                if len(local_messages) > 0 and local_messages[-1].get("content", "").startswith("Retrieved Database Info"):
                    local_messages.pop()

        if route == "GEMINI":
            # Route to Gemini
            # Synchronize Gemini's new input to the local messages so Ollama knows what happened
            local_messages.append({"role": "user", "content": user_input})
            
            max_retries = 3
            base_delay = 2
            
            for attempt in range(max_retries + 1):
                try:
                    response = gemini_chat.send_message(user_input)
                    break
                except Exception as e:
                    error_msg = str(e)
                    if "503" in error_msg or "UNAVAILABLE" in error_msg:
                        if attempt < max_retries:
                            delay = base_delay * (2 ** attempt)
                            print(f"\n[System Info]: API is busy (503). Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
                            time.sleep(delay)
                            continue
                    elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                        if attempt < max_retries:
                            match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg)
                            delay = float(match.group(1)) + 1 if match else base_delay * (2 ** attempt)
                            print(f"\n[System Info]: Rate limit reached (429). Waiting for {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                            time.sleep(delay)
                            continue
                    print(f"\n[System Error]: Failed to generate response. {e}\n")
                    response = None
                    break
                    
            if response is None:
                continue
                
            text_response = ""
            if getattr(response, "candidates", None) and response.candidates:
                parts = getattr(response.candidates[0].content, "parts", [])
                for part in parts:
                    if getattr(part, "text", None):
                        text_response += part.text
            elif getattr(response, "text", None):
                text_response = response.text
                
            if text_response:
                print("\nAI (Gemini): " + text_response + "\n")
                # Synchronize local memory so Ollama is aware of what Gemini did
                local_messages.append({"role": "assistant", "content": text_response})

            if len(gemini_chat.get_history()) > 40:
                print("\n[System Info]: Chat history getting too long. Generating summary for long-term memory...")
                
                history_text = ""
                full_history = list(gemini_chat.get_history())
                for msg in full_history:
                    role = msg.role
                    if getattr(msg, "parts", None) and getattr(msg.parts[0], "text", None):
                        history_text += f"{role.capitalize()}: {msg.parts[0].text}\n"

                summary_prompt = (
                    "Summarize the following conversation in 2-3 sentences. "
                    "Focus strictly on factual information provided by the user, key decisions made, "
                    "and tasks completed. Ignore casual greetings or pleasantries.\n\n"
                    f"Conversation:\n{history_text}"
                )
                
                try:
                    summary_response = gemini_client.models.generate_content(
                        model="gemini-3.1-flash-lite",
                        contents=summary_prompt,
                        config=types.GenerateContentConfig(tools=[])
                    )
                    
                    if summary_response.text:
                        chunk_id = int(time.time())
                        memory_client.add_memory(
                            text_chunk=summary_response.text, 
                            source_id="chat_summary", 
                            chunk_index=chunk_id
                        )
                except Exception as e:
                    print(f"[System Error]: Failed to generate or save summary: {e}")

                print("[System Info]: Resetting short-term memory to save tokens.")
                # Preserve the last 4 messages (2 user-assistant turns) so it doesn't lose immediate context
                preserved_history = full_history[-4:] if len(full_history) >= 4 else full_history
                
                gemini_chat = gemini_client.chats.create(
                    model="gemini-3.1-flash-lite",
                    config=types.GenerateContentConfig(
                        system_instruction=current_instructions,
                        tools=available_tools
                    ),
                    history=preserved_history
                )

