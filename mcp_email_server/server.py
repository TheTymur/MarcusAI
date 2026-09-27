from fastmcp import FastMCP
from tools import GmailClient, System, VectorMemory

mcp = FastMCP("email-agent")
gmail_client = GmailClient()
system_client = System()
memory_client = VectorMemory()

tools_to_add = [
    gmail_client.delete_label, gmail_client.remove_label, gmail_client.list_labels, gmail_client.count_messages_in_label,
    gmail_client.search_emails, gmail_client.delete_message, gmail_client.delete_draft, gmail_client.list_drafts, system_client.exit_agent,
    gmail_client.create_draft, gmail_client.modify_draft, gmail_client.send_draft, gmail_client.read_email_content, system_client.get_current_date_and_time,
    gmail_client.create_response_draft, gmail_client.forward_email, memory_client.search_knowledge_base, memory_client.save_user_preference,
    gmail_client.search_local_database, gmail_client.unsubscribe_from_email, gmail_client.block_sender, gmail_client.unblock_sender,
    gmail_client.list_blocked_senders, gmail_client.read_recent_emails, gmail_client.read_local_email, gmail_client.create_label, 
    gmail_client.apply_label, gmail_client.untrash_message
]

from mcp.types import ToolAnnotations

read_only_tools = {
    "read_recent_emails", "read_email_content", "list_labels", "list_drafts",
    "search_emails", "search_local_database", "count_messages_in_label",
    "list_blocked_senders", "get_current_date_and_time", "search_knowledge_base",
    "read_local_email"
}
destructive_tools = {
    "delete_message", "delete_draft", "delete_label", "untrash_message"
}

for t in tools_to_add:
    name = t.__name__
    is_ro = name in read_only_tools
    is_destructive = name in destructive_tools
    ann = ToolAnnotations(
        readOnlyHint=is_ro,
        destructiveHint=is_destructive,
        idempotentHint=is_ro,
        openWorldHint=False
    )
    mcp.tool(name=name, annotations=ann)(t)

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
