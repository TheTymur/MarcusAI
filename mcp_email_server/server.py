from fastmcp import FastMCP
from tools import GmailClient, System, VectorMemory, multiply

mcp = FastMCP("email-agent")
gmail_client = GmailClient()
system_client = System()
memory_client = VectorMemory()

tools_to_add = [
    multiply, gmail_client.read_recent_emails, gmail_client.create_label, gmail_client.apply_label,
    gmail_client.delete_label, gmail_client.remove_label, gmail_client.list_labels, gmail_client.count_messages_in_label,
    gmail_client.search_emails, gmail_client.delete_message, gmail_client.delete_draft, gmail_client.list_drafts, system_client.exit_agent,
    gmail_client.create_draft, gmail_client.modify_draft, gmail_client.send_draft, gmail_client.read_email_content, system_client.get_current_date_and_time,
    gmail_client.create_response_draft, gmail_client.forward_email, memory_client.search_knowledge_base, memory_client.save_user_preference,
    gmail_client.search_local_database, gmail_client.unsubscribe_from_email, gmail_client.block_sender, gmail_client.unblock_sender, system_client.deep_system_diagnostic,
    gmail_client.list_blocked_senders
]

for t in tools_to_add:
    mcp.add_tool(t)

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
