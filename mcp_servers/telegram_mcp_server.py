import asyncio
import os
import time
from dotenv import load_dotenv
from fastmcp import FastMCP
from telethon import TelegramClient, functions, types, utils
from mcp.types import ToolAnnotations
from pydantic import Field
from telethon.tl.types import MessageActionPhoneCall, InputMessagesFilterPinned


load_dotenv()

api_id_raw = os.getenv("API_TELEGRAM_ID")
api_hash = os.getenv("API_TELEGRAM_HASH")

if not api_id_raw or not api_hash:
    raise RuntimeError("Set API_TELEGRAM_ID and API_TELEGRAM_HASH in your environment or .env file.")

API_ID = int(api_id_raw)
API_HASH = str(api_hash)

mcp = FastMCP("Telegram Server")

# Session file name (kept as before so your existing login keeps working).
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "ukranian_session")
user_client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

_connect_lock = asyncio.Lock()
SESSION_ERROR = (
    f"Telegram session '{SESSION_NAME}' is missing, invalid or expired. "
    "Log in again with a separate script to recreate the session file."
)


async def _ensure_ready():
    """Connect if needed and verify the session. Returns an error string, or None if ready."""
    async with _connect_lock:
        if not user_client.is_connected():
            await user_client.connect()
        if not await user_client.is_user_authorized():
            return SESSION_ERROR
    return None


def _title_text(title) -> str:
    """Folder titles are TextWithEntities on new Telegram layers, plain str on old ones."""
    return getattr(title, "text", title)


def _make_title(text: str):
    """Build a folder title in whichever form the installed Telethon expects."""
    if hasattr(types, "TextWithEntities"):
        return types.TextWithEntities(text=text, entities=[])
    return text


def _peer_key(peer):
    """Stable comparison key for a (possibly Input*) peer."""
    try:
        return utils.get_peer_id(peer)
    except Exception:
        return repr(peer)


async def _get_filters():
    result = await user_client(functions.messages.GetDialogFiltersRequest())
    return getattr(result, "filters", result)

