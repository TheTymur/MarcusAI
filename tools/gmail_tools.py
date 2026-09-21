import os.path
import base64
from email.message import EmailMessage
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from agent.data.database_manager import Emailprocessing

SCOPE = ["https://mail.google.com/"]

class GmailClient:
    def __init__(self):
        self.creds = None
        self.service = None
        self.db_manager = Emailprocessing()
        self.authenticate()

    def extract_body(self, part):
                found_text = ""
                found_html = ""
                if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                    found_text = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                elif part.get('mimeType') == 'text/html' and 'data' in part.get('body', {}):
                    found_html = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                
                if 'parts' in part:
                    for subpart in part['parts']:
                        sub_text, sub_html = self.extract_body(subpart)
                        if sub_text: found_text += sub_text
                        if sub_html: found_html += sub_html
                        
                return found_text, found_html


    def authenticate(self):
        if os.path.exists("token.json"):
            self.creds = Credentials.from_authorized_user_file("token.json", SCOPE)
        
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPE)
                self.creds = flow.run_local_server(port=0)

            with open("token.json", "w") as token:
                token.write(self.creds.to_json())

        self.service = build("gmail", "v1", credentials=self.creds)

    def _parse_and_cache_email(self, msg_data):
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
            elif name == 'date': date = header['value']
            elif name == 'message-id': message_id = header['value']
            elif name == 'in-reply-to': in_reply_to = header['value']
            elif name == 'references': references = header['value']

        plain_text, html_text = self.extract_body(payload)
        
        body = "No Body available"
        if html_text:
            soup = BeautifulSoup(html_text, "html.parser")
            body = soup.get_text(separator="\n", strip=True)
        elif plain_text:
            body = plain_text
        else:
            body = msg_data.get("snippet", "No Body available")
            
        # Only process embeddings if the SQL insert was successful (new email)
        gmail_id = msg_data['id']
        is_new = self.db_manager.store_email(message_id, gmail_id, sender, recipient, subject, date, body)
        if is_new:
            self.db_manager.process_email(body, message_id, gmail_id, sender, subject, date)

        return {
            "sender": sender, "recipient": recipient, "cc": cc, 
            "subject": subject, "date": date, "message_id": message_id,
            "in_reply_to": in_reply_to, "references": references,
            "reply_to": reply_to, "body": body
        }


    def search_local_database(self, sender: str=None, subject: str=None):
        """Search the local SQLite database for emails matching specific criteria (sender/subject)."""

        return self.db_manager.search_email_metadata(sender, subject)
            

    def read_recent_emails(self, limit: int = 5) -> str:
        """Reads the most recent emails from the user's Gmail inbox. 
        IMPORTANT: This returns a list of emails. Each email has an 'ID' field. You MUST save and use this exact 'ID' if you need to read the full content, reply, forward, or delete the email later."""
        try:
            results = self.service.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=limit).execute()
            messages = results.get("messages", [])

            if not messages:
                return "No new emails found."
        
            email_data = []
            for msg in messages:
                txt = self.service.users().messages().get(userId="me", id=msg["id"]).execute()
                parsed = self._parse_and_cache_email(txt)

                snippet = txt.get("snippet", "No preview available")
                email_data.append(f"- ID: {msg['id']} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | From: {parsed['sender']} | To: {parsed['recipient']} | Subject: {parsed['subject']} | Snippet: {snippet}")
        
            return "\n".join(email_data)

        except Exception as error:
            return f"An error occurred connecting to Gmail: {error}"

    def create_label(self, label_name: str) -> str:
        """Creates a new label in the user's Gmail account."""
        try:
            label = self.service.users().labels().create(userId="me", body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show"
            }).execute()
            return f"Label '{label_name}' created successfully and its ID is {label['id']}."
        except Exception as error:
            return f"An error occurred creating the label: {error}"

    def apply_label(self, email_id: str, label_id: str) -> str:
        """Applies a label to a message in the user's Gmail account."""
        try:
            self.service.users().messages().modify(userId="me", id=email_id, body={
                "addLabelIds": [label_id]
            }).execute()
            return f"Label '{label_id}' applied to message '{email_id}' successfully."
        except Exception as error:
            return f"An error occurred applying the label: {error}" 

    def remove_label(self, email_id: str, label_id: str) -> str:
        """Removes a label from a message in the user's Gmail account.
        Also you can use this tool to mark email as read by setting the label_id to "UNREAD".
        """
        try:
            self.service.users().messages().modify(userId="me", id=email_id, body={
                "removeLabelIds": [label_id]
            }).execute()
            return f"Label '{label_id}' removed from message '{email_id}' successfully."
        except Exception as error:
            return f"An error occurred removing the label: {error}" 

    def delete_label(self, label_id: str) -> str:
        """Deletes a label from the user's Gmail account. """
        try:
            self.service.users().labels().delete(userId="me", id=label_id).execute()
            return f"Label '{label_id}' deleted successfully."
        except Exception as error:
            return f"An error occurred deleting the label: {error}"

    def list_labels(self) -> str:
        """Lists all labels in the user's Gmail account."""
        try:
            results = self.service.users().labels().list(userId="me").execute()
            labels = results.get("labels", [])

            if not labels:
                return "No labels found."
        
            label_data = []
            for label in labels:
                label_data.append(f"- ID: {label['id']} | Name: {label['name']}")
        
            return "\n".join(label_data)
        except Exception as error:
            return f"An error occurred listing the labels: {error}"

    def count_messages_in_label(self, label_id: str) -> str:
        """Counts the number of messages in a specific label in the user's Gmail account."""
        try:
            results = self.service.users().messages().list(userId="me", labelIds=[label_id]).execute()
            message_count = results.get("resultSizeEstimate", 0)
            return f"The label '{label_id}' has {message_count} message(s)."
        except Exception as error:
            return f"An error occurred counting messages in label '{label_id}': {error}"

    def search_emails(self, query: str) -> str:
        """
        Searches for emails in the user's Gmail account based on a query. 
        IMPORTANT: This returns a list of emails. Each email has an 'ID' field. You MUST save and use this exact 'ID' if you need to read the full content, reply, forward, or delete the email later.
        """
        try:
            results = self.service.users().messages().list(userId="me", q=query, maxResults=5).execute()
            messages = results.get("messages", [])

            if not messages:
                return "No emails found matching that query."
        
            email_data = []
            for msg in messages:
                txt = self.service.users().messages().get(userId="me", id=msg["id"]).execute()
                parsed = self._parse_and_cache_email(txt)

                snippet = txt.get("snippet", "No preview available")
                email_data.append(f"- ID: {msg['id']} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | From: {parsed['sender']} | To: {parsed['recipient']} | Subject: {parsed['subject']} | Snippet: {snippet}")
        
            return "\n".join(email_data)

        except Exception as error:
            return f"An error occurred searching for emails: {error}"

    def read_email_content(self, email_id: str) -> str:
        """Reads the full content of a specific email in the user's Gmail account.
        You MUST provide the 'email_id' parameter. You can find the email_id by first calling the 'read_recent_emails' or 'search_emails' tools and looking for the 'ID:' field in the results."""
        try:
            txt = self.service.users().messages().get(userId="me", id=email_id).execute()
            parsed = self._parse_and_cache_email(txt)
            
            body = parsed['body']
            if len(body) > 2000:
                body = body[:2000] + "\n\n...[EMAIL BODY TRUNCATED TO SAVE TOKENS]..."
                
            cc_string = f" | Cc: {parsed['cc']}" if parsed['cc'] else ""
            return f"- ID: {email_id} | Message-ID: {parsed['message_id']} | Date: {parsed['date']} | From: {parsed['sender']} | To: {parsed['recipient']}{cc_string} | Reply-to: {parsed['reply_to']} | In-Reply-To: {parsed['in_reply_to']} | References: {parsed['references']} | Subject: {parsed['subject']} | Body:\n{body}"
            
        except Exception as error:
            return f"An error occurred reading the email content: {error}"

    def delete_message(self, email_id: str) -> str:
        """
        Deletes an email or moves it to the trash. 
        If the user asks to 'trash', 'remove', or 'delete' an email, use this tool. 
        You MUST provide the 'email_id'. Look in the previous tool responses for the exact ID of the email you are trying to delete.
        CRITICAL: NEVER guess or hallucinate the ID. If you don't know the exact ID, you MUST call 'read_recent_emails' or 'search_emails' FIRST and wait for the results. Do NOT call this tool and a search tool in the same turn.
        """
        try:
            msg = self.service.users().messages().get(userId="me", id=email_id, format="metadata").execute()
            if 'TRASH' in msg.get('labelIds', []):
                self.service.users().messages().delete(userId="me", id=email_id).execute()
                self.db_manager.delete_email(email_id)
                return f"Message '{email_id}' permanently deleted from trash successfully."
            else:
                self.service.users().messages().trash(userId="me", id=email_id).execute()
                self.db_manager.delete_email(email_id)
                return f"Message '{email_id}' moved to trash successfully."
        except Exception as error:
            return f"An error occurred deleting the message: {error}"

    def delete_draft(self, draft_id: str) -> str:
        """Deletes a draft from the user's Gmail account."""
        try:
            self.service.users().drafts().delete(userId="me", id=draft_id).execute()
            return f"Draft '{draft_id}' deleted successfully."
        except Exception as error:
            return f"An error occurred deleting the draft: {error}"

    def list_drafts(self) -> str:
        """Lists all drafts and its details (To, Subject, Body) in the user's Gmail account."""
        try:
            results = self.service.users().drafts().list(userId="me").execute()
            drafts = results.get("drafts", [])

            if not drafts:
                return "No drafts found."
        
            draft_data = []
            for draft in drafts:
                txt = self.service.users().drafts().get(userId="me", id=draft["id"]).execute()
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
                    elif name == 'date': date = header['value']
                
                snippet = message.get("snippet", "No Body available")
                draft_data.append(f"- ID: {draft['id']} | Date: {date} | To: {to} | Subject: {subject} | Body: {snippet}")
        
            return "\n".join(draft_data)

        except Exception as error:
            return f"An error occurred listing the drafts: {error}"

    def create_draft(self, to: str, subject: str, message: str) -> str:
        """Creates a draft with To, Subject and Message in the user's Gmail account."""
        try:
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
            draft = self.service.users().drafts().create(userId="me", body=draft).execute()
            return f"Draft '{draft['id']}' created successfully."
        except Exception as error:
            return f"An error occurred creating the draft: {error}"

    def modify_draft(self, draft_id: str, to: str, subject: str, message: str) -> str:
        """Modifies a draft in the user's Gmail account."""
        try:
            existing_draft = self.service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
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

            draft = self.service.users().drafts().update(userId="me", id=draft_id, body=draft).execute()
            return f"Draft '{draft['id']}' modified successfully."
        except Exception as error:
            return f"An error occurred modifying the draft: {error}"

    def send_draft(self, draft_id: str) -> str:
        """Sends an existing draft email. 
        CRITICAL: MUST ONLY BE USED AFTER EXPLICIT USER APPROVAL of the draft content.
        CRITICAL: You MUST NOT call this tool in the same turn as 'create_draft', 'create_response_draft', or 'forward_email'. You must create the draft first, show it to the user, and wait for their approval in the next turn before sending.
        """
        try:
            draft_body = {'id': draft_id}
            draft = self.service.users().drafts().send(userId="me", body=draft_body).execute()
            return f"Draft '{draft['id']}' sent successfully."
        except Exception as error:
            return f"An error occurred sending the draft: {error}"

    def create_response_draft(self, to: str, subject: str, message: str, in_reply_to: str) -> str:
        """Creates a draft reply to an email using the in_reply_to field to thread it correctly. Use if user wants to reply to an email. Not to confuse with create_draft function.
        
        Args:
            to (str): The recipient of the email.
            subject (str): The subject of the email.
            message (str): The body of the email.
            in_reply_to (str): The ID of the email to reply to.
        """
        try:
            original_msg = self.service.users().messages().get(userId="me", id=in_reply_to, format="metadata").execute()
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
            draft = self.service.users().drafts().create(userId="me", body=draft).execute()
            return f"Draft '{draft['id']}' created successfully."
        except Exception as error:
            return f"An error occurred creating the draft: {error}"


    def forward_email(self, to: str, subject: str, message: str, in_reply_to: str) -> str:
        """Forwards an email using the in_reply_to field to thread it correctly. Use if user wants to forward an email.
        
        Args:
            to (str): The recipient of the email.
            subject (str): The subject of the email.
            message (str): The body of the email.
            in_reply_to (str): The ID of the email to forward.
        """
        try:
            original_msg = self.service.users().messages().get(userId="me", id=in_reply_to, format="full").execute()
            thread_id = original_msg.get('threadId')
            
            headers = original_msg.get('payload', {}).get('headers', [])
            message_id = ""
            references = ""
            payload = original_msg.get('payload', {}) 
            original_body = ""

            if "parts" in payload:
                for part in payload["parts"]:
                    if part["mimeType"] == "text/plain":
                        data = part["body"].get("data")
                        if data:
                            original_body = base64.urlsafe_b64decode(data).decode()
                            break
            elif "body" in payload and "data" in payload["body"]:
                original_body = base64.urlsafe_b64decode(payload["body"]["data"]).decode()
            
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
            draft = self.service.users().drafts().create(userId="me", body=draft).execute()
            return f"Draft '{draft['id']}' created successfully."
        except Exception as error:
            return f"An error occurred creating the draft: {error}"