import os
import sys
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tools import multiply, GmailClient, exit_agent, get_current_date_and_time, VectorMemory
import re
with open("context/identity.md", "r") as f:
    role = f.read()
with open("context/user.md", "r") as f:
    user = f.read()
with open("context/soul.md", "r") as f:
    personality = f.read()

master_instructions = role + "\n" + user + "\n" + personality

gmail_client = GmailClient()
memory_client = VectorMemory()

available_tools = [multiply, gmail_client.read_recent_emails, gmail_client.create_label, gmail_client.apply_label,
    gmail_client.delete_label, gmail_client.remove_label, gmail_client.list_labels, gmail_client.count_messages_in_label, 
    gmail_client.search_emails, gmail_client.delete_message, gmail_client.delete_draft, gmail_client.list_drafts, exit_agent,
    gmail_client.create_draft, gmail_client.modify_draft, gmail_client.send_draft, gmail_client.read_email_content, get_current_date_and_time,
    gmail_client.create_response_draft, gmail_client.forward_email, memory_client.search_knowledge_base]

load_dotenv()
api_key = os.getenv("API_KEY_GEMINI")

client = genai.Client(api_key=api_key)

def start_agent():

    global master_instructions

    recent_memory = memory_client.get_recent_summaries(limit=3)

    master_instructions += recent_memory

    chat = client.chats.create(
        model="gemini-3.1-flash-lite",
        config=types.GenerateContentConfig(
            system_instruction=master_instructions,
            tools=available_tools
        )
    )
    while True:
        user_input = input("User: ")
        max_retries = 3
        base_delay = 2
        
        for attempt in range(max_retries + 1):

            try:
                response = chat.send_message(user_input)
                break

            except SystemExit:
                print("\n[System Info]: Saving final session memory before exit...")

                if len(chat.get_history()) > 2: 
                    history_text = ""
                    for msg in chat.get_history():
                        if getattr(msg, "parts", None) and getattr(msg.parts[0], "text", None):
                            history_text += f"{msg.role.capitalize()}: {msg.parts[0].text}\n"

                    summary_prompt = (
                        "Summarize the final steps of this conversation in 1-2 sentences. "
                        "Focus on tasks completed right before the user exited.\n\n"
                        f"Conversation:\n{history_text}"
                    )
                    
                    try:
                        summary_response = client.models.generate_content(
                            model="gemini-3.1-flash-lite",
                            contents=summary_prompt
                        )
                        if summary_response.text:
                            memory_client.add_memory(
                                text_chunk=summary_response.text, 
                                source_id="chat_summary_final", 
                                chunk_index=int(time.time())
                            )
                    except Exception as mem_e:
                        print(f"[System Error]: Could not save final memory: {mem_e}")
                
                sys.exit(0)
                
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
            print("\nAI: " + text_response + "\n")

        if len(chat.get_history()) > 40:
            print("\n[System Info]: Chat history getting too long. Generating summary for long-term memory...")
            
            history_text = ""
            for msg in chat.get_history():
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
                summary_response = client.models.generate_content(
                    model="gemini-3.1-flash-lite",
                    contents=summary_prompt
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
            recent_history = chat.get_history()[-4:] if len(chat.get_history()) >= 4 else chat.get_history()
            chat = client.chats.create(
                model="gemini-3.1-flash-lite",
                config=types.GenerateContentConfig(
                    system_instruction=master_instructions,
                    tools=available_tools
                ),
                history=recent_history
            )
