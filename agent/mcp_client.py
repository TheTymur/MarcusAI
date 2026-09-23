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
        
        if p_name in required:
            args_list.append(f"{p_name}: {py_type}")
        else:
            args_list.append(f"{p_name}: {py_type} = None")
            
    args_str = ", ".join(args_list)
    func_code = f"""
async def {name}({args_str}):
    \"\"\"{desc}\"\"\"
    import agent.mcp_client
    session = agent.mcp_client._SESSIONS['{session_id}']
    kwargs = locals()
    kwargs = {{k: v for k, v in kwargs.items() if v is not None and k not in ('agent', 'session')}}
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
    def __init__(self):
        self.server_params = StdioServerParameters(command="python", args=["-m", "mcp_server.server"])
        self.exit_stack = contextlib.AsyncExitStack()
        self.session = None
        self.session_id = str(id(self))

    async def connect_and_get_tools(self):
        read, write = await self.exit_stack.enter_async_context(stdio_client(self.server_params))
        self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        
        _SESSIONS[self.session_id] = self.session
        
        mcp_tools_list = await self.session.list_tools()
        return [make_tool_callable(self.session_id, t) for t in mcp_tools_list.tools]

    async def cleanup(self):
        _SESSIONS.pop(self.session_id, None)
        try:
            await self.exit_stack.aclose()
        except Exception:
            pass
