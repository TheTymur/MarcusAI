# AI_EmailAgent

## Intro
AI_EmailAgent is an intelligent, CLI-based Executive Email Assistant designed to help you manage your Gmail inbox efficiently. Operating under the persona of a concise, slightly sarcastic assistant named Marcus, it combines the Gemini API with direct Gmail integration to proactively summarize threads, sort incoming mail, and draft contextual responses.

## Technologies and tools used for this project:
*   **Core Logic:** Python 3.x
*   **AI Engine:** Google GenAI SDK (`gemini-3.1-flash-lite` model)
*   **Email Integration:** Google Workspace Gmail API (OAuth 2.0)

## Project Structure
```shell
├── .venv/                     # Virtual environment
├── agent/                     # Core agent application module
│   ├── __init__.py
│   └── engine.py              # Gemini client setup and API retry logic
├── context/                   # Modular persona and prompt configuration
│   ├── identity.md            # Core AI instructions and tool constraints
│   ├── soul.md                # Concise personality rules
│   └── user.md                # User background and inbox priorities
├── tools/                     # Custom tools for the agent
│   ├── __init__.py
│   ├── gmail_tools.py         # GmailClient class for OAuth and email operations
│   ├── multiply.py            # Example mathematical utility tool
│   └── system_tools.py        # System functions for exiting and time checking
├── .env                       # Environment variables (Gemini API Key)
├── .gitignore                 # Git exclusion rules
├── credentials.json           # Google OAuth desktop client secrets
├── LICENSE                    # Open-source license
├── main.py                    # Application entry point
├── README.md                  # Project documentation
├── requirements.txt           # Python dependencies
└── token.json                 # Generated upon first successful Google login
```

## Features
*   **Smart Inbox Sorting:** Automatically categorizes emails into dedicated labels (e.g., University, Travel) while routing spam and newsletters to the Trash.
*   **Proactive Summarization:** Reads recent emails and generates structured summaries, clearly distinguishing between received messages and sent ones.
*   **Safe Drafting Protocol:** Contextually drafts new emails, replies, and forwards while maintaining thread IDs, but operates under a strict ban against sending anything without explicit user approval.
*   **Deep Search Protocol:** Conducts comprehensive inbox searches across specific folders, archived mail, and sent messages to track down missing context.
*   **Resilient API Handling:** Built-in exponential backoff retry logic to handle temporary 503 API unavailability gracefully.

## Setup
You will need a Gemini API key and a Google Cloud desktop OAuth `credentials.json` file.

1.  **Install dependencies:** Ensure you have the required Google authentication and Generative AI packages installed via `requirements.txt`:
    ```shell
    pip install -r requirements.txt
    ```
2.  **Environment Variables:** Create a file named `.env` in the root directory and add your Gemini API key:
    ```text
    API_KEY_GEMINI=your_gemini_api_key_here
    ```
3.  **Gmail Credentials:** Place your downloaded OAuth 2.0 client secret file into the root directory and name it `credentials.json`.
4.  **Run the application:**
    ```shell
    python main.py
    ```

*   **Authentication:** On the first run, a browser window will automatically open to request Gmail access permissions. After approval, a `token.json` file is saved locally so you do not need to log in again.

## Current limitations
*   **CLI Interface Only:** The application runs entirely in the terminal without a graphical user interface.
