import json
from datetime import datetime, timezone
from typing import Optional
from email.utils import parsedate_to_datetime

from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker
 
from agent.memory import get_memory

Base = declarative_base()
memory_client = get_memory()


class Emails(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String, unique=True, nullable=True, index=True)
    gmail_id = Column(String, unique=True, nullable=False, index=True)

    sender = Column(String)
    recipient = Column(String)
    subject = Column(String)
    date = Column(DateTime)
    account_name = Column(Text)

    body = Column(Text)
    attachments = Column(Text, nullable=True)

    ai_processed = Column(Boolean, default=False)
    reply_drafted = Column(Boolean, default=False)


class Emailprocessing:
    def __init__(self, db_path="./agent/data/agent_emails.db"):
        engine = create_engine(f'sqlite:///{db_path}', echo=False)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        self.session = SessionLocal()

    def search_email_metadata(
        self, 
        sender: Optional[str] = None, 
        subject: Optional[str] = None, 
        query_text: Optional[str] = None,
        has_attachments: Optional[bool] = None,
        after_date: Optional[str] = None,
        before_date: Optional[str] = None,
        limit: int = 15, 
        account: Optional[str] = None
    ) -> str:
        """
        Searches the local SQL database for emails by sender, subject, full-text query, date, or attachments.
        
        Args:
            sender (str, optional): A name or email address to search for in the 'From' field.
            subject (str, optional): Keywords to search for in the email subject.
            query_text (str, optional): Keywords to search across subject, body text, and sender.
            has_attachments (bool, optional): If True, only returns emails with attachments.
            after_date (str, optional): Filter emails on or after this date (YYYY-MM-DD).
            before_date (str, optional): Filter emails on or before this date (YYYY-MM-DD).
            limit (int, optional): Maximum number of results to return (default 15).
            account (str, optional): Account to filter by ('personal' or 'work').
        """
        try:
            query = self.session.query(Emails)
            
            if sender:
                query = query.filter(Emails.sender.ilike(f"%{sender}%"))
            if subject:
                query = query.filter(Emails.subject.ilike(f"%{subject}%"))
            if query_text:
                query = query.filter(
                    or_(
                        Emails.subject.ilike(f"%{query_text}%"),
                        Emails.body.ilike(f"%{query_text}%"),
                        Emails.sender.ilike(f"%{query_text}%"),
                    )
                )
            if has_attachments is True:
                query = query.filter(Emails.attachments.isnot(None), Emails.attachments != "[]", Emails.attachments != "")
            elif has_attachments is False:
                query = query.filter(or_(Emails.attachments.is_(None), Emails.attachments == "[]", Emails.attachments == ""))
            if after_date:
                parsed_after = self.parse_date(after_date)
                if parsed_after:
                    query = query.filter(Emails.date >= parsed_after)
            if before_date:
                parsed_before = self.parse_date(before_date)
                if parsed_before:
                    query = query.filter(Emails.date <= parsed_before)
            if account:
                query = query.filter(Emails.account_name.ilike(f"%{account}%"))
                
            results = query.order_by(Emails.date.desc().nullslast()).limit(limit).all()
            
            if not results:
                return "No emails found in the local database matching those criteria."
                
            formatted = []
            for email in results:
                date_str = email.date.strftime('%Y-%m-%d %H:%M') if email.date else "Unknown"
                att_info = ""
                if email.attachments:
                    try:
                        atts = json.loads(email.attachments)
                        if isinstance(atts, list) and atts:
                            names = [a.get("filename", "file") for a in atts if isinstance(a, dict)]
                            if names:
                                att_info = f" | Attachments: [{', '.join(names)}]"
                    except Exception:
                        att_info = f" | Attachments: {email.attachments[:50]}"
                
                snippet = ""
                if email.body:
                    clean_snip = " ".join(email.body.split())[:120]
                    if clean_snip:
                        snippet = f" | Snippet: {clean_snip}..."

                formatted.append(
                    f"- ID: {email.gmail_id} | Date: {date_str} | From: {email.sender} | To: {email.recipient} | Subject: <email_subject>{email.subject}</email_subject>{att_info}{snippet} | Account: {email.account_name}"
                )
            return "\n".join(formatted)
            
        except Exception as e:
            return f"Database metadata search error: {e}"

    def read_local_email(self, email_id: str) -> str:
        """
        Reads the full content of an email from the local SQL database using its ID.
        Use this tool when you need to read the full body of a specific email.
        
        Args:
            email_id (str): The unique ID of the email to read.
        """
        try:
            email = self.session.query(Emails).filter(Emails.gmail_id == email_id).first()
            if not email:
                return f"No email found with ID {email_id}."
            
            body = email.body if email.body else "No body content available."
            attachments_str = f" | Attachments: {email.attachments}" if getattr(email, 'attachments', None) else ""
            return (
                f"- ID: {email.gmail_id} | Date: {email.date} | From: {email.sender} | "
                f"To: {email.recipient} | Subject: <email_subject>{email.subject}</email_subject>{attachments_str}\n"
                f"Body:\n<email_body>\n{body}\n</email_body>"
            )
        except Exception as e:
            return f"Error reading email from local database: {e}"

    def parse_date(self, raw):
        if not raw or raw == "Unknown":
            return None
        if isinstance(raw, datetime):
            return raw
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except (ValueError, TypeError):
                pass
        try:
            dt = parsedate_to_datetime(raw)
            if dt:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            return None


    def chunker(self, text: str, chunk_size: int = 1000, overlap: int = 200):
        if not text:
            return []

        chunks = []
        start = 0
        
        while start < len(text):
            old_start = start
            end = start + chunk_size
            
            if end < len(text):
                last_space = text.rfind(' ', start, end)
                if last_space != -1:
                    end = last_space
            
            chunks.append(text[start:end].strip())

            if end >= len(text): break
            
            new_start = end - overlap
            start = max(new_start, old_start + 1)

        
        return [c for c in chunks if c]


    def process_email(self, clean_body, message_id, gmail_id, sender, subject, date, account):
        chunks = self.chunker(clean_body, chunk_size=500)
        if not chunks:
            return

        documents = []
        for chunk in chunks:
            header = f"Email ID: {gmail_id}\nSender: {sender}\nSubject: {subject}\nDate: {date}\nAccount: {account}\n\n"
            documents.append(header + chunk)

        memory_client.collection.upsert(
            documents=documents,
            metadatas=[
                {"source": "email", "gmail_id": gmail_id, "chunk": i, "account": account}
                for i in range(len(chunks))
            ],
            ids=[f"{gmail_id}_chunk{i}" for i in range(len(chunks))]
        )

    def store_email(self, message_id, gmail_id, sender, recipient, subject, date, clean_body, account, attachments=None):
        try:
            parsed_d = self.parse_date(date)
            existing = self.session.query(Emails).filter(Emails.gmail_id == gmail_id).first()
            if existing:
                updated = False
                if clean_body and (not existing.body or len(clean_body) > len(existing.body)):
                    existing.body = clean_body
                    updated = True
                if attachments and not existing.attachments:
                    existing.attachments = attachments
                    updated = True
                if parsed_d and not existing.date:
                    existing.date = parsed_d
                    updated = True
                if updated:
                    self.session.commit()
                return False

            new_email = Emails(
                message_id=message_id,
                gmail_id=gmail_id,
                sender=sender,
                recipient=recipient,
                subject=subject,
                date=parsed_d,
                body=clean_body,
                account_name=account,
                attachments=attachments
            )

            self.session.add(new_email)
            self.session.commit()
            return True

        except IntegrityError:
            self.session.rollback()
            return False
        except Exception:
            self.session.rollback()
            return False    

    def delete_email(self, gmail_id: str):
        try:
            self.session.query(Emails).filter(Emails.gmail_id == gmail_id).delete()
            self.session.commit()
            
            memory_client.collection.delete(where={"gmail_id": gmail_id})
            
            return True
        except Exception as e:
            self.session.rollback()
            return False    


        
        



