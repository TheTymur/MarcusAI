import os
import re
import sys
import json
import base64
import socket
import logging
import ipaddress
import email.utils
from email.message import EmailMessage
from urllib.parse import urlparse, unquote, parse_qs
from typing import Literal

import requests
from bs4 import BeautifulSoup
from pydantic import Field
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from agent.data.database_manager import Emailprocessing

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

SCOPES = [
    'https://mail.google.com/',
    'https://www.googleapis.com/auth/gmail.settings.basic'
]

mcp = FastMCP("Gmail Unified Server")

class GmailServiceManager:
    """Manages multi-account authentication, parsing, and local database caching."""
    def __init__(self):
        self.services = {}
        self.db_manager = Emailprocessing()
        for account in ["personal", "work"]:
            self.authenticate(account)

    def authenticate(self, account: str):
        creds = None
        token_file = f"token_{account}.json"
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists('credentials.json'):
                    logger.warning(f"credentials.json not found! Required for {account} account.")
                    return
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)

            with open(token_file, "w") as token:
                token.write(creds.to_json())

        self.services[account] = build("gmail", "v1", credentials=creds)

    def get_service(self, account: str):
        service = self.services.get(account)
        if not service:
            raise ValueError(f"Account '{account}' not authenticated. Valid options are 'personal' and 'work'.")
        return service

    def extract_body(self, part):
        found_text, found_html, attachments = "", "", []
        
        filename = part.get('filename')
        if filename:
            attachment_id = part.get('body', {}).get('attachmentId')
            if attachment_id:
                attachments.append({"filename": filename, "attachmentId": attachment_id})

        if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
            found_text = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
        elif part.get('mimeType') == 'text/html' and 'data' in part.get('body', {}):
            found_html = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
        
        if 'parts' in part:
            for subpart in part['parts']:
                sub_text, sub_html, sub_attachments = self.extract_body(subpart)
                if sub_text: found_text += sub_text
                if sub_html: found_html += sub_html
                if sub_attachments: attachments.extend(sub_attachments)
                
        return found_text, found_html, attachments

    def parse_and_cache_email(self, msg_data, account: str):
        payload = msg_data.get("payload", {})
        headers = payload.get("headers", [])
        
        sender, recipient, subject, date, message_id = "Unknown", "Unknown", "No Subject", "Unknown", None
        in_reply_to, references, cc, reply_to = "None", "None", "", "Unknown"
        
        for header in headers:
            name = header['name'].lower()
            if name == 'from': sender = header['value']
            elif name == 'to': recipient = header['value']
            elif name == 'cc': cc = header['value']
            elif name == 'subject': subject = header['value']
            elif name == 'reply-to': reply_to = header['value']
            elif name == 'date': 
                try:
                    dt = email.utils.parsedate_to_datetime(header['value'])
                    date = dt.astimezone().strftime('%Y-%m-%d %H:%M')
                except Exception:
                    date = header['value']
            elif name == 'message-id': message_id = header['value']
            elif name == 'in-reply-to': in_reply_to = header['value']
            elif name == 'references': references = header['value']

        plain_text, html_text, attachments = self.extract_body(payload)
        
        if html_text:
            soup = BeautifulSoup(html_text, "html.parser")
            for element in soup(["script", "style"]):
                element.decompose()
            for br in soup.find_all("br"):
                br.replace_with("\n")
            for block in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"]):
                block.insert_after(soup.new_string("\n"))
            
            body = soup.get_text(separator=" ", strip=True)
            body = re.sub(r'[ \t]+', ' ', body)
            body = re.sub(r' \n |\n | \n', '\n', body)
            body = re.sub(r'\n{3,}', '\n\n', body).strip()
        elif plain_text:
            body = plain_text
        else:
            body = msg_data.get("snippet", "No Body available")
            
        gmail_id = msg_data['id']
        attachments_json = json.dumps(attachments) if attachments else None
        
        is_new = self.db_manager.store_email(message_id, gmail_id, sender, recipient, subject, date, body, account, attachments=attachments_json)
        if is_new:
            self.db_manager.process_email(body, message_id, gmail_id, sender, subject, date, account)

        return {
            "sender": sender, "recipient": recipient, "cc": cc, "subject": subject, 
            "date": date, "message_id": message_id, "in_reply_to": in_reply_to, 
            "references": references, "reply_to": reply_to, "body": body, 
            "labels": msg_data.get("labelIds", []), "attachments": attachments
        }

    @staticmethod
    def is_safe_url(url: str):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        try:
            for info in socket.getaddrinfo(parsed.hostname, None):
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False
        except (socket.gaierror, ValueError):
            return False
        return True

