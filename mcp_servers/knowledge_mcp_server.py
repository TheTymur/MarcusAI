import time
from typing import Annotated

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

mcp = FastMCP("knowledge_server")


class VectorMemory:

    def __init__(self, db_path="./agent/data/chroma_db"):
        self.client = chromadb.PersistentClient(path=db_path)
        self.ef = OllamaEmbeddingFunction(
            url="http://localhost:11434/api/embeddings",
            model_name="bge-m3",
        )
        self.collection = self.client.get_or_create_collection(
            name="longterm_memory", embedding_function=self.ef
        )

    def add_memory(self, text_chunk: str, source_id: str, chunk_index: int) -> str:
        doc_id = f"{source_id}-{chunk_index}"
        try:
            self.collection.upsert(
                documents=[text_chunk],
                metadatas=[{"source": source_id, "index": chunk_index}],
                ids=[doc_id],
            )
            return f"Memory added: {doc_id}"
        except Exception as error:
            return f"Error adding memory: {error}"

    def search(self, query: str, where_account: str | None = None, n_results: int = 10) -> str:
        try:
            query_args = {"query_texts": [query], "n_results": n_results}
            if where_account:
                query_args["where"] = {"account": where_account}

            results = self.collection.query(**query_args)
            if not results["documents"] or not results["documents"][0]:
                return "No relevant information found in memory."

            formatted = []
            for i, document in enumerate(results["documents"][0]):
                source = results["metadatas"][0][i].get("source", "Unknown Source")
                doc_id = results["ids"][0][i]
                formatted.append(f"Result {i + 1} (ID: {doc_id}, Source: {source}):\n{document}")
            return "\n".join(formatted)
        except Exception as error:
            return f"Error searching memory: {error}"

    def get_recent_summaries(self, limit: int = 3) -> str:
        """Used by the system at boot, not exposed to the model as a tool."""
        try:
            results = self.collection.get(
                where={"source": {"$in": ["chat_summary", "chat_summary_final"]}}
            )
            if not results["documents"]:
                return ""

            sorted_docs = sorted(zip(results["ids"], results["documents"]), key=lambda x: x[0], reverse=True)
            recent_docs = sorted_docs[:limit][::-1]

            formatted = "Recent Memory Context:\n"
            for _, doc in recent_docs:
                formatted += f"- {doc}\n"
            return formatted
        except Exception as error:
            print(f"[System Error]: Failed to load recent memory: {error}")
            return ""

    def delete(self, memory_id: str) -> str:
        try:
            self.collection.delete(ids=[memory_id])
            return f"Successfully deleted memory with ID: {memory_id}"
        except Exception as error:
            return f"Error deleting memory: {error}"


memory = VectorMemory()


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def save_user_preference(
    fact: Annotated[str, Field(description="The fact, contact, or preference about the user to save.")],
) -> str:
    """
    Saves a fact, contact, or preference about the user into long-term memory.
    Use this tool whenever the user tells you something you should remember for the future
    (e.g., someone's name, email, relationship to the user, or how they want things done).
    """
    return memory.add_memory(fact, "user_preference", int(time.time()))


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def search_knowledge_base(
    query: Annotated[str, Field(description="The query to search the knowledge base for.")],
    where_account: Annotated[
        str | None, Field(description="Optional account filter, e.g. 'work' or 'personal'.")
    ] = None,
    limit: Annotated[
        int, Field(description="Maximum number of memory chunks to retrieve (default 10).")
    ] = 10,
) -> str:
    """
    Searches the vector database for past emails, documents, or knowledge.
    Use this tool when you need to recall previous interactions, retrieve standard
    operating procedures, or find context that is not in the current chat history.
    """
    return memory.search(query, where_account, n_results=limit)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False))
def delete_memory(
    memory_id: Annotated[str, Field(description="The exact ID of the memory to delete.")],
) -> str:
    """
    Deletes a specific memory from the long-term vector database using its ID.
    Use this tool when the user corrects a previous fact, or asks you to forget something.
    """
    return memory.delete(memory_id)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)