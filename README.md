# Marcus: Omni-Channel AI Executive Assistant

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Model Context Protocol](https://img.shields.io/badge/MCP-FastMCP-green.svg)](https://modelcontextprotocol.io/)
[![Google Gemini API](https://img.shields.io/badge/LLM-Gemini%202.5%2F3.1-orange.svg)](https://aistudio.google.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Marcus is a fully autonomous, persona-driven **Omni-Channel Executive Assistant** built on modern **Model Context Protocol (MCP)** multi-server architecture. Designed to eliminate daily communication and organizational overhead, Marcus manages multi-account Gmail inboxes, automates Telegram messaging and channel organization, and leverages local SQL and Vector databases for long-term semantic memory.

Unlike naive chatbot wrappers, Marcus features an asynchronous, streaming multi-turn tool execution loop with strict **Human-in-the-Loop (HITL)** safeguards for destructive and outbound actions.

---

## Architecture Overview

```mermaid
flowchart TD
    subgraph Clients["User Interfaces"]
        CLI["Terminal CLI Agent (main.py)"]
        TGBot["Telegram Bot Client (marcus_bot.py)<br/>• Single-User Auth Whitelist<br/>• Inline Approval Buttons"]
    end

    subgraph CoreEngine["Marcus Core Engine (agent/engine.py)"]
        PromptEngine["System Prompts & Context<br/>(identity.md, soul.md, user.md)"]
        LLM["Google Gemini API<br/>(Streaming & Multi-Turn Loop)"]
        Retry["503 Backoff & Fallback Engine<br/>(Local Ollama lfm2:24b)"]
        MCPManager["Async MCP Client Manager<br/>(agent/mcp_client.py)"]
    end

    subgraph MCPServers["Subprocess MCP Servers (FastMCP / Stdio)"]
        EmailServer["Email MCP Server<br/>(mcp_servers/email_mcp_server.py)<br/>• Multi-Account Gmail (Personal/Work)<br/>• MIME Parsing & Safe Drafting<br/>• SQLite Email Caching"]
        TelegramServer["Telegram MCP Server<br/>(mcp_servers/telegram_mcp_server.py)<br/>• Telethon MTProto Client<br/>• Chats, Folders, Read-Receipts, Pinning"]
        KnowledgeServer["Knowledge & Memory MCP Server<br/>(mcp_servers/knowledge_mcp_server.py)<br/>• ChromaDB Vector Store<br/>• Ollama bge-m3 Semantic Search"]
    end

    subgraph Persistence["Storage & Databases"]
        SQLiteDB[("SQLite Database<br/>(agent/data/agent_emails.db)")]
        ChromaStore[("ChromaDB Vector Store<br/>(agent/data/chroma_db)")]
    end

    CLI --> CoreEngine
    TGBot --> CoreEngine
    CoreEngine --> MCPManager
    MCPManager <==> EmailServer
    MCPManager <==> TelegramServer
    MCPManager <==> KnowledgeServer
    EmailServer --> SQLiteDB
    KnowledgeServer --> ChromaStore
```

---

## Key Capabilities

### 1. Model Context Protocol (MCP) Multi-Server Isolation
- **Process Isolation**: Tools run as isolated background stdio subprocesses adhering to the Anthropic Model Context Protocol specification.
- **Dynamic Discovery**: The core engine queries and aggregates tools at boot without hardcoded coupling.
- **Fail-Safe Startup**: If a specific server (e.g., Telegram) is unconfigured, Marcus logs a warning and continues running with available servers.

### 2. Multi-Account Gmail Integration
- **Personal & Work Support**: Manages multiple independent OAuth2 tokens (`token_personal.json`, `token_work.json`).
- **Safe Drafting Protocol**: Strictly enforces a draft-first rule (`create_draft`, `create_response_draft`, `forward_email`). Marcus is programmatically prevented from sending emails without explicit approval.
- **Local Relational Caching**: Automatically persists email headers, bodies, and attachment metadata into SQLite via SQLAlchemy for instant local search.

### 3. Telegram Automation (User MTProto & Bot UI)
- **MTProto User Automation**: Powered by Telethon to manage folders/dialog filters, unread badge clearing, channel creation, and pinned chats.
- **Decoupled Bot Interface (`marcus_bot.py`)**: A dedicated Telegram Bot interface built on `aiogram 3.x` with:
  - **Single-User Whitelist**: Rejects requests from unauthorized user IDs for complete security.
  - **Interactive Inline Keyboard Approvals**: Sends interactive `✅ Approve` / `❌ Deny` prompts to your phone before executing sensitive actions (sending messages, trashing threads).

### 4. Hybrid Long-Term Memory
- **Structured Search**: Query email metadata, date ranges, senders, and attachment presence with SQLAlchemy.
- **Semantic Vector Memory**: Uses ChromaDB and Ollama (`bge-m3` embedding model) to save user preferences, contact details, and historical context across sessions.

### 5. Resilient AI Engine
- **Streaming Execution**: Real-time response streaming for fast user feedback.
- **Multi-Turn Tool Calling**: Autonomously chains tools (e.g., search -> retrieve -> draft -> summarize) in a single user query.
- **API Resilience**: Automatic exponential backoff for temporary Gemini 503 unavailability, accompanied by local Ollama fallback support.

---

## Project Structure

```shell
├── agent/
│   ├── data/
│   │   └── database_manager.py     # SQLite schema and email search queries
│   ├── engine.py                   # MarcusEngine: Gemini streaming, tool loops, fallback
│   └── mcp_client.py               # Async multi-server MCP client manager
├── context/
│   ├── identity.md                 # System instructions and operational constraints
│   ├── soul.md                     # Persona, tone, and brevity rules
│   ├── user.md.example             # Template for user profile and contact context
│   └── user.md                     # Personal user context (gitignored)
├── mcp_servers/
│   ├── email_mcp_server.py         # FastMCP server for Gmail multi-account operations
│   ├── telegram_mcp_server.py      # FastMCP server for Telegram MTProto automation
│   └── knowledge_mcp_server.py     # FastMCP server for ChromaDB vector memory
├── tools/
│   ├── knowledge_tools.py          # Vector memory utilities
│   └── system_tools.py             # System utilities (datetime, clean exit)
├── .env.example                    # Template for required environment variables
├── .gitignore                      # Security exclusions (tokens, sessions, DBs)
├── main.py                         # CLI entry point
├── marcus_bot.py                   # Telegram Bot UI entry point
└── requirements.txt                # Python dependencies
```

---

## Getting Started

### Prerequisites
- **Python 3.10+**
- **Google AI Studio API Key**: For Gemini models.
- *(Optional)* **Google Cloud OAuth 2.0 Client Credentials**: Required for Gmail features.
- *(Optional)* **Telegram API ID & Hash**: Required for Telegram user-client tools.
- *(Optional)* **Telegram Bot Token**: Required for running the Telegram Bot client (`marcus_bot.py`).
- *(Optional)* **Ollama**: If using offline embeddings (`bge-m3`) and local fallback models.

---

### Step-by-Step Setup

#### 1. Clone the Repository & Create Virtual Environment
```bash
git clone https://github.com/TheTymur/MarcusEmail.git
cd MarcusEmail

python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

#### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Open `.env` and insert your credentials:
```ini
# Required:
API_KEY_GEMINI=your_gemini_api_key_here

# Optional (for Telegram MCP tools):
API_TELEGRAM_ID=your_api_id
API_TELEGRAM_HASH=your_api_hash
TELEGRAM_SESSION_NAME=marcus_session

# Optional (for running marcus_bot.py):
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
ALLOWED_TELEGRAM_USER_ID=your_numeric_telegram_id
```

#### 4. Configure Personal Context (Optional)
Marcus adapts his tone and knows your contacts using a localized context file:
```bash
cp context/user.md.example context/user.md
```
Edit `context/user.md` to describe your personal/work background and communication preferences. *(This file is strictly gitignored to keep personal data private).*

#### 5. Configure Gmail OAuth (Optional)
If using the Gmail MCP Server:
1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and enable the **Gmail API**.
2. Create an **OAuth 2.0 Client ID** (Desktop Application) and download the JSON file.
3. Save it in the project root as `credentials.json`.
4. On first tool execution, a browser prompt will open to authorize access and create `token_personal.json` / `token_work.json`.

---

### Running Marcus

#### Option A: Terminal CLI Interface
Run the interactive CLI agent directly in your terminal:
```bash
python main.py
```

#### Option B: Telegram Bot Client (Remote Control)
Run the background Telegram bot to control Marcus from your phone with interactive approval buttons:
```bash
python marcus_bot.py
```

---

## Security & Privacy Notice (For Reviewers & Recruiters)

This repository represents an authentic, production-grade personal automation system tailored for daily productivity. For security and privacy:
- **No Credentials Committed**: All OAuth tokens (`token_*.json`), Telegram session files (`*.session`), Google Client Secrets (`credentials.json`), SQLite databases (`*.db`), and personal context profiles (`context/user.md`) are explicitly excluded via `.gitignore`.
- **Zero Hallucination Sending**: Outgoing emails and messages require explicit affirmative user confirmation or an interactive button click (`Approve`).
- **Single-User Telegram Whitelist**: The Telegram bot drops any incoming message whose sender ID does not match `ALLOWED_TELEGRAM_USER_ID`.

---

## License

This project is licensed under the [MIT License](LICENSE).
