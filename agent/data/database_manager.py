from google.genai import documents
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker
from tools.knowledge_tools import VectorMemory
 
Base = declarative_base()
memory_client = VectorMemory()

class Emails(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String, unique=True, nullable=False, index=True)

    sender = Column(String)
    recipient = Column(String)
    subject = Column(String)
    date = Column(String)

    body = Column(Text)

    ai_processed = Column(Boolean)
    reply_drafted = Column(Boolean)


class Emailprocessing:
    def __init__(self, db_path="./agent/data/agent_emails.db"):
        engine = create_engine(f'sqlite:///{db_path}', echo=False)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        self.session = SessionLocal()


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
            
            new_start = end - overlap
            start = max(new_start, old_start + 1)

        
        return [c for c in chunks if c]


    def process_email(self, clean_body, message_id):
        chunks= self.chunker(clean_body, chunk_size=500)

        for i, chunk in enumerate(chunks):
            memory_client.collection.add(
                documents=[chunk],
                metadatas=[{"source": "email", "message_id": message_id}],
                ids=[f"{message_id}_chunk{i}"]
            )


    def store_email(self, message_id, sender, recipient, subject, date, clean_body):
        try:
            new_email = Emails(
                message_id=message_id,
                sender=sender,
                recipient=recipient,
                subject=subject,
                date=date,
                body=clean_body
            )

            self.session.add(new_email)
            self.session.commit()

        except IntegrityError:
            self.session.rollback()
            print(f"[System Info]: Email {message_id} already exists in database.")
        finally:
            self.session.close()


        
        



