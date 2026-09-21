from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker
from tools.knowledge_tools import VectorMemory
from email.utils import parsedate_to_datetime
from datetime import timezone
from typing import Optional
 
#TODO: hybrid search

Base = declarative_base()
memory_client = VectorMemory()

class Emails(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String, unique=True, nullable=True, index=True)
    gmail_id = Column(String, unique=True, nullable=False, index=True)

    sender = Column(String)
    recipient = Column(String)
    subject = Column(String)
    date = Column(DateTime)

    body = Column(Text)

    ai_processed = Column(Boolean, default=False)
    reply_drafted = Column(Boolean, default=False)


class Emailprocessing:
    def __init__(self, db_path="./agent/data/agent_emails.db"):
        engine = create_engine(f'sqlite:///{db_path}', echo=False)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        self.session = SessionLocal()



    def search_email_metadata(self, sender: Optional[str] = None, subject: Optional[str] = None, limit: int = 5) -> str:
        """
        Searches the local SQL database for emails by sender or subject.
        Use this tool when you need to find exact metadata, count emails from a specific person, 
        or check if an email about a specific topic exists.
        
        Args:
            sender (str, optional): A name or email address to search for in the 'From' field.
            subject (str, optional): Keywords to search for in the email subject.
            limit (int, optional): Maximum number of results to return.
        """
        try:
            query = self.session.query(Emails)
            
            if sender:
                query = query.filter(Emails.sender.ilike(f"%{sender}%"))
            if subject:
                query = query.filter(Emails.subject.ilike(f"%{subject}%"))
                
            
            results = query.order_by(Emails.date.desc()).limit(limit).all()
            
            if not results:
                return "No emails found in the local database matching those criteria."
                
            formatted = []
            for email in results:
                formatted.append(
                    f"- ID: {email.gmail_id} | Date: {email.date} | From: {email.sender} | To: {email.recipient} | Subject: {email.subject}"
                )
            return "\n".join(formatted)
            
        except Exception as e:
            return f"Database metadata search error: {e}"


    def parse_date(self, raw):
        try:
            dt = parsedate_to_datetime(raw)
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
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


    def process_email(self, clean_body, message_id, gmail_id, sender, subject, date):
        chunks = self.chunker(clean_body, chunk_size=500)
        if not chunks:
            return

        documents = []
        for chunk in chunks:
            header = f"Email ID: {gmail_id}\nSender: {sender}\nSubject: {subject}\nDate: {date}\n\n"
            documents.append(header + chunk)

        memory_client.collection.upsert(
            documents=documents,
            metadatas=[
                {"source": "email", "gmail_id": gmail_id, "chunk": i}
                for i in range(len(chunks))
            ],
            ids=[f"{gmail_id}_chunk{i}" for i in range(len(chunks))]
        )

    def store_email(self, message_id, gmail_id, sender, recipient, subject, date, clean_body):
        try:
            new_email = Emails(
                message_id=message_id,
                gmail_id=gmail_id,
                sender=sender,
                recipient=recipient,
                subject=subject,
                date=self.parse_date(date),
                body=clean_body
            )

            self.session.add(new_email)
            self.session.commit()
            return True

        except IntegrityError:
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


        
        



