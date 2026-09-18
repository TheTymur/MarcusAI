import chromadb
from chromadb import Settings

class VectorMemory:
    def __init__(self, db_path="./agent/data/chroma_db"):
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name="longterm_memory")

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
    
    def search_knowledge_base(self, query: str):
        """
        Searches the vector database for past emails, documents, or knowledge.
        Use this tool when you need to recall previous interactions, retrieve standard 
        operating procedures, or find context that is not in the current chat history.
        """

        try: 
            results = self.collection.query(
                query_texts=[query],
                n_results=3
            )
            
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
            