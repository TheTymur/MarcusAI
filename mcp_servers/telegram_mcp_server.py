import os
from dotenv import load_dotenv
from fastmcp import FastMCP
from telethon import TelegramClient
from telethon.sessions import StringSession
from mcp.types import ToolAnnotations
from pydantic import Field


load_dotenv()

api_id_raw = os.getenv("API_TELEGRAM_ID")
api_hash = os.getenv("API_TELEGRAM_HASH")
    

API_ID = int(api_id_raw)
API_HASH = str(api_hash)

mcp = FastMCP("Telegram Server")

user_client = TelegramClient("polish_session", API_ID, API_HASH)

def _parse_message(msg) -> dict:
    return {
        "message_id": msg.id, 
        "text": msg.message,
        "sender_id": "me" if msg.out else msg.sender_id,
        "timestamp": msg.date.isoformat() if msg.date else None,
        "is_edited": msg.edit_date is not None,
        "reply_to_message_id": msg.reply_to_msg_id,
        "media_type": (
            "sticker" if msg.sticker else
            "photo" if msg.photo else
            "video" if getattr(msg, 'video', False) else
            "voice" if getattr(msg, 'voice', False) else
            "document" if msg.document else None
        ),
        "sticker_emoji": msg.file.emoji if msg.sticker and getattr(msg, 'file', None) else None
    }

