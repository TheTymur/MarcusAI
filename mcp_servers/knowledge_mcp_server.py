import time
from typing import Annotated


from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from agent.crash_handler import CrashRecorder
from agent.memory import get_memory

crash_recorder = CrashRecorder()
crash_recorder.install()

memory = get_memory()

mcp = FastMCP("knowledge_server")

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