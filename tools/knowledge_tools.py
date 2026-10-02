from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
import chromadb
from pydantic import Field

class VectorMemory:
    def __init__(self, db_path="./agent/data/chroma_db"):
        self.client = chromadb.PersistentClient(path=db_path)
        self.ef = OllamaEmbeddingFunction(
            url="http://localhost:11434/api/embeddings",
            model_name="bge-m3"
        )
        self.collection = self.client.get_or_create_collection(name="longterm_memory", embedding_function=self.ef)

    def add_memory(self, text_chunk: str, source_id: str, chunk_index: int) -> str:
        """Adds a text chunk to the vector memory."""

        doc_id = f"{source_id}-{chunk_index}"

        try:
            self.collection.add(
                documents=[text_chunk],
                metadatas=[{"source": source_id, "index": chunk_index}],
                ids= [doc_id]
            )

            return f"Memory added: {doc_id}"

        except Exception as error:
            return f"Error adding memory: {error}"
    
    def save_user_preference(self, fact: str = Field(description="The fact, contact, or preference about the user to save.")):
        """
        Saves a fact, contact, or preference about the user into long-term memory.
        Use this tool whenever the user tells you something you should remember for the future
        (e.g., someone's name, email, relationship to the user, or how they want things done).
        """
        import time
        print("[System Info]: Saving user preference...")
        return self.add_memory(fact, "user_preference", int(time.time()))
    
    def search_knowledge_base(self, query: str = Field(description="The query to search the knowledge base for."), where_account: str = Field(None, description="Optional account filter, e.g. 'work' or 'personal'.")) -> str:
        """
        Searches the vector database for past emails, documents, or knowledge.
        Use this tool when you need to recall previous interactions, retrieve standard 
        operating procedures, or find context that is not in the current chat history.
        """

        try: 
            query_args = {
                "query_texts": [query],
                "n_results": 3
            }

            if where_account:
                query_args["where"] = {"account": where_account}
            
            results = self.collection.query(**query_args)

            if not results["documents"] or not results["documents"][0]:
                return "No relevant information found in memory."
            
            formatted = []
            for i, document in enumerate(results["documents"][0]):
                source = results["metadatas"][0][i].get('source', "Unknown Source")
                formatted.append(f"Result {i+1} (Source: {source}):\n{document}")

            return '\n'.join(formatted)

        except Exception as error:
            return f"Error searching memory: {error}"
            

    def get_recent_summaries(self, limit: int = 3) -> str:
        """
        Retrieves the most recent chat summaries chronologically.
        Called by the system at boot to build dynamic instructions.
        """
        try:
            results = self.collection.get(
                where={"source": {"$in": ["chat_summary", "chat_summary_final"]}}
            )
            
            if not results['documents']:
                return ""

            sorted_docs = sorted(
                zip(results['ids'], results['documents']), 
                key=lambda x: x[0], 
                reverse=True
            )
            
            recent_docs = sorted_docs[:limit][::-1]
            
            formatted_memory = "Recent Memory Context:\n"
            for _, doc in recent_docs:
                formatted_memory += f"- {doc}\n"
                
            return formatted_memory
        except Exception as e:
            print(f"[System Error]: Failed to load recent memory: {e}")
            return ""
            