# readOnlyHint: Only reads data, no modifications.
# destructiveHint: Permanently modifies or deletes data.
# idempotentHint: Safe to call multiple times with the same result.
# openWorldHint: Interacts with external systems/APIs.

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def send_message(
    chat_id: int = Field(description="The chat ID of the conversation."), 
    message: str = Field(description="The message to send."),
    reply_to_message_id: int = Field(default=None, description="Optional ID of a message to reply to.")) -> str:
    """Sends a message to a chat or user by ID.
    IMPORTANT: This tool sends a message directly to a user or chat on Telegram.
    CRITICAL RESTRICTION: You MUST NEVER call this tool on your own. You must FIRST output the [TELEGRAM MESSAGE PREPARED] text to the user and wait for them to explicitly reply "yes" or "send" in the next turn before executing this tool.
    Args:
        chat_id (int): The numeric chat ID.
        message (str): The content of the message to send.
        reply_to_message_id (int): Optional ID of a message to reply to.
    """
    if not user_client.is_connected():
        await user_client.connect()
        
        if not await user_client.is_user_authorized():
            return "Error: Telegram session is invalid or expired! Generate a new TELEGRAM_SESSION_STRING."
    try:
        sent_msg = await user_client.send_message(chat_id, message, reply_to=reply_to_message_id)
        return f"Message sent successfully to {chat_id}. Message ID: {sent_msg.id}"
    except Exception as e:
        return f"Error sending message: {str(e)}"
        
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def edit_message(
    chat_id: int = Field(description="The chat ID of the conversation."), 
    message_id: int = Field(description="The ID of the message to edit."),
    message: str = Field(description="The message to send.")) -> str:
    """Edits a message in a conversation.
    IMPORTANT: This tool edits a message directly in a chat on Telegram.
    CRITICAL RESTRICTION: You MUST NEVER call this tool on your own without explicit permission. You must FIRST output the [TELEGRAM EDIT PREPARED] text to the user and wait for them to explicitly reply "yes" or "edit it" in the next turn before executing this tool.
    Args:
        chat_id (int): The numeric chat ID.
        message_id (int): The ID of the message to edit.
        message (str): The new content of the message.
    """
    if not user_client.is_connected():
        await user_client.connect()
        
        if not await user_client.is_user_authorized():
            return "Error: Telegram session is invalid or expired! Generate a new TELEGRAM_SESSION_STRING."
    try:
        await user_client.edit_message(chat_id, message_id, message)
        return f"Message edited in {chat_id}"
    except Exception as e:
        return f"Error editing message: {str(e)}"

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def get_message(chat_id: int = Field(description="The chat ID of the conversation."), limit: int = Field(description="The number of messages to get.", default=1)) -> dict:
    """Gets the last message from a chat.
    IMPORTANT: This tool gets the last message from a chat on Telegram.
    Use this tool when you need to get the last message from a chat by its numeric ID.
    Args:
        chat_id (int): The chat ID of the conversation.
        limit (int): The number of messages to get.
    """

    if not user_client.is_connected():
        await user_client.connect()
        
        if not await user_client.is_user_authorized():
            return {"error": "Telegram session is invalid or expired! Generate a new TELEGRAM_SESSION_STRING."}
    try:
        messages = await user_client.get_messages(chat_id, limit=limit)
        if not messages:
            return {"error": "No messages found in this chat."}
        
        return {"messages": [_parse_message(msg) for msg in messages]}
        
    except ValueError:
        return {"error": "No chat found with this ID."}
    except Exception as e:
        return {"error": f"Failed to get messages: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def get_chat_id(recipient: str = Field(description="The recipient's contact handle (@username) or phone number (e.g. +1234567890).")) -> dict:
    """Gets the numeric chat ID of a user or conversation along with their name.
    IMPORTANT: This tool resolves a user's handle or phone number into a numeric chat ID.
    Use this tool when you have a username or phone number and need to get the chat ID for sending messages.
    Args:
        recipient (str): The contact handle (e.g., @username) or full phone number.
    """

    if not user_client.is_connected():
        await user_client.connect()
        
        if not await user_client.is_user_authorized():
            return {"error": "Telegram session is invalid or expired! Generate a new TELEGRAM_SESSION_STRING."}
    try:
        chat = await user_client.get_entity(recipient)

        name = getattr(chat, 'title', None)
        if not name:
            first = getattr(chat, 'first_name', '') or ''
            last = getattr(chat, 'last_name', '') or ''
            name = (first + ' ' + last).strip()
            
        return {
            "chat_id": chat.id,
            "name": name or getattr(chat, 'username', 'Unknown'),
            "type": type(chat).__name__,
            "is_bot": getattr(chat, 'bot', False),
            "verified": getattr(chat, 'verified', False),
            "phone": getattr(chat, 'phone', None)
        }

    except ValueError:
        return {"error": "No chat found with this ID."}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def get_recent_chats(limit: int = Field(5, description="The number of recent chats to retrieve.")) -> dict:
    """Gets the most recent active conversations/dialogs.
    IMPORTANT: This tool gets the most recent chats you have interacted with on Telegram.
    Use this tool when you need to see who you recently talked to or get their chat IDs.
    Args:
        limit (int): The number of recent chats to retrieve.
    """

    if not user_client.is_connected():
        await user_client.connect()
        
        if not await user_client.is_user_authorized():
            return {"error": "Telegram session is invalid or expired! Generate a new TELEGRAM_SESSION_STRING."}
    try:
        dialogs = await user_client.get_dialogs(limit=limit)
        return {"chats": [{
            "chat_id": d.id,
            "name": d.name,
            "is_group": d.is_group,
            "is_channel": d.is_channel,
            "is_bot": getattr(d.entity, 'bot', False),
            "phone": getattr(d.entity, 'phone', None)
        } for d in dialogs]}
    except Exception as e:
        return {"error": f"Failed to get recent chats: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def search_messages(chat_id: int = Field(description="The chat ID of the conversation."), query: str = Field(description="The text to search for."), limit: int = Field(description="The number of messages to return.", default=10)) -> dict:
    """Searches a specific chat for messages containing a keyword.
    IMPORTANT: This tool searches for a keyword in a specific conversation on Telegram.
    Use this tool when you need to find a specific message or topic in a chat.
    Args:
        chat_id (int): The numeric chat ID.
        query (str): The text/keyword to search for.
        limit (int): Max number of results.
    """

    if not user_client.is_connected():
        await user_client.connect()
        
        if not await user_client.is_user_authorized():
            return {"error": "Telegram session is invalid or expired! Generate a new TELEGRAM_SESSION_STRING."}
    try:
        messages = await user_client.get_messages(chat_id, search=query, limit=limit)
        if not messages:
            return {"error": "No matching messages found in this chat."}
        
        return {"messages": [_parse_message(msg) for msg in messages]}
    except Exception as e:
        return {"error": f"Failed to search messages: {str(e)}"}


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)