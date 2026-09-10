import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tools import multiply, GmailClient, exit_agent, get_current_date_and_time

with open("context/identity.md", "r") as f:
    role = f.read()
with open("context/user.md", "r") as f:
    user = f.read()
with open("context/soul.md", "r") as f:
    personality = f.read()

master_instructions = role + "\n" + user + "\n" + personality

gmail_client = GmailClient()

available_tools = [multiply, gmail_client.read_recent_emails, gmail_client.create_label, gmail_client.apply_label,
    gmail_client.delete_label, gmail_client.remove_label, gmail_client.list_labels, gmail_client.count_messages_in_label, 
    gmail_client.search_emails, gmail_client.delete_message, gmail_client.delete_draft, gmail_client.list_drafts, exit_agent,
    gmail_client.create_draft, gmail_client.modify_draft, gmail_client.send_draft, gmail_client.read_email_content, get_current_date_and_time,
    gmail_client.create_response_draft, gmail_client.forward_email]

load_dotenv()
api_key = os.getenv("API_KEY_GEMINI")

client = genai.Client(api_key=api_key)

def start_agent():
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
            except Exception as e:
                error_msg = str(e)
                if "503" in error_msg or "UNAVAILABLE" in error_msg:
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        print(f"\n[System Info]: API is busy (503). Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
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
            # Fallback if the structure is different
            text_response = response.text
            
        if text_response:
            print("\nAI: " + text_response + "\n")
