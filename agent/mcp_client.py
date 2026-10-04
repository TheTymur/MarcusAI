import sys
import contextlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_SESSIONS = {}

def make_tool_callable(session_id, tool_info):
    name = tool_info.name
    desc = tool_info.description or "No description"
    properties = tool_info.input_schema.get("properties", {}) if tool_info.input_schema else {}
    required = tool_info.input_schema.get("required", []) if tool_info.input_schema else []
    
    args_list = []
    for p_name, p_details in properties.items():
        ptype = p_details.get("type", "string")
        py_type = "str"
        if ptype == "integer": py_type = "int"
        elif ptype == "boolean": py_type = "bool"
        elif ptype == "array": py_type = "list"
        elif ptype == "object": py_type = "dict"
        
        if p_name in required:
            args_list.append(f"{p_name}: {py_type}")
        else:
            args_list.append(f"{p_name}: {py_type} = None")
            
    args_str = ", ".join(args_list)
    func_code = f"""
async def {name}({args_str}):
    \"\"\"{desc}\"\"\"
    import sys
    if 'agent.mcp_client' in sys.modules:
        session = sys.modules['agent.mcp_client']._SESSIONS['{session_id}']
    elif 'mcp_client' in sys.modules:
        session = sys.modules['mcp_client']._SESSIONS['{session_id}']
    else:
        import mcp_client
        session = mcp_client._SESSIONS['{session_id}']
        
    kwargs = locals()
    kwargs = {{k: v for k, v in kwargs.items() if v is not None and k not in ('agent', 'session', 'sys')}}
    try:
        result = await session.call_tool('{name}', arguments=kwargs)
        return str(result.content[0].text) if result.content else str(result)
    except Exception as e:
        return f"Error calling tool: {{e}}"
"""
    local_vars = {}
    exec(func_code, globals(), local_vars)
    return local_vars[name]

class MCPClientManager:
    def __init__(self, server_modules=None):
        """
        Initializes MCPClientManager with a list of server modules to start.
        Defaults to loading both email and telegram MCP servers.
        """
        if server_modules is None:
            server_modules = [
                "mcp_servers.email_mcp_server",
                "mcp_servers.telegram_mcp_server",
                "mcp_servers.knowledge_mcp_server"
            ]
            
        self.server_params_list = [
            StdioServerParameters(command=sys.executable, args=["-m", mod], show_banner=False)
            for mod in server_modules
        ]
        self.exit_stack = contextlib.AsyncExitStack()
        self.session_ids = []

    async def connect_and_get_tools(self):
        """Connects to all configured MCP servers and fetches all tools across them."""
        all_tools = []
        
        for params in self.server_params_list:
            try:
                read, write = await self.exit_stack.enter_async_context(stdio_client(params))
                session = await self.exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                
                session_id = str(id(session))
                _SESSIONS[session_id] = session
                self.session_ids.append(session_id)
                
                mcp_tools_list = await session.list_tools()
                for tool_info in mcp_tools_list.tools:
                    all_tools.append(make_tool_callable(session_id, tool_info))
            except Exception as e:
                print(f"[System Warning]: Failed to connect to MCP server ({' '.join(params.args)}): {e}")
                
        return all_tools

    async def cleanup(self):
        """Cleanly closes all active server contexts and removes sessions."""
        for session_id in self.session_ids:
            _SESSIONS.pop(session_id, None)
        self.session_ids.clear()
        
        try:
            await self.exit_stack.aclose()
        except Exception:
            pass