manager = GmailServiceManager()

# --- Local Database Tools ---
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def search_local_database(
    sender: str = Field(None, description="The sender email address to filter by (partial match)."), 
    subject: str = Field(None, description="The subject to filter by (partial match)."), 
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Search the local SQLite database for emails matching specific criteria.
    Args:
        sender (str, optional): The sender email address to filter by. Uses partial matching.
        subject (str, optional): The subject to filter by. Uses partial matching.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    return manager.db_manager.search_email_metadata(sender, subject)

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def read_local_email(
    email_id: str = Field(description="The ID of the local email to read."), 
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Read the full body of an email from the local SQLite database using its email_id.
    Args:
        email_id (str): The ID of the local email to read.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    return manager.db_manager.read_local_email(email_id)

# --- Email Reading/Searching Tools ---
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def read_recent_emails(
    limit: int = Field(5, description="The maximum number of emails to return."), 
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Reads the most recent emails from the user's Gmail inbox. 
    IMPORTANT: This returns a list of emails. Each email has an 'ID' field. You MUST save and use this exact 'ID' if you need to read the full content, reply, forward, or delete the email later.
    Args:
        limit (int): The maximum number of emails to return.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=limit).execute()
        messages = results.get("messages", [])

        if not messages:
            return "No new emails found."
    
        email_data = []
        for msg in messages:
            txt = service.users().messages().get(userId="me", id=msg["id"], format="metadata", metadataHeaders=["From", "To", "Subject", "Date", "Message-ID"]).execute()
            parsed = manager.parse_and_cache_email(txt, account)
            is_read = "UNREAD" not in parsed['labels']
            email_data.append(f"- ID: {msg['id']} | Read: {is_read} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | From: {parsed['sender']} | To: {parsed['recipient']} | Subject: <email_subject>{parsed['subject']}</email_subject>")
    
        return "\n".join(email_data)

    except Exception as error:
        return f"An error occurred connecting to Gmail: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def search_emails(
    query: str = Field(description="The search query string (like in Gmail search bar)."), 
    limit: int = Field(5, description="The maximum number of emails to return."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Searches for emails in the user's Gmail account based on a query. 
    IMPORTANT: This returns a list of emails. Each email has an 'ID' field. You MUST save and use this exact 'ID' if you need to read the full content, reply, forward, or delete the email later.
    Args:
        query (str): The search query string (like in Gmail search bar).
        limit (int): The maximum number of emails to return.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        messages = results.get("messages", [])

        if not messages:
            return "No emails found matching that query."
    
        email_data = []
        for msg in messages:
            txt = service.users().messages().get(userId="me", id=msg["id"], format="metadata", metadataHeaders=["From", "To", "Subject", "Date", "Message-ID"]).execute()
            parsed = manager.parse_and_cache_email(txt, account)
            email_data.append(f"- ID: {msg['id']} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | From: {parsed['sender']} | To: {parsed['recipient']} | Subject: <email_subject>{parsed['subject']}</email_subject>")
    
        return "\n".join(email_data)

    except Exception as error:
        return f"An error occurred searching for emails: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def read_email_content(
    email_id: str = Field(description="The ID of the email to read."), 
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Reads the full content of a specific email in the user's Gmail account.
    You MUST provide the 'email_id' parameter. You can find the email_id by first calling the 'read_recent_emails' or 'search_emails' tools and looking for the 'ID:' field in the results.
    Args:
        email_id (str): The ID of the email to read.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        txt = service.users().messages().get(userId="me", id=email_id).execute()
        parsed = manager.parse_and_cache_email(txt, account)
        
        if "UNREAD" in parsed['labels']:
            try:
                service.users().messages().modify(userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}).execute()
            except Exception:
                pass

        body = parsed['body']
        if len(body) > 4000:
            body = body[:4000] + "\n\n...[EMAIL BODY TRUNCATED TO SAVE TOKENS]..."
            
        cc_string = f" | Cc: {parsed['cc']}" if parsed['cc'] else ""
        return f"- ID: {email_id} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | From: {parsed['sender']} | To: {parsed['recipient']}{cc_string} | Reply-to: {parsed['reply_to']} | In-Reply-To: {parsed['in_reply_to']} | References: {parsed['references']} | Subject: <email_subject>{parsed['subject']}</email_subject> | Body:\n<email_body>\n{body}\n</email_body>"
        
    except Exception as error:
        return f"An error occurred reading the email content: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def download_attachment(
    email_id: str = Field(description="The ID of the email containing the attachment."),
    attachment_id: str = Field(description="The ID of the attachment to download."),
    filename: str = Field(description="The name to save the attachment as."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Downloads an attachment from an email and saves it to the macOS Downloads folder.
    You MUST provide the 'email_id' and 'attachment_id'. You can find these by first calling 'read_email_content'.
    Args:
        email_id (str): The ID of the email containing the attachment.
        attachment_id (str): The ID of the attachment to download.
        filename (str): The name to save the attachment as.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        attachment = service.users().messages().attachments().get(userId='me', messageId=email_id, id=attachment_id).execute()
        data = attachment.get('data')
        if not data:
            return f"Error: Attachment {attachment_id} has no data."
        
        file_data = base64.urlsafe_b64decode(data.encode('UTF-8'))
        downloads_path = os.path.expanduser("~/Downloads")
        full_path = os.path.join(downloads_path, filename)
        
        with open(full_path, 'wb') as f:
            f.write(file_data)
        
        return f"Attachment successfully downloaded and saved to: {full_path}"
    except Exception as error:
        return f"An error occurred downloading the attachment: {error}"

# --- Label Management Tools ---
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def create_label(
    label_name: str = Field(description="The name of the label to create."),
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] = Field("labelShow", description="Visibility in label list."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Creates a new, empty label in the user's Gmail account.
    This only creates the label itself. To actually apply this label to an email, you must subsequently call 'apply_label'.
    Args:
        label_name (str): The name of the new label to create.
        label_list_visibility (str): Visibility in the label list.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        label = service.users().labels().create(userId="me", body={
            "name": label_name,
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": "show"
        }).execute()
        return f"Label '{label_name}' created successfully and its ID is {label['id']}."
    except Exception as error:
        return f"An error occurred creating the label: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def list_labels(
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Lists all labels in the user's Gmail account.
    Args:
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])

        if not labels:
            return "No labels found."
    
        label_data = []
        for label in labels:
            label_data.append(f"- ID: {label['id']} | Name: {label['name']}")
    
        return "\n".join(label_data)
    except Exception as error:
        return f"An error occurred listing the labels: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def apply_label(
    email_id: str = Field(description="The ID of the email."), 
    label_id: str = Field(description="The ID of the label to apply."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Applies an existing label to a specific email message in the user's Gmail account.
    Args:
        email_id (str): The ID of the email.
        label_id (str): The exact ID of the existing label to apply.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        service.users().messages().modify(userId="me", id=email_id, body={
            "addLabelIds": [label_id]
        }).execute()
        return f"Label '{label_id}' applied to message '{email_id}' successfully."
    except Exception as error:
        return f"An error occurred applying the label: {error}" 

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def remove_label(
    email_id: str = Field(description="The ID of the email."), 
    label_id: str = Field(description="The ID of the label to remove."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Removes a label from a specific email message (untags the email).
    Also you can use this tool to mark an email as read by setting the label_id to "UNREAD".
    Args:
        email_id (str): The ID of the email.
        label_id (str): The ID of the label to remove from this email.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        service.users().messages().modify(userId="me", id=email_id, body={
            "removeLabelIds": [label_id]
        }).execute()
        return f"Label '{label_id}' removed from message '{email_id}' successfully."
    except Exception as error:
        return f"An error occurred removing the label: {error}" 

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def mark_email_as_read(
    email_id: str = Field(description="The ID of the email to mark as read."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Marks an email as read by removing the UNREAD label.
    Args:
        email_id (str): The ID of the email to mark as read.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        service.users().messages().modify(userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}).execute()
        return f"Successfully marked email {email_id} as read."
    except Exception as e:
        return f"Error marking email as read: {e}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )
)
def delete_label(
    label_id: str = Field(description="The ID of the label to delete."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Permanently deletes a label from the user's Gmail account entirely.
    Args:
        label_id (str): The ID of the label to permanently delete.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        service.users().labels().delete(userId="me", id=label_id).execute()
        return f"Label '{label_id}' deleted successfully."
    except Exception as error:
        return f"An error occurred deleting the label: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def count_messages_in_label(
    label_id: str = Field(description="The ID of the label to count messages for."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Counts the number of messages in a specific label in the user's Gmail account.
    Args:
        label_id (str): The ID of the label to count messages for.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().messages().list(userId="me", labelIds=[label_id]).execute()
        message_count = results.get("resultSizeEstimate", 0)
        return f"The label '{label_id}' has {message_count} message(s)."
    except Exception as error:
        return f"An error occurred counting messages in label '{label_id}': {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def update_label(
    label_id: str = Field(description="The ID of the label to update."),
    label_name: str = Field(default=None, description="The new name of the label (optional)."),
    label_list_visibility: str = Field(default=None, description="Visibility in label list: 'labelShow', 'labelShowIfUnread', or 'labelHide'."),
    message_list_visibility: str = Field(default=None, description="Visibility of messages with this label: 'show' or 'hide'."),
    text_color: str = Field(default=None, description="The text color as a hex string (e.g. '#000000')."),
    background_color: str = Field(default=None, description="The background color as a hex string (e.g. '#ffffff')."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Updates the properties of an existing label in the user's Gmail account.
    Args:
        label_id (str): The ID of the label to update.
        label_name (str): The new name of the label (optional).
        label_list_visibility (str): Visibility in the label list (optional).
        message_list_visibility (str): Visibility of messages with this label (optional).
        text_color (str): The text color as a hex string (optional).
        background_color (str): The background color as a hex string (optional).
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        body = {}
        if label_name is not None:
            body["name"] = label_name
        if label_list_visibility is not None:
            body["labelListVisibility"] = label_list_visibility
        if message_list_visibility is not None:
            body["messageListVisibility"] = message_list_visibility
        
        if text_color is not None or background_color is not None:
            body["color"] = {}
            if text_color is not None:
                body["color"]["textColor"] = text_color
            if background_color is not None:
                body["color"]["backgroundColor"] = background_color
        
        if not body:
            return "No updates provided."

        service.users().labels().patch(userId="me", id=label_id, body=body).execute()
        return f"Label '{label_id}' updated successfully."
    except Exception as error:
        return f"An error occurred updating the label: {error}"

# --- Filter Management Tools ---
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def create_filter(
    from_sender: str = Field(default=None, description="Match emails from this sender."),
    to_recipient: str = Field(default=None, description="Match emails sent to this address."),
    subject: str = Field(default=None, description="Match emails with this subject."),
    query: str = Field(default=None, description="Match emails matching this Gmail search query."),
    add_label_ids: list[str] = Field(default=None, description="List of label IDs to add (e.g., ['TRASH', 'STARRED'])."),
    remove_label_ids: list[str] = Field(default=None, description="List of label IDs to remove (e.g., ['INBOX', 'UNREAD'])."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Creates a new filter to automatically handle incoming emails based on specified criteria.
    Args:
        from_sender (str, optional): Match emails from this sender.
        to_recipient (str, optional): Match emails sent to this address.
        subject (str, optional): Match emails with this subject.
        query (str, optional): Raw Gmail search query.
        add_label_ids (list[str], optional): List of label IDs to add.
        remove_label_ids (list[str], optional): List of label IDs to remove.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        if not any([from_sender, to_recipient, subject, query]):
            return "Error: You must provide at least one of 'from_sender', 'to_recipient', 'subject', or 'query'."

        criteria = {}
        if from_sender: criteria['from'] = from_sender
        if to_recipient: criteria['to'] = to_recipient
        if subject: criteria['subject'] = subject
        if query: criteria['query'] = query

        action = {}
        if add_label_ids: action['addLabelIds'] = add_label_ids
        if remove_label_ids: action['removeLabelIds'] = remove_label_ids

        filter_body = {"criteria": criteria, "action": action}
        result = service.users().settings().filters().create(userId='me', body=filter_body).execute()
        return f"Successfully created filter with ID: {result['id']}"
    except Exception as e:
        return f"Error creating filter: {str(e)}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def list_filters(
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Lists all filters in the user's Gmail account, including their IDs, criteria, and actions.
    Args:
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().settings().filters().list(userId="me").execute()
        filters = results.get('filter', [])
        
        if not filters:
            return "No filters found in your account."
            
        filter_data = []
        for f in filters:
            f_id = f.get('id')
            criteria = f.get('criteria', {})
            action = f.get('action', {})
            filter_data.append(f"- ID: {f_id} | Criteria: {criteria} | Action: {action}")
            
        return "\n".join(filter_data)
    except Exception as e:
        return f"An error occurred listing filters: {e}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )
)
def delete_filter(
    filter_id: str = Field(description="The ID of the filter to delete."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Permanently deletes a specific filter from the user's Gmail account.
    Args:
        filter_id (str): The ID of the filter to delete.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        service.users().settings().filters().delete(userId="me", id=filter_id).execute()
        return f"Successfully deleted filter with ID: {filter_id}"
    except Exception as e:
        return f"An error occurred deleting the filter: {e}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def block_sender(
    sender: str = Field(description="The email address to block."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Blocks an email sender by creating a filter to move all their emails to the trash.
    Args:
        sender (str): The email address to block.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    res = create_filter(from_sender=sender, add_label_ids=["TRASH"], remove_label_ids=["INBOX"], account=account)
    if "Successfully" in res:
        return f"Successfully blocked {sender}."
    return res

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def unblock_sender(
    sender: str = Field(description="The email address to unblock."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Unblocks an email sender by finding and removing their specific filter.
    Args:
        sender (str): The email address to unblock.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().settings().filters().list(userId="me").execute()
        filters = results.get('filter', [])
        
        filters_deleted = 0
        for f in filters:
            criteria = f.get('criteria', {})
            action = f.get('action', {})
            from_email = criteria.get('from', '')
            
            if sender.lower() in from_email.lower() and 'TRASH' in action.get('addLabelIds', []):
                filter_id = f['id']
                service.users().settings().filters().delete(userId="me", id=filter_id).execute()
                filters_deleted += 1
                
        if filters_deleted > 0:
            return f"Successfully unblocked {sender} by removing {filters_deleted} filter(s)."
        else:
            return f"Could not find any active block filter for {sender}."
            
    except Exception as e:
        return f"An error occurred unblocking {sender}: {e}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def list_blocked_senders(
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> list[str] | str:
    """Lists all blocked senders in the user's Gmail account.
    CRITICAL: This tool ONLY lists blocked senders. It CANNOT create, modify, or forward drafts/emails.
    Args:
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().settings().filters().list(userId="me").execute()
        filters = results.get('filter', [])
        
        blocked_senders = []
        for f in filters:
            criteria = f.get('criteria', {})
            action = f.get('action', {})
            from_email = criteria.get('from', '')
            
            if from_email and 'TRASH' in action.get('addLabelIds', []):
                blocked_senders.append(from_email)
        
        if not blocked_senders:
            return "No block filters found in your account."
        
        return blocked_senders
    except Exception as e:
        return f"An error occurred listing blocked senders: {e}"

# --- Message and Draft Management Tools ---
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )
)
def delete_message(
    email_id: str = Field(description="The ID of the email to delete."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Deletes an email or moves it to the trash. 
    Args:
        email_id (str): The ID of the email to delete.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        msg = service.users().messages().get(userId="me", id=email_id, format="metadata").execute()
        if 'TRASH' in msg.get('labelIds', []):
            service.users().messages().delete(userId="me", id=email_id).execute()
            manager.db_manager.delete_email(email_id)
            return f"Message '{email_id}' permanently deleted from trash successfully."
        else:
            service.users().messages().trash(userId="me", id=email_id).execute()
            manager.db_manager.delete_email(email_id)
            return f"Message '{email_id}' moved to trash successfully."
    except Exception as error:
        return f"An error occurred deleting the message: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )
)
def untrash_message(
    email_id: str = Field(description="The ID of the email to untrash."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Removes an email from the trash and restores it to the inbox.
    Args:
        email_id (str): The ID of the email to untrash.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        service.users().messages().untrash(userId="me", id=email_id).execute()
        return f"Message '{email_id}' restored from trash successfully."
    except Exception as error:
        return f"An error occurred untrashing the message: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )
)
def delete_draft(
    draft_id: str = Field(description="The ID of the draft to delete."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Permanently deletes a draft from the user's Gmail account.
    Args:
        draft_id (str): The ID of the draft to delete.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        service.users().drafts().delete(userId="me", id=draft_id).execute()
        return f"Draft '{draft_id}' deleted successfully."
    except Exception as error:
        return f"An error occurred deleting the draft: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def list_drafts(
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Lists all drafts and its details (To, Subject, Body) in the user's Gmail account.
    Args:
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        results = service.users().drafts().list(userId="me").execute()
        drafts = results.get("drafts", [])

        if not drafts:
            return "No drafts found."
    
        draft_data = []
        for draft in drafts:
            txt = service.users().drafts().get(userId="me", id=draft["id"]).execute()
            message = txt.get("message", {})
            payload = message.get("payload", {})
            headers = payload.get("headers", [])
            
            to, subject, date = "No To available", "No Subject available", "Unknown"
            
            for header in headers:
                name = header['name'].lower()
                if name == 'to': to = header['value']
                elif name == 'subject': subject = header['value']
                elif name == 'date': date = header['value']
            
            snippet = message.get("snippet", "No Body available")
            draft_data.append(f"- ID: {draft['id']} | Date: {date} | To: {to} | Subject: {subject} | Body: {snippet}")
    
        return "\n".join(draft_data)

    except Exception as error:
        return f"An error occurred listing the drafts: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def create_draft(
    to: str = Field(description="The recipient email address."), 
    subject: str = Field(description="The subject of the email."), 
    message: str = Field(description="The body of the email."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Creates a draft with To, Subject and Message in the user's Gmail account.
    Args:
        to (str): The recipient email address.
        subject (str): The subject of the email.
        message (str): The body of the email.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        email_msg = EmailMessage()
        email_msg.set_content(message)
        email_msg['To'], email_msg['Subject'] = to, subject

        encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()
        draft = {'message': {'raw': encoded_message}}
        draft = service.users().drafts().create(userId="me", body=draft).execute()
        return f"Draft '{draft['id']}' created successfully."
    except Exception as error:
        return f"An error occurred creating the draft: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def modify_draft(
    draft_id: str = Field(description="The ID of the draft to modify."), 
    to: str = Field(description="The recipient email address."), 
    subject: str = Field(description="The subject of the email."), 
    message: str = Field(description="The body of the email."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Modifies a draft in the user's Gmail account.
    Args:
        draft_id (str): The ID of the draft to modify.
        to (str): The recipient email address.
        subject (str): The subject of the email.
        message (str): The body of the email.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        existing_draft = service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
        existing_message = existing_draft.get('message', {})
        thread_id = existing_message.get('threadId')
        
        headers = existing_message.get('payload', {}).get('headers', [])
        in_reply_to, references = "", ""
        for header in headers:
            if header['name'].lower() == 'in-reply-to': in_reply_to = header['value']
            if header['name'].lower() == 'references': references = header['value']

        email_msg = EmailMessage()
        email_msg.set_content(message)
        email_msg['To'], email_msg['Subject'] = to, subject
        if in_reply_to: email_msg['In-Reply-To'] = in_reply_to
        if references: email_msg['References'] = references

        encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()
        draft = {'message': {'raw': encoded_message}}
        if thread_id: draft['message']['threadId'] = thread_id

        draft = service.users().drafts().update(userId="me", id=draft_id, body=draft).execute()
        return f"Draft '{draft['id']}' modified successfully."
    except Exception as error:
        return f"An error occurred modifying the draft: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def send_draft(
    draft_id: str = Field(description="The ID of the draft to send."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Sends an existing draft email. 
    Args:
        draft_id (str): The ID of the draft to send.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        draft = service.users().drafts().send(userId="me", body={'id': draft_id}).execute()
        return f"Draft '{draft['id']}' sent successfully."
    except Exception as error:
        return f"An error occurred sending the draft: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def create_response_draft(
    to: str = Field(description="The recipient email address."), 
    subject: str = Field(description="The subject of the email."), 
    message: str = Field(description="The body of the email."), 
    in_reply_to: str = Field(description="The Message-ID of the email being replied to."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Creates a draft reply to an email using the in_reply_to field to thread it correctly. 
    Args:
        to (str): The recipient of the email.
        subject (str): The subject of the email.
        message (str): The body of the email.
        in_reply_to (str): The ID of the email to reply to.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        original_msg = service.users().messages().get(userId="me", id=in_reply_to, format="metadata").execute()
        thread_id = original_msg.get('threadId')
        
        headers = original_msg.get('payload', {}).get('headers', [])
        message_id, references = "", ""
        
        for header in headers:
            if header['name'].lower() == 'message-id': message_id = header['value']
            if header['name'].lower() == 'references': references = header['value']
        
        email_msg = EmailMessage()
        email_msg.set_content(message)
        email_msg['To'] = to
        email_msg['Subject'] = subject if subject.lower().startswith('re:') else 'Re: ' + subject
        
        if message_id:
            email_msg['In-Reply-To'] = message_id
            email_msg['References'] = (references + " " + message_id).strip() if references else message_id

        encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()
        draft = {'message': {'raw': encoded_message, 'threadId': thread_id}}
        draft = service.users().drafts().create(userId="me", body=draft).execute()
        return f"Draft '{draft['id']}' created successfully."
    except Exception as error:
        return f"An error occurred creating the draft: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def forward_email(
    to: str = Field(description="The recipient email address."), 
    subject: str = Field(description="The subject of the email."), 
    message: str = Field(description="The body of the email."), 
    in_reply_to: str = Field(description="The Message-ID of the email being forwarded."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Forwards an email using the in_reply_to field to thread it correctly. 
    Args:
        to (str): The recipient of the email.
        subject (str): The subject of the email.
        message (str): The body of the email.
        in_reply_to (str): The ID of the email to forward.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    try:
        service = manager.get_service(account)
        original_msg = service.users().messages().get(userId="me", id=in_reply_to, format="full").execute()
        thread_id = original_msg.get('threadId')
        
        headers = original_msg.get('payload', {}).get('headers', [])
        _, original_body, _ = manager.extract_body(original_msg.get('payload', {}))
        
        combined_message = f"{message}\n\n---------- Forwarded message ---------\n{original_body}"
        message_id, references = "", ""
        
        for header in headers:
            if header['name'].lower() == 'message-id': message_id = header['value']
            if header['name'].lower() == 'references': references = header['value']
        
        email_msg = EmailMessage()
        email_msg.set_content(combined_message)
        email_msg['To'] = to
        email_msg['Subject'] = subject if subject.lower().startswith('fwd:') else 'Fwd: ' + subject
        
        if message_id:
            email_msg['In-Reply-To'] = message_id
            email_msg['References'] = (references + " " + message_id).strip() if references else message_id

        encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()
        draft = {'message': {'raw': encoded_message, 'threadId': thread_id}}
        draft = service.users().drafts().create(userId="me", body=draft).execute()
        return f"Draft '{draft['id']}' created successfully."
    except Exception as error:
        return f"An error occurred creating the draft: {error}"

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
)
def unsubscribe_from_email(
    email_id: str = Field(description="The ID of the email to unsubscribe from."),
    account: str = Field("personal", description="The account to use. Valid options: 'personal' (default), 'work'.")
) -> str:
    """Unsubscribes the user from a mailing list using the email's List-Unsubscribe header.
    Args:
        email_id (str): The ID of the email to unsubscribe from.
        account (str): The account to use. Valid options: 'personal' (default), 'work'.
    """
    sender = "Unknown"
    try:
        service = manager.get_service(account)
        msg = service.users().messages().get(
            userId="me",
            id=email_id,
            format="metadata",
            metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post", "From"],
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "Unknown")
        unsub_header = headers.get("list-unsubscribe")
        one_click = "one-click" in headers.get("list-unsubscribe-post", "").lower()

        if not unsub_header:
            return "No unsubscribe option found in this email."

        mailto_match = re.search(r"<(mailto:[^>]+)>", unsub_header, re.I)
        http_match = re.search(r"<(https?://[^>]+)>", unsub_header, re.I)
        http_url = http_match.group(1) if http_match else None

        if http_url and one_click and manager.is_safe_url(http_url):
            try:
                res = requests.post(http_url, data={"List-Unsubscribe": "One-Click"}, timeout=10, allow_redirects=False)
                if 200 <= res.status_code < 300:
                    return f"Sent a one-click unsubscribe request to {sender} (HTTP {res.status_code})."
            except requests.RequestException:
                pass 

        if mailto_match:
            parsed = urlparse(mailto_match.group(1))
            to_email = unquote(parsed.path)
            params = parse_qs(parsed.query)

            email_msg = EmailMessage()
            email_msg.set_content(params.get("body", ["Unsubscribe"])[0])
            email_msg["To"] = to_email
            email_msg["Subject"] = params.get("subject", ["Unsubscribe"])[0]

            encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": encoded_message}).execute()
            return f"Sent an unsubscribe email to {to_email} for {sender}."

        if http_url:
            return f"{sender} has no one-click unsubscribe. Open this link to finish unsubscribing: {http_url}"

        return "Found an unsubscribe header, but it was not in a usable format."
    except Exception as e:
        return f"An error occurred unsubscribing from {sender}: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)