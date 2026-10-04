from fastmcp import FastMCP
from fastmcp.tools import tool
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
import chromadb
from pydantic import Field
from mcp.types import ToolAnnotations

mcp = FastMCP("knowledge_server")

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
    
    @tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def save_user_preference(self, fact: str = Field(description="The fact, contact, or preference about the user to save.")) -> str:
        """
        Saves a fact, contact, or preference about the user into long-term memory.
        Use this tool whenever the user tells you something you should remember for the future
        (e.g., someone's name, email, relationship to the user, or how they want things done).
        """
        import time
        return self.add_memory(fact, "user_preference", int(time.time()))


    @tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
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
                doc_id = results["ids"][0][i]
                formatted.append(f"Result {i+1} (ID: {doc_id}, Source: {source}):\n{document}")

            return '\n'.join(formatted)

        except Exception as error:
            return f"Error searching memory: {error}"
            

    @tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def get_recent_summaries(self, limit: int = Field(3, description="The maximum number of recent summaries to retrieve.")) -> str:
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


    @tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False))
    def delete_memory(self, memory_id: str = Field(description="The exact ID of the memory to delete.")) -> str:
        """
        Deletes a specific memory from the long-term vector database using its ID.
        Use this tool when the user corrects a previous fact, or asks you to forget something.
        """
        try:
            self.collection.delete(ids=[memory_id])
            return f"Successfully deleted memory with ID: {memory_id}"
        except Exception as error:
            return f"Error deleting memory: {error}"

memory = VectorMemory()


mcp.add_tool(memory.save_user_preference)
mcp.add_tool(memory.search_knowledge_base)
mcp.add_tool(memory.get_recent_summaries)
mcp.add_tool(memory.delete_memory)

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)