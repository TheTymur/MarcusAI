from functools import lru_cache
import time
import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

DEFAULT_DB_PATH = "./agent/data/chroma_db"


class VectorMemory:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.client = chromadb.PersistentClient(path=db_path)
        self.ef = OllamaEmbeddingFunction(
            url="http://localhost:11434/api/embeddings",
            model_name="bge-m3",
        )
        self.collection = self.client.get_or_create_collection(
            name="longterm_memory", embedding_function=self.ef
        )

    def add_memory(self, text_chunk, source_id, chunk_index, account=None) -> str:
        doc_id = f"{source_id}-{chunk_index}"
        meta = {"source": source_id, "index": chunk_index, "timestamp": int(time.time())}
        if account:
            meta["account"] = account
        try:
            self.collection.upsert(
                documents=[text_chunk],
                metadatas=[meta],
                ids=[doc_id],
            )
            return f"Memory added: {doc_id}"
        except Exception as error:
            return f"Error adding memory: {error}"

    def search(self, query, where_account=None, n_results=10, source=None) -> str:
        conditions = []
        if where_account:
            conditions.append({"account": where_account})
        if source:
            conditions.append({"source": source})
        query_args = {"query_texts": [query], "n_results": n_results}
        if len(conditions) == 1:
            query_args["where"] = conditions[0]
        elif len(conditions) > 1:
            query_args["where"] = {"$and": conditions}
        try:
            results = self.collection.query(**query_args)
            if not results["documents"] or not results["documents"][0]:
                return "No relevant information found in memory."

            formatted = []
            for i, document in enumerate(results["documents"][0]):
                source_name = results["metadatas"][0][i].get("source", "Unknown Source")
                doc_id = results["ids"][0][i]
                formatted.append(f"Result {i + 1} (ID: {doc_id}, Source: {source_name}):\n{document}")
            return "\n".join(formatted)
        except Exception as error:
            return f"Error searching memory: {error}"

    def get_recent_summaries(self, limit: int = 3) -> str:
        try:
            results = self.collection.get(
                where={"source": {"$in": ["chat_summary", "chat_summary_final"]}}
            )
            if not results["documents"]:
                return ""

            sorted_docs = sorted(
                zip(results["ids"], results["documents"]), key=lambda x: x[0], reverse=True
            )
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


@lru_cache(maxsize=1)
def get_memory() -> VectorMemory:
    """One shared instance per process, so only one Chroma client is opened."""
    return VectorMemory()