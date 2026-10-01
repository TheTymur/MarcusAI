from pydantic import Field
import os.path
import socket
import ipaddress
from urllib.parse import urlparse, unquote, parse_qs
import base64
from email.message import EmailMessage
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from agent.data.database_manager import Emailprocessing
import re, requests, json
from typing import Literal

SCOPE = ["https://mail.google.com/",
        "https://www.googleapis.com/auth/gmail.settings.basic"]

work_creds = "work"
personal_creds = "personal"
uni_creds = ""

class GmailClient:
    def __init__(self):
        self.creds = None
        self.service = {}
        self.db_manager = Emailprocessing()
        for account in [personal_creds, work_creds]:
            self.authenticate(account)

    def extract_body(self, part):
        found_text = ""
        found_html = ""
        attachments = []
        
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


    def authenticate(self, account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")):
        creds = None
        if os.path.exists(f"token_{account}"):
            creds = Credentials.from_authorized_user_file(f"token_{account}", SCOPE)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPE)
                creds = flow.run_local_server(port=0)

            with open(f"token_{account}", "w") as token:
                token.write(creds.to_json())

        self.service[account] = build("gmail", "v1", credentials=creds)


    def _parse_and_cache_email(self, msg_data, account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")):
        """Helper to parse headers/body and save to SQLite + Vector DB if new."""
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
                import email.utils
                try:
                    dt = email.utils.parsedate_to_datetime(header['value'])
                    date = dt.astimezone().strftime('%Y-%m-%d %H:%M')
                except Exception:
                    date = header['value']
            elif name == 'message-id': message_id = header['value']
            elif name == 'in-reply-to': in_reply_to = header['value']
            elif name == 'references': references = header['value']

        plain_text, html_text, attachments = self.extract_body(payload)
        
        body = "No Body available"
        if html_text:
            soup = BeautifulSoup(html_text, "html.parser")
            body = soup.get_text(separator="\n", strip=True)
        elif plain_text:
            body = plain_text
        else:
            body = msg_data.get("snippet", "No Body available")
            
        gmail_id = msg_data['id']
        attachments_json = json.dumps(attachments) if attachments else None
        is_new = self.db_manager.store_email(message_id, gmail_id, sender, recipient, subject, date, body, account, attachments=attachments_json)
        if is_new:
            self.db_manager.process_email(body, message_id, gmail_id, sender, subject, date, account)

        labels = msg_data.get("labelIds", [])

        return {
            "sender": sender, "recipient": recipient, "cc": cc, 
            "subject": subject, "date": date, "message_id": message_id,
            "in_reply_to": in_reply_to, "references": references,
            "reply_to": reply_to, "body": body, "labels": labels, "attachments": attachments
        }

    def target_account(self, account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")):
        try:
            target_service = self.service.get(account)
            if target_service is None:
                raise ValueError(f"Account '{account}' not found. Valid accounts are 'personal' and 'work'.")
            return target_service
        except ValueError as error:
            raise error
       

    def search_local_database(self, sender: str = Field(None, description="The sender email address to filter by. Uses partial matching. Optional."), subject: str = Field(None, description="The subject to filter by. Uses partial matching. Optional."), account: str = Field(personal_creds, description="The account to use. Valid options: 'personal' (default), 'work'.")) -> str:
        """Search the local SQLite database for emails matching specific criteria.
        Both `sender` and `subject` use partial matching (case-insensitive substring match). 
        If both `sender` and `subject` are provided, they are combined with a logical AND.
        They are both optional.
        CRITICAL: Do NOT use this tool if the user asks you to "read recent emails", "check my inbox", or "read my mail". For checking live/new emails, you MUST use 'read_recent_emails' instead. Only use this database tool when specifically searching for past historical records.
        VALID ACCOUNTS: 'personal' (default), 'work'."""

        return self.db_manager.search_email_metadata(sender, subject)
            
    def read_local_email(self, email_id: str, account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")):
        """Read the full body of an email from the local SQLite database using its email_id.
        VALID ACCOUNTS: 'personal' (default), 'work'."""
        return self.db_manager.read_local_email(email_id)

    def read_recent_emails(self, limit: int = Field(5, description='The maximum number of emails to return.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Reads the most recent emails from the user's Gmail inbox. 
        IMPORTANT: This returns a list of emails. Each email has an 'ID' field. You MUST save and use this exact 'ID' if you need to read the full content, reply, forward, or delete the email later.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            results = target_service.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=limit).execute()
            messages = results.get("messages", [])

            if not messages:
                return "No new emails found."
        
            email_data = []
            for msg in messages:
                txt = target_service.users().messages().get(userId="me", id=msg["id"], format = "metadata", metadataHeaders=["From", "To", "Subject", "Date", "Message-ID"]).execute()
                parsed = self._parse_and_cache_email(txt, account)

                snippet = txt.get("snippet", "No preview available")
                labels_str = ", ".join(parsed['labels']) if parsed['labels'] else "None"
                is_read = "UNREAD" not in parsed['labels']
                email_data.append(f"- ID: {msg['id']} | Is Read: {is_read} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | Labels: [{labels_str}] | From: {parsed['sender']} | To: {parsed['recipient']} | Subject: <email_subject>{parsed['subject']}</email_subject> | Snippet: <email_snippet>{snippet}</email_snippet>")
        
            return "\n".join(email_data)

        except Exception as error:
            return f"An error occurred connecting to Gmail: {error}"

    def create_label(
        self,
        label_name: str = Field(description='The name of the label to create.'),
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] = Field(
            "labelShow", 
            description="Visibility of the label in the label list. labelShow: Show the label. labelShowIfUnread: Show if there are any unread messages with that label. labelHide: Do not show."
        ),
        account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")
    ) -> str:
        """
        Creates a new, empty label in the user's Gmail account.

        This only creates the label itself. To actually apply this label to an email, you must subsequently call 'apply_label'.
        If a label with the exact same name already exists, this operation will fail.

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            label = target_service.users().labels().create(userId="me", body={
                "name": label_name,
                "labelListVisibility": label_list_visibility,
                "messageListVisibility": "show"
            }).execute()
            return f"Label '{label_name}' created successfully and its ID is {label['id']}."
        except Exception as error:
            return f"An error occurred creating the label: {error}"

    def apply_label(self, email_id: str = Field(description='The ID of the email.'), label_id: str = Field(description='The ID of the label to apply.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Applies an existing label to a specific email message in the user's Gmail account.
        This is a mutating operation that modifies the email's metadata by attaching the specified label.

        Prerequisites: The label MUST already exist. If you need to create a new label, use 'create_label' first.
        If you want to remove a label from an email, use 'remove_label' instead.

        This tool is idempotent; applying a label that is already attached to the email will succeed with no additional side effects.

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            target_service.users().messages().modify(userId="me", id=email_id, body={
                "addLabelIds": [label_id]
            }).execute()
            return f"Label '{label_id}' applied to message '{email_id}' successfully."
        except Exception as error:
            return f"An error occurred applying the label: {error}" 

    def remove_label(self, email_id: str = Field(description='The ID of the email.'), label_id: str = Field(description='The ID of the label to remove.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Removes a label from a specific email message (untags the email).

        Also you can use this tool to mark an email as read by setting the label_id to "UNREAD".

        When to use vs alternatives:
        - Use this tool when you want to keep the label in the account, but remove it from a specific email.
        - Do NOT use this tool if you want to permanently destroy the label from the entire account (use 'delete_label' instead).
        - Do NOT use this tool if you want to delete the email message itself (use 'delete_message' instead).

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            target_service.users().messages().modify(userId="me", id=email_id, body={
                "removeLabelIds": [label_id]
            }).execute()
            return f"Label '{label_id}' removed from message '{email_id}' successfully."
        except Exception as error:
            return f"An error occurred removing the label: {error}" 

    def delete_label(self, label_id: str = Field(description='The ID of the label to delete.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Permanently deletes a label from the user's Gmail account entirely.

        Prerequisites: The label MUST exist in the user's account. Use 'list_labels' first to verify the label_id.

        When to use vs alternatives:
        - Use this tool ONLY when you want to permanently destroy the label itself from the entire Gmail account.
        - If you only want to remove a label from a specific email (untag the email), use 'remove_label' instead.
        - If you want to delete an email message entirely, use 'delete_message' instead.

        Consequences: This is a destructive operation. The label will be permanently deleted and removed from all messages it was attached to. The messages themselves will NOT be deleted. This cannot be undone.

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            target_service.users().labels().delete(userId="me", id=label_id).execute()
            return f"Label '{label_id}' deleted successfully."
        except Exception as error:
            return f"An error occurred deleting the label: {error}"

    def update_label(
        self,
        label_id: str = Field(description='The ID of the label to update.'),
        label_name: str = Field(default=None, description='The new name of the label (optional).'),
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] = Field(
            default=None, 
            description="Visibility of the label in the label list. labelShow: Show the label. labelShowIfUnread: Show if there are any unread messages with that label. labelHide: Do not show."
        ),
        message_list_visibility: Literal["show", "hide"] = Field(
            default=None,
            description="Visibility of messages with this label in the message list."
        ),
        text_color: str = Field(default=None, description="The text color of the label as a hex string (e.g. '#000000'). Must be a color supported by the Gmail API."),
        background_color: str = Field(default=None, description="The background color of the label as a hex string (e.g. '#ffffff'). Must be a color supported by the Gmail API."),
        account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")
    ) -> str:
        """
        Updates the properties of an existing label in the user's Gmail account.
        Only the properties provided will be updated (patch semantics).

        Prerequisites: The label MUST exist in the user's account. Use 'list_labels' first to verify the label_id.

        When to use vs alternatives:
        - Use this tool when you want to rename a label, change its visibility, or update its colors.
        - If you want to apply a label to an email, use 'apply_label' instead.
        - If you want to create a new label from scratch, use 'create_label' instead.

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
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

            target_service.users().labels().patch(userId="me", id=label_id, body=body).execute()
            return f"Label '{label_id}' updated successfully."
        except Exception as error:
            return f"An error occurred updating the label: {error}"

    def list_labels(self, account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Lists all labels in the user's Gmail account.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            results = target_service.users().labels().list(userId="me").execute()
            labels = results.get("labels", [])

            if not labels:
                return "No labels found."
        
            label_data = []
            for label in labels:
                label_data.append(f"- ID: {label['id']} | Name: {label['name']}")
        
            return "\n".join(label_data)
        except Exception as error:
            return f"An error occurred listing the labels: {error}"

    def count_messages_in_label(self, label_id: str = Field(description='The ID of the label to count messages for.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Counts the total number of messages and the number of unread messages in a specific label in the user's Gmail account.

        Prerequisites: The label MUST exist in the user's account. Use 'list_labels' first to verify the label_id if you are unsure.

        When to use vs alternatives:
        - Use this tool when you only need to know how many messages (total and unread) have a specific label.
        - Do NOT use this tool if you need to read the actual content or see the metadata of the messages (use 'search_emails' or 'read_recent_emails' instead).
        - Do NOT use this tool to list all available labels (use 'list_labels' instead).

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            label = target_service.users().labels().get(userId="me", id=label_id).execute()
            message_count = label.get("messagesTotal", 0)
            unread_count = label.get("messagesUnread", 0)
            return f"The label '{label_id}' has {message_count} total message(s), {unread_count} of which are unread."
        except Exception as error:
            return f"An error occurred counting messages in label '{label_id}': {error}"

    def search_emails(self, query: str = Field(description='The search query string (like in Gmail search bar).'), limit: int = Field(5, description='The maximum number of emails to return.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Searches for emails in the user's Gmail account based on a query. 
        IMPORTANT: This returns a list of emails. Each email has an 'ID' field. You MUST save and use this exact 'ID' if you need to read the full content, reply, forward, or delete the email later.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            results = target_service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
            messages = results.get("messages", [])

            if not messages:
                return "No emails found matching that query."
        
            email_data = []
            for msg in messages:
                txt = target_service.users().messages().get(userId="me", id=msg["id"], format = "metadata", metadataHeaders=["From", "To", "Subject", "Date", "Message-ID"]).execute()
                parsed = self._parse_and_cache_email(txt, account)

                snippet = txt.get("snippet", "No preview available")
                labels_str = ", ".join(parsed['labels']) if parsed['labels'] else "None"
                is_read = "UNREAD" not in parsed['labels']
                email_data.append(f"- ID: {msg['id']} | Is Read: {is_read} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | Labels: [{labels_str}] | From: {parsed['sender']} | To: {parsed['recipient']} | Subject: <email_subject>{parsed['subject']}</email_subject> | Snippet: <email_snippet>{snippet}</email_snippet>")
        
            return "\n".join(email_data)

        except Exception as error:
            return f"An error occurred searching for emails: {error}"

    def read_email_content(self, email_id: str = Field(description='The ID of the email to read.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Reads the full content of a specific email in the user's Gmail account.
        You MUST provide the 'email_id' parameter. You can find the email_id by first calling the 'read_recent_emails' or 'search_emails' tools and looking for the 'ID:' field in the results.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            txt = target_service.users().messages().get(userId="me", id=email_id).execute()
            parsed = self._parse_and_cache_email(txt, account)
            
            if "UNREAD" in parsed['labels']:
                try:
                    target_service.users().messages().modify(userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}).execute()
                    parsed['labels'].remove("UNREAD")
                except Exception:
                    pass

            body = parsed['body']
            if len(body) > 2000:
                body = body[:2000] + "\n\n...[EMAIL BODY TRUNCATED TO SAVE TOKENS]..."
                
            cc_string = f" | Cc: {parsed['cc']}" if parsed['cc'] else ""
            labels_str = ", ".join(parsed['labels']) if parsed['labels'] else "None"
            attachments_str = ""
            if parsed.get('attachments'):
                att_info = [f"{a['filename']} (ID: {a['attachmentId']})" for a in parsed['attachments']]
                attachments_str = f" | Attachments: {', '.join(att_info)}"
            return f"- ID: {email_id} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | Labels: [{labels_str}] | From: {parsed['sender']} | To: {parsed['recipient']}{cc_string} | Reply-to: {parsed['reply_to']} | In-Reply-To: {parsed['in_reply_to']} | References: {parsed['references']}{attachments_str} | Subject: <email_subject>{parsed['subject']}</email_subject> | Body:\n<email_body>\n{body}\n</email_body>"
            
        except Exception as error:
            return f"An error occurred reading the email content: {error}"

    def download_attachment(self, email_id: str = Field(description='The ID of the email containing the attachment.'), attachment_id: str = Field(description='The ID of the attachment to download.'), filename: str = Field(description='The name to save the attachment as.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Downloads an attachment from an email and saves it to the macOS Downloads folder.
        You MUST provide the 'email_id' and 'attachment_id'. You can find these by first calling 'read_email_content'.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            attachment = target_service.users().messages().attachments().get(userId='me', messageId=email_id, id=attachment_id).execute()
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

    def delete_message(self, email_id: str = Field(description='The ID of the email to delete.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Deletes an email or moves it to the trash. 
        If the user asks to 'trash', 'remove', or 'delete' an email, use this tool. 
        You MUST provide the 'email_id'. Look in the previous tool responses for the exact ID of the email you are trying to delete.
        CRITICAL: NEVER guess or hallucinate the ID. If you don't know the exact ID, you MUST call 'read_recent_emails' or 'search_emails' FIRST and wait for the results. Do NOT call this tool and a search tool in the same turn.
        Note: To undo a deletion, use the `untrash_message` tool.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            msg = target_service.users().messages().get(userId="me", id=email_id, format="metadata").execute()
            if 'TRASH' in msg.get('labelIds', []):
                target_service.users().messages().delete(userId="me", id=email_id).execute()
                self.db_manager.delete_email(email_id)
                return f"Message '{email_id}' permanently deleted from trash successfully."
            else:
                target_service.users().messages().trash(userId="me", id=email_id).execute()
                self.db_manager.delete_email(email_id)
                return f"Message '{email_id}' moved to trash successfully."
        except Exception as error:
            return f"An error occurred deleting the message: {error}"

    def untrash_message(self, email_id: str = Field(description='The ID of the email to untrash.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Removes an email from the trash and restores it to the inbox.
        Use this tool if the user asks to undo a deletion, restore a deleted email, or move an email out of the trash.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            target_service.users().messages().untrash(userId="me", id=email_id).execute()
            return f"Message '{email_id}' restored from trash successfully."
        except Exception as error:
            return f"An error occurred untrashing the message: {error}"

    def delete_draft(self, draft_id: str = Field(description='The ID of the draft to delete.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Permanently deletes a draft from the user's Gmail account.

        Prerequisites: The draft MUST already exist. You should find the draft_id by using 'list_drafts' first.

        When to use: Use this ONLY when the user explicitly asks to cancel, discard, or delete an unsent draft.
        When NOT to use: Do NOT use this to delete regular sent or received emails (use 'delete_message' instead). Do NOT use this to modify a draft's content (use 'modify_draft' instead).

        Warning: This is a highly destructive operation. Draft deletions cannot be undone (unlike deleting a regular message which goes to trash).

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            target_service.users().drafts().delete(userId="me", id=draft_id).execute()
            return f"Draft '{draft_id}' deleted successfully."
        except Exception as error:
            return f"An error occurred deleting the draft: {error}"

    def list_drafts(self, account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Lists all drafts and its details (To, Subject, Body) in the user's Gmail account.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            results = target_service.users().drafts().list(userId="me").execute()
            drafts = results.get("drafts", [])

            if not drafts:
                return "No drafts found."
        
            draft_data = []
            for draft in drafts:
                txt = target_service.users().drafts().get(userId="me", id=draft["id"]).execute()
                message = txt.get("message", {})
                payload = message.get("payload", {})
                headers = payload.get("headers", [])
                
                to = "No To available"
                subject = "No Subject available"
                date = "Unknown"
                
                for header in headers:
                    name = header['name'].lower()
                    if name == 'to': to = header['value']
                    elif name == 'subject': subject = header['value']
                    elif name == 'date': 
                        import email.utils
                        try:
                            dt = email.utils.parsedate_to_datetime(header['value'])
                            date = dt.astimezone().strftime('%Y-%m-%d %H:%M')
                        except Exception:
                            date = header['value']
                
                snippet = message.get("snippet", "No Body available")
                draft_data.append(f"- ID: {draft['id']} | Date: {date} | To: {to} | Subject: <email_subject>{subject}</email_subject> | Body: <email_body>{snippet}</email_body>")
        
            return "\n".join(draft_data)

        except Exception as error:
            return f"An error occurred listing the drafts: {error}"

    def create_draft(self, to: str = Field(description='The recipient email address.'), subject: str = Field(description='The subject of the email.'), message: str = Field(description='The body of the email.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Creates a standalone draft email in the user's Gmail account.

        When to use vs alternatives:
        - Use `create_draft` ONLY when starting a brand new email thread.
        - If you are replying to an existing email, MUST use `create_response_draft` instead (to maintain threading).
        - If you are forwarding an existing email, MUST use `forward_email` instead.
        
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            email_msg = EmailMessage()
            email_msg.set_content(message)
            email_msg['To'] = to
            email_msg['Subject'] = subject

            encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()

            draft = {
                'message': {
                    'raw': encoded_message
                }
            }
            draft = target_service.users().drafts().create(userId="me", body=draft).execute()
            return f"Draft '{draft['id']}' created successfully."
        except Exception as error:
            return f"An error occurred creating the draft: {error}"

    def modify_draft(self, draft_id: str = Field(description='The ID of the draft to modify.'), to: str = Field(description='The recipient email address.'), subject: str = Field(description='The subject of the email.'), message: str = Field(description='The body of the email.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Modifies a draft in the user's Gmail account.
        
        Behavior & Side Effects:
        - This operation completely replaces the draft's To, Subject, and Message body with the newly provided values.
        - It is a mutating but reversible operation (you can call this again to change the values back).
        - The `account` parameter determines which inbox's draft is modified.
        
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            existing_draft = target_service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
            existing_message = existing_draft.get('message', {})
            thread_id = existing_message.get('threadId')
            
            headers = existing_message.get('payload', {}).get('headers', [])
            in_reply_to = ""
            references = ""
            
            for header in headers:
                if header['name'].lower() == 'in-reply-to':
                    in_reply_to = header['value']
                if header['name'].lower() == 'references':
                    references = header['value']

            email_msg = EmailMessage()
            email_msg.set_content(message)
            email_msg['To'] = to
            email_msg['Subject'] = subject
            if in_reply_to:
                email_msg['In-Reply-To'] = in_reply_to
            if references:
                email_msg['References'] = references

            encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()

            draft = {
                'message': {
                    'raw': encoded_message
                }
            }
            if thread_id:
                draft['message']['threadId'] = thread_id

            draft = target_service.users().drafts().update(userId="me", id=draft_id, body=draft).execute()
            return f"Draft '{draft['id']}' modified successfully."
        except Exception as error:
            return f"An error occurred modifying the draft: {error}"

    def send_draft(self, draft_id: str = Field(description='The ID of the draft to send.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Sends an existing draft email. 
        CRITICAL: MUST ONLY BE USED AFTER EXPLICIT USER APPROVAL of the draft content.
        CRITICAL: You MUST NOT call this tool in the same turn as 'create_draft', 'create_response_draft', or 'forward_email'. You must create the draft first, show it to the user, and wait for their approval in the next turn before sending.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            draft_body = {'id': draft_id}
            draft = target_service.users().drafts().send(userId="me", body=draft_body).execute()
            return f"Draft '{draft['id']}' sent successfully."
        except Exception as error:
            return f"An error occurred sending the draft: {error}"

    def create_response_draft(self, to: str = Field(description='The recipient email address.'), subject: str = Field(description='The subject of the email.'), message: str = Field(description='The body of the email.'), in_reply_to: str = Field(description='The Message-ID of the email being replied to.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Creates a draft reply to an existing email thread.

        When to use vs alternatives:
        - Use `create_response_draft` ONLY when replying to an existing email (requires `in_reply_to`).
        - If you are starting a brand new email thread, MUST use `create_draft` instead.
        - If you are forwarding an email to a third party, MUST use `forward_email` instead.
        
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            original_msg = target_service.users().messages().get(userId="me", id=in_reply_to, format="metadata").execute()
            thread_id = original_msg.get('threadId')
            
            headers = original_msg.get('payload', {}).get('headers', [])
            message_id = ""
            references = ""
            
            for header in headers:
                if header['name'].lower() == 'message-id':
                    message_id = header['value']
                if header['name'].lower() == 'references':
                    references = header['value']
            
            email_msg = EmailMessage()
            email_msg.set_content(message)
            email_msg['To'] = to
            
            if not subject.lower().startswith('re:'):
                subject = 'Re: ' + subject
            email_msg['Subject'] = subject
            
            if message_id:
                email_msg['In-Reply-To'] = message_id
                email_msg['References'] = (references + " " + message_id).strip() if references else message_id

            encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()

            draft = {
                'message': {
                    'raw': encoded_message,
                    'threadId': thread_id
                }
            }
            draft = target_service.users().drafts().create(userId="me", body=draft).execute()
            return f"Draft '{draft['id']}' created successfully."
        except Exception as error:
            return f"An error occurred creating the draft: {error}"


    def forward_email(self, to: str = Field(description='The recipient email address.'), subject: str = Field(description='The subject of the email.'), message: str = Field(description='The body of the email.'), in_reply_to: str = Field(description='The Message-ID of the email being forwarded.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Creates a draft forwarding an existing email to a new recipient.

        When to use vs alternatives:
        - Use `forward_email` ONLY when sending an existing email to a new third-party recipient (requires `in_reply_to`).
        - If you are replying back to the original sender, MUST use `create_response_draft` instead.
        - If you are starting a brand new email thread, MUST use `create_draft` instead.
        
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            original_msg = target_service.users().messages().get(userId="me", id=in_reply_to, format="full").execute()
            thread_id = original_msg.get('threadId')
            
            headers = original_msg.get('payload', {}).get('headers', [])
            _, original_body = self.extract_body(original_msg.get('payload', {}))
            combined_message = message + "\n\n" + "Original message:\n" + original_body

            for header in headers:
                if header['name'].lower() == 'message-id':
                    message_id = header['value']
                if header['name'].lower() == 'references':
                    references = header['value']
            
            email_msg = EmailMessage()
            email_msg.set_content(combined_message)
            email_msg['To'] = to
            
            if not subject.lower().startswith('fwd:'):
                subject = 'Fwd: ' + subject
            email_msg['Subject'] = subject
            
            if message_id:
                email_msg['In-Reply-To'] = message_id
                email_msg['References'] = (references + " " + message_id).strip() if references else message_id

            encoded_message = base64.urlsafe_b64encode(email_msg.as_bytes()).decode()

            draft = {
                'message': {
                    'raw': encoded_message,
                    'threadId': thread_id
                }
            }
            draft = target_service.users().drafts().create(userId="me", body=draft).execute()
            return f"Draft '{draft['id']}' created successfully."
        except Exception as error:
            return f"An error occurred creating the draft: {error}"


    def _is_safe_url(self, url: str):
        """Only allow https URLs that resolve to public IPs. Anti Server-Side Request Forgery (SSRF)."""
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

    def unsubscribe_from_email(self, email_id: str = Field(description='The ID of the email to unsubscribe from.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Unsubscribes the user from a mailing list using the email's List-Unsubscribe header.
        This sends an email or a web request on the user's behalf, so ONLY call it after the user
        explicitly asks to unsubscribe from that sender.
        You MUST provide the Gmail 'email_id' (the 'ID:' field from read_recent_emails or search_emails).
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        sender = "Unknown"
        try:
            target_service = self.target_account(account)
            msg = target_service.users().messages().get(
                userId="me",
                id=email_id,
                format="metadata",
                metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post", "From"],
            ).execute()

            headers = {
                h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            sender = headers.get("from", "Unknown")
            unsub_header = headers.get("list-unsubscribe")
            one_click = "one-click" in headers.get("list-unsubscribe-post", "").lower()

            if not unsub_header:
                return "No unsubscribe option found in this email."

            mailto_match = re.search(r"<(mailto:[^>]+)>", unsub_header, re.I)
            http_match = re.search(r"<(https?://[^>]+)>", unsub_header, re.I)
            http_url = http_match.group(1) if http_match else None

            if http_url and one_click and self._is_safe_url(http_url):
                try:
                    res = requests.post(
                        http_url,
                        data={"List-Unsubscribe": "One-Click"},
                        timeout=10,
                        allow_redirects=False,
                    )
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
                target_service.users().messages().send(
                    userId="me", body={"raw": encoded_message}
                ).execute()
                return f"Sent an unsubscribe email to {to_email} for {sender}."

            if http_url:
                return (
                    f"{sender} has no one-click unsubscribe. "
                    f"Open this link to finish unsubscribing: {http_url}"
                )

            return "Found an unsubscribe header, but it was not in a usable format."

        except Exception as e:
            return f"An error occurred unsubscribing from {sender}: {e}"

    def create_filter(self, 
                      from_sender: str = Field(None, description="Match emails from this sender."),
                      to_recipient: str = Field(None, description="Match emails sent to this address."),
                      subject: str = Field(None, description="Match emails with this subject."),
                      query: str = Field(None, description="Match emails matching this Gmail search query."),
                      add_label_ids: list[str] = Field(None, description="List of label IDs to add (e.g., ['TRASH', 'STARRED'])."),
                      remove_label_ids: list[str] = Field(None, description="List of label IDs to remove (e.g., ['INBOX', 'UNREAD'])."),
                      account: str = Field(personal_creds, description="The account to use. Valid options: 'personal' (default), 'work'.")) -> str:
        """
        Creates a new filter to automatically handle incoming emails based on specified criteria.
        At least one of 'from_sender', 'to_recipient', 'subject', or 'query' must be provided.

        Args:
            from_sender (str, optional): Match emails from this sender.
            to_recipient (str, optional): Match emails sent to this address.
            subject (str, optional): Match emails with this subject.
            query (str, optional): Raw Gmail search query.
            add_label_ids (list[str], optional): List of label IDs to add (e.g., ['TRASH', 'STARRED']).
            remove_label_ids (list[str], optional): List of label IDs to remove (e.g., ['INBOX', 'UNREAD']).
            account (str, optional): The account to use. Valid options: 'personal' (default), 'work'.

        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)

            if not any([from_sender, to_recipient, subject, query]):
                return "Error: You must provide at least one of 'from_sender', 'to_recipient', 'subject', or 'query'."

            criteria = {}
            if from_sender:
                criteria['from'] = from_sender
            if to_recipient:
                criteria['to'] = to_recipient
            if subject:
                criteria['subject'] = subject
            if query:
                criteria['query'] = query

            action = {}
            if add_label_ids:
                action['addLabelIds'] = add_label_ids
            if remove_label_ids:
                action['removeLabelIds'] = remove_label_ids

            filter_body = {
                "criteria": criteria,
                "action": action
            }

            result = target_service.users().settings().filters().create(
                userId='me',
                body=filter_body
            ).execute()

            return f"Successfully created filter with ID: {result['id']}"

        except Exception as e:
            return f"Error creating filter: {str(e)}"

    def block_sender(self, sender: str = Field(description='The email address to block.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Blocks an email sender by creating a filter to move all their emails to the trash.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        result = self.create_filter(from_sender=sender, add_label_ids=["TRASH"], remove_label_ids=["INBOX"], account=account)
        if "Successfully" in result:
            return f"Successfully blocked {sender}."
        return result

    def list_blocked_senders(self, account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> list[str] | str:
        """
        Lists all blocked senders in the user's Gmail account.
        CRITICAL: This tool ONLY lists blocked senders. It CANNOT create, modify, or forward drafts/emails. Do NOT confuse this tool with modify_draft, create_response_draft, or forward_email.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            results = target_service.users().settings().filters().list(userId="me").execute()
            filters = results.get('filter', [])
            
            blocked_senders = []
            for f in filters:
                criteria = f.get('criteria', {})
                action = f.get('action', {})
                from_email = criteria.get('from', '')
                
                # Check if this filter is actually a "block" filter
                if from_email and 'TRASH' in action.get('addLabelIds', []):
                    blocked_senders.append(from_email)
            
            if not blocked_senders:
                return "No block filters found in your account."
            
            return blocked_senders
        except Exception as e:
            return f"An error occurred listing blocked senders: {e}"


    def list_filters(self, account: str = Field(personal_creds, description="The account to use. Valid options: 'personal' (default), 'work'.")) -> str:
        """
        Lists all filters in the user's Gmail account, including their IDs, criteria, and actions.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            results = target_service.users().settings().filters().list(userId="me").execute()
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

    def delete_filter(self, filter_id: str = Field(description='The ID of the filter to delete.'), account: str = Field(personal_creds, description="The account to use. Valid options: 'personal' (default), 'work'.")) -> str:
        """
        Deletes a specific filter in the user's Gmail account by its ID.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            target_service.users().settings().filters().delete(userId="me", id=filter_id).execute()
            return f"Successfully deleted filter with ID: {filter_id}"
        except Exception as e:
            return f"An error occurred deleting the filter: {e}"

    def unblock_sender(self, sender: str = Field(description='The email address to unblock.'), account: str = Field(personal_creds, description="The account to use. Valid options: \'personal\' (default), \'work\'.")) -> str:
        """
        Unblocks an email sender by finding and removing their specific filter.
        VALID ACCOUNTS: 'personal' (default), 'work'.
        """
        try:
            target_service = self.target_account(account)
            results = target_service.users().settings().filters().list(userId="me").execute()
            filters = results.get('filter', [])
            
            filters_deleted = 0
            for f in filters:
                criteria = f.get('criteria', {})
                action = f.get('action', {})
                from_email = criteria.get('from', '')
                    
                if from_email and sender.lower() in from_email.lower() and 'TRASH' in action.get('addLabelIds', []):
                    filter_id = f['id']
                    self.delete_filter(filter_id=filter_id, account=account)
                    filters_deleted += 1
                        
            if filters_deleted > 0:
                return f"Successfully unblocked {sender} by removing {filters_deleted} filter(s)."
            else:
                return f"Could not find any active block filter for {sender}."
                    
        except Exception as e:
            return f"An error occurred unblocking {sender}: {e}"
            