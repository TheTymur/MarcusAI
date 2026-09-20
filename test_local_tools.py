from openai import OpenAI
import json
import sys

def exit_agent(message: str):
    print(f"\nAI: {message}\n")
    sys.exit(0)

client = OpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1"
)

exit_tool_schema = {
    "type": "function",
    "function": {
        "name": "exit_agent",
        "description": "Exits the agent if the user told it to exit or if the user is done with the agent. You MUST provide a 'message' argument with your final goodbye message to the user. Do NOT use this tool unless the user explicitly tells you to exit.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Your final goodbye message to the user."
                }
            },
            "required": ["message"]
        }
    }
}

try:
    response = client.chat.completions.create(
        model="lfm2:24b-a2b",
        messages=[{"role": "user", "content": "bye"}],
        tools=[exit_tool_schema]
    )
    message = response.choices[0].message
    if message.tool_calls:
        print("Tool call detected!")
        for tool_call in message.tool_calls:
            if tool_call.function.name == "exit_agent":
                args = json.loads(tool_call.function.arguments)
                print(f"Calling exit_agent with args: {args}")
                exit_agent(args.get("message", "Goodbye!"))
    else:
        print("No tool call:", message.content)
except Exception as e:
    print(f"FAILED: {e}")