def _parse_message(msg):
    media_type = None
    sticker_emoji = None
    file_name = None

    if getattr(msg, 'file', None):
        file_name = msg.file.name

    if msg.sticker:
        media_type = "sticker"
        sticker_emoji = msg.file.emoji if msg.file else None
    elif msg.photo:
        media_type = "photo"
    elif msg.video:
        media_type = "video"
    elif msg.voice:
        media_type = "voice"
    elif msg.audio:
        media_type = "audio"
    elif msg.document:
        media_type = "document"

    is_phone_call = False
    if hasattr(msg, 'action') and msg.action:
        if isinstance(msg.action, MessageActionPhoneCall):
            is_phone_call = True

    sender_id = "me" if msg.out else getattr(msg, "sender_id", None)

    return {
        "message_id": msg.id,
        "text": msg.text or "",  
        "sender_id": sender_id,
        "timestamp": msg.date.isoformat() if msg.date else None,
        "is_edited": msg.edit_date is not None,
        "reply_to_message_id": msg.reply_to_msg_id if hasattr(msg, 'reply_to_msg_id') else None,
        "media_type": media_type,
        "file_name": file_name,
        "sticker_emoji": sticker_emoji,
        "is_phone_call": is_phone_call,
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
    reply_to_message_id: int | None = Field(default=None, description="Optional ID of a message to reply to.")) -> str:
    """Sends a message to a chat or user by ID.
    IMPORTANT: This tool sends a message directly to a user or chat on Telegram.
    CRITICAL RESTRICTION: You MUST NEVER call this tool on your own. You must FIRST output the [TELEGRAM MESSAGE PREPARED] text to the user and wait for them to explicitly reply "yes" or "send" in the next turn before executing this tool.
    Args:
        chat_id (int): The numeric chat ID.
        message (str): The content of the message to send.
        reply_to_message_id (int): Optional ID of a message to reply to.
    """
    if (err := await _ensure_ready()):
        return f"Error: {err}"
    try:
        sent_msg = await user_client.send_message(chat_id, message, reply_to=reply_to_message_id, parse_mode=None)
        return f"Message sent successfully to {chat_id}. Message ID: {sent_msg.id}"
    except Exception as e:
        return f"Error sending message: {str(e)}"
        
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def edit_message(
    chat_id: int = Field(description="The chat ID of the conversation."), 
    message_id: int = Field(description="The ID of the message to edit."),
    message: str = Field(description="The new text of the message.")) -> str:
    """Edits a message in a conversation.
    IMPORTANT: This tool edits a message directly in a chat on Telegram.
    CRITICAL RESTRICTION: You MUST NEVER call this tool on your own without explicit permission. You must FIRST output the [TELEGRAM EDIT PREPARED] text to the user and wait for them to explicitly reply "yes" or "edit it" in the next turn before executing this tool.
    Args:
        chat_id (int): The numeric chat ID.
        message_id (int): The ID of the message to edit.
        message (str): The new content of the message.
    """
    if (err := await _ensure_ready()):
        return f"Error: {err}"
    try:
        await user_client.edit_message(chat_id, message_id, message, parse_mode=None)
        return f"Message edited in {chat_id}"
    except Exception as e:
        return f"Error editing message: {str(e)}"

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
))
async def get_message(chat_id: int = Field(description="The chat ID of the conversation."), limit: int = Field(description="The number of messages to get.", default=1)) -> dict:
    """Gets the last message from a chat.
    IMPORTANT: This tool gets the last message from a chat on Telegram.
    Use this tool when you need to get the last message from a chat by its numeric ID.
    Args:
        chat_id (int): The chat ID of the conversation.
        limit (int): The number of messages to get.
    """

    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        messages = await user_client.get_messages(chat_id, limit=max(1, min(limit, 100)))
        if not messages:
            return {"error": "No messages found in this chat."}
        
        return {"messages": [_parse_message(msg) for msg in messages]}
        
    except ValueError:
        return {"error": "No chat found with this ID."}
    except Exception as e:
        return {"error": f"Failed to get messages: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
))
async def get_chat_id(
    recipient: str = Field(description="The recipient's handle (@username), phone number (+1234...), or display name (e.g. 'Mom').")
) -> dict:
    """Gets the numeric chat ID of a user or conversation along with their name.
    IMPORTANT: Resolves usernames, phone numbers, or contact display names into numeric chat IDs.
    """
    if (err := await _ensure_ready()):
        return {"error": err}

    try:
        chat = await user_client.get_entity(recipient)
        name = getattr(chat, 'title', None)
        if not name:
            first = getattr(chat, 'first_name', '') or ''
            last = getattr(chat, 'last_name', '') or ''
            name = (first + ' ' + last).strip()

        return {
            "chat_id": utils.get_peer_id(chat),   
            "name": name or getattr(chat, 'username', 'Unknown'),
            "type": type(chat).__name__,
            "is_bot": getattr(chat, 'bot', False),
            "phone": getattr(chat, 'phone', None)
        }
    except Exception:
        pass

    try:
        dialogs = await user_client.get_dialogs(limit=300)
        search_query = recipient.strip().lower()

        for d in dialogs:
            if d.name and search_query in d.name.lower():
                return {
                    "chat_id": d.id,
                    "name": d.name,
                    "type": type(d.entity).__name__,
                    "is_bot": getattr(d.entity, 'bot', False),
                    "phone": getattr(d.entity, 'phone', None)
                }

        return {"error": f"No chat found matching '{recipient}' in handles or active dialogs."}

    except Exception as e:
        return {"error": f"Failed to search chats: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
))
async def get_recent_chats(limit: int = Field(5, description="The number of recent chats to retrieve.")) -> dict:
    """Gets the most recent active conversations/dialogs.
    IMPORTANT: This tool gets the most recent chats you have interacted with on Telegram.
    Use this tool when you need to see who you recently talked to or get their chat IDs.
    Args:
        limit (int): The number of recent chats to retrieve.
    """

    if (err := await _ensure_ready()):
        return {"error": err}
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
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
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

    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        messages = await user_client.get_messages(chat_id, search=query, limit=max(1, min(limit, 100)))
        return {"messages": [_parse_message(msg) for msg in messages]}
    except Exception as e:
        return {"error": f"Failed to search messages: {str(e)}"}


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
))
async def read_whole_chat(
    chat_id: int = Field(description="The chat ID of the conversation."), 
    limit: int = Field(description="The number of messages to return. max 250.", default=100)
    ) -> dict:
    """Reads the most recent messages of a chat on Telegram (newest first, max 250).
    IMPORTANT: This tool reads chat history of a user or conversation on Telegram.
    Args:
        chat_id (int): The chat ID of the conversation.
        limit (int): How many of the latest messages to read (1-250).
    """

    if (err := await _ensure_ready()):
        return {"error": err}

    safe_limit = max(1, min(limit, 250))

    try:
        messages = [_parse_message(msg) async for msg in user_client.iter_messages(chat_id, limit=safe_limit)]
        return {"messages": messages}
    except Exception as e:
        return {"error": f"Failed to read whole chat: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
))
async def delete_message_in_chat(chat_id: int = Field(description="The chat ID of the conversation."), message_id: int = Field(description="The ID of the message to delete.")) -> dict:
    """Deletes a message from a chat on Telegram.
    IMPORTANT: This tool deletes a message from a chat on Telegram.
    Args:
        chat_id (int): The chat ID of the conversation.
        message_id (int): The ID of the message to delete.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        result = await user_client.delete_messages(chat_id, message_id)
        if result and not any(getattr(r, "pts_count", 1) for r in result):
            return {"error": f"Message {message_id} was not deleted (not found or no permission)."}
        return {"success": f"Message {message_id} deleted from chat {chat_id}"}
    except Exception as e:
        return {"error": f"Failed to delete message: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def forward_message(chat_id: int = Field(description="The chat ID of the conversation."), message_id: int = Field(description="The ID of the message to forward."), to_chat_id: int = Field(description="The chat ID of the conversation to forward to.")) -> dict:
    """Forwards a message from a chat to another chat on Telegram. 
    IMPORTANT: This tool forwards a message from a chat to another chat on Telegram.
    Do not use chat_id as to_chat_id. Use get_recent_chats or get_chat_id to get the chat_id of the conversation to forward to.
    Args:
        chat_id (int): The chat ID of the conversation.
        message_id (int): The ID of the message to forward.
        to_chat_id (int): The chat ID of the conversation to forward to.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        await user_client.forward_messages(to_chat_id, message_id, chat_id)
        return {"success": f"Message {message_id} forwarded from chat {chat_id} to {to_chat_id}"}
    except Exception as e:
        return {"error": f"Failed to forward message: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))

async def forward_to_saved_messages(chat_id: int = Field(description="The chat ID of the conversation."), message_id: int = Field(description="The ID of the message to forward.")) -> dict:
    """Forwards a message from a chat to saved messages on Telegram. 
    IMPORTANT: This tool forwards a message from a chat to saved messages on Telegram.
    Do not use chat_id as to_chat_id. Use get_recent_chats or get_chat_id to get the chat_id of the conversation to forward to.
    Args:
        chat_id (int): The chat ID of the conversation.
        message_id (int): The ID of the message to forward.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        await user_client.forward_messages("me", message_id, chat_id)
        return {"success": f"Message {message_id} forwarded from chat {chat_id} to saved messages"}
    except Exception as e:
        return {"error": f"Failed to forward message: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def send_message_to_saved_messages(message: str = Field(description="The message to send to saved messages.")) -> dict:
    """Sends a message to saved messages on Telegram. 
    IMPORTANT: This tool sends a message to saved messages on Telegram.
    Args:
        message (str): The message to send to saved messages.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        sent = await user_client.send_message("me", message, parse_mode=None)
        return {"success": f"Message sent to saved messages. Message ID: {sent.id}"}
    except Exception as e:
        return {"error": f"Failed to send message: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def pin_message(chat_id: int = Field(description="The chat ID of the conversation."), message_id: int = Field(description="The ID of the message to pin.")) -> dict:
    """Pins a message on Telegram.
    IMPORTANT: Use this tool when you want to pin a message on Telegram.
    Args:
        chat_id (int): The chat ID of the conversation.
        message_id (int): The ID of the message to pin.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        await user_client.pin_message(chat_id, message_id)
        return {"success": f"Message {message_id} pinned to chat {chat_id}"}
    except Exception as e:
        return {"error": f"Failed to pin message: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def unpin_message(chat_id: int = Field(description="The chat ID of the conversation."), message_id: int = Field(description="The ID of the message to unpin.")) -> dict:
    """Unpins a message from a chat on Telegram.
    IMPORTANT: Use this tool when you want to unpin a message from a chat on Telegram.
    Args:
        chat_id (int): The chat ID of the conversation.
        message_id (int): The ID of the message to unpin.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        await user_client.unpin_message(chat_id, message_id)
        return {"success": f"Message {message_id} unpinned from chat {chat_id}"}
    except Exception as e:
        return {"error": f"Failed to unpin message: {str(e)}"}


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
))
async def get_pinned_messages(chat_id: int = Field(description="The chat ID of the conversation.")) -> dict:
    """Gets 200 pinned messages from a chat on Telegram.
    IMPORTANT: Use this tool when you want to get all pinned messages from a chat on Telegram.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        messages = await user_client.get_messages(chat_id, filter=InputMessagesFilterPinned(), limit=200)
        
        if not messages:
            return {"messages": []}
            
        return {"messages": [_parse_message(msg) for msg in messages]}
        
    except Exception as e:
        return {"error": f"Failed to get pinned messages: {str(e)}"}


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
))
async def get_folder_chats(folder_id: int = Field(description="The ID of the folder to get chats from.")):
    """Gets a list of chats explicitly included in a Telegram folder."""
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        result = await user_client(functions.messages.GetDialogFiltersRequest())
        filters = getattr(result, 'filters', result)
        
        target_folder = next((f for f in filters if getattr(f, 'id', None) == folder_id), None)
        
        if not target_folder:
            return {"error": f"Folder ID {folder_id} not found."}
            
        peers = getattr(target_folder, 'pinned_peers', []) + getattr(target_folder, 'include_peers', [])
        
        chats = []
        for peer in peers:
            try:
                entity = await user_client.get_entity(peer)
                name = getattr(entity, 'title', None)
                if not name:
                    first = getattr(entity, 'first_name', '') or ''
                    last = getattr(entity, 'last_name', '') or ''
                    name = (first + ' ' + last).strip() or getattr(entity, 'username', 'Unknown')
                
                chats.append({
                    "chat_id": utils.get_peer_id(entity),
                    "name": name,
                    "type": type(entity).__name__
                })
            except Exception:
                continue

        return {"folder_name": _title_text(target_folder.title), "chats": chats}

    except Exception as e:
        return {"error": f"Failed to get folder chats: {str(e)}"}


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
))
async def get_folders():
    """Gets all folders on Telegram.
    IMPORTANT: Use this tool when you want to get all folders on Telegram.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        result = await user_client(functions.messages.GetDialogFiltersRequest())
        filters = getattr(result, 'filters', result)
        
        folders = []
        for f in filters:
            if hasattr(f, 'id') and hasattr(f, 'title'):
                folders.append({"name": _title_text(f.title), "id": f.id})
                
        return {"folders": folders}
        
    except Exception as e:
        return {"error": f"Failed to get folders: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def create_empty_folder(title: str = Field(description="The name of the new folder.")) -> dict:
    """Creates a new Telegram folder with the given title."""
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        result = await user_client(functions.messages.GetDialogFiltersRequest())
        existing_filters = getattr(result, 'filters', result)
        existing_ids = [f.id for f in existing_filters if hasattr(f, 'id')]
        
        folder_id = 2  
        while folder_id in existing_ids:
            folder_id += 1

        safe_me = types.InputPeerSelf()

        new_filter = types.DialogFilter(
            id=folder_id,
            title=_make_title(title),
            pinned_peers=[],
            include_peers=[safe_me],
            exclude_peers=[],
            contacts=False,
            non_contacts=False,
            groups=False,
            broadcasts=False,
            bots=False,
            exclude_muted=False,
            exclude_read=False,
            exclude_archived=False
        )

        await user_client(functions.messages.UpdateDialogFilterRequest(
            id=folder_id,
            filter=new_filter
        ))
        
        return {"success": f"Created folder '{title}' with ID {folder_id}", "folder_id": folder_id}

    except Exception as e:
        return {"error": f"Failed to create folder: {str(e)}"}
        

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def move_chat_to_folder(chat_id: int = Field(description="The chat ID of the conversation."), folder_id: int = Field(description="The ID of the folder to move the chat to.")):
    """Moves a chat to a folder on Telegram.
    IMPORTANT: Use this tool when you want to move a chat to a folder on Telegram.
    Args:
        chat_id (int): The chat ID of the conversation.
        folder_id (int): The ID of the folder to move the chat to.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        filters = await _get_filters()
        target = next((f for f in filters if getattr(f, "id", None) == folder_id and hasattr(f, "title")), None)
        if not target:
            return {"error": f"Folder ID {folder_id} not found."}

        peer = await user_client.get_input_entity(chat_id)
        key = _peer_key(peer)
        if any(_peer_key(p) == key for p in target.include_peers):
            return {"success": f"Chat {chat_id} is already in folder {folder_id}"}

        if hasattr(target, "exclude_peers"):
            target.exclude_peers = [p for p in target.exclude_peers if _peer_key(p) != key]
        target.include_peers.append(peer)

        await user_client(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=target))
        return {"success": f"Chat {chat_id} moved to folder {folder_id}"}
    except Exception as e:
        return {"error": f"Failed to move chat: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
))
async def delete_folder(folder_id: int = Field(description="The ID of the folder to delete.")):
    """Deletes a folder on Telegram.
    IMPORTANT: Use this tool when you want to delete a folder on Telegram. The folder is removed; its chats stay in your chat list.
    Args:
        folder_id (int): The ID of the folder to delete.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        await user_client(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=None))
        return {"success": f"Folder {folder_id} deleted successfully."}
    except Exception as e:
        return {"error": f"Failed to delete folder: {str(e)}"}
        
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def archive_chat(chat_id: int = Field(description="The chat ID of the conversation.")) -> dict:
    """Archives a chat on Telegram.
    IMPORTANT: Use this tool when you want to archive a chat on Telegram.
    Args:
        chat_id (int): The chat ID of the conversation.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        await user_client.edit_folder(chat_id, 1)
        return {"success": f"Chat {chat_id} archived successfully."}
    except Exception as e:
        return {"error": f"Failed to archive chat: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def unarchive_chat(chat_id: int = Field(description="The chat ID of the conversation.")) -> dict:
    """Unarchives a chat on Telegram.
    IMPORTANT: Use this tool when you want to unarchive a chat on Telegram.
    Args:
        chat_id (int): The chat ID of the conversation.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        await user_client.edit_folder(chat_id, 0)
        return {"success": f"Chat {chat_id} unarchived successfully."}
    except Exception as e:
        return {"error": f"Failed to unarchive chat: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def mute_chat(
    chat_username_or_id: str = Field(description="The chat username (e.g., '@username') or ID to mute."),
    duration_minutes: int = Field(default=0, description="How many minutes to mute the chat. Leave as 0 to mute forever.")
) -> dict:
    """Mutes notifications for a specific Telegram chat.
    IMPORTANT: Use this tool when you want to mute notifications for a specific chat on Telegram.
    Args:
        chat_username_or_id (str): The chat username (e.g., '@username') or ID to mute.
        duration_minutes (int): How many minutes to mute the chat. Leave as 0 to mute forever.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        target = chat_username_or_id.strip()
        if target.lstrip("-").isdigit():
            target = int(target)
        entity = await user_client.get_input_entity(target)
        
        if duration_minutes == 0:

            mute_until = 2147483647
        else:
            mute_until = int(time.time()) + (duration_minutes * 60)

        settings = types.InputPeerNotifySettings(mute_until=mute_until)
        
        await user_client(functions.account.UpdateNotifySettingsRequest(
            peer=types.InputNotifyPeer(peer=entity),
            settings=settings
        ))
        
        duration_str = "forever" if duration_minutes == 0 else f"for {duration_minutes} minutes"
        return {"success": f"Successfully muted {chat_username_or_id} {duration_str}."}

    except Exception as e:
        return {"error": f"Failed to mute chat: {str(e)}"}

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
))
async def unmute_chat(
    chat_username_or_id: str = Field(description="The chat username (e.g., '@username') or ID to unmute.")
) -> dict:
    """Unmutes notifications for a specific Telegram chat.
    IMPORTANT: Use this tool when you want to unmute notifications for a specific chat on Telegram.
    Args:
        chat_username_or_id (str): The chat username (e.g., '@username') or ID to unmute.
    """
    if (err := await _ensure_ready()):
        return {"error": err}
    try:
        target = chat_username_or_id.strip()
        if target.lstrip("-").isdigit():
            target = int(target)
        entity = await user_client.get_input_entity(target)
        
        settings = types.InputPeerNotifySettings(mute_until=0)
        
        await user_client(functions.account.UpdateNotifySettingsRequest(
            peer=types.InputNotifyPeer(peer=entity),
            settings=settings
        ))
        
        return {"success": f"Successfully unmuted {chat_username_or_id}."}

    except Exception as e:
        return {"error": f"Failed to unmute chat: {str(e)}"}


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)