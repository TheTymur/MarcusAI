from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from dataclasses import dataclass

from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

DEFAULT_SERVER_MODULES = (
    "mcp_servers.email_mcp_server",
    "mcp_servers.telegram_mcp_server",
    "mcp_servers.knowledge_mcp_server",
)

CONNECT_TIMEOUT_S = 120 
CALL_TIMEOUT_S = 120
MAX_RESULT_CHARS = 20_000  


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False

    def as_text(self) -> str:
        """Single string form, for the OpenAI/Ollama 'tool' message."""
        return f"Error: {self.text}" if self.is_error else self.text


@dataclass(frozen=True)
class _RegisteredTool:
    server: str
    session: ClientSession
    tool: Tool


class MCPClientManager:
    def __init__(self, server_modules=None, approval_names=()):

        self.server_modules = list(server_modules or DEFAULT_SERVER_MODULES)
        self.approval_names = set(approval_names)
        self._exit_stack = contextlib.AsyncExitStack()
        self._tools: dict[str, _RegisteredTool] = {}
        self._servers_connected = 0

    async def connect(self) -> None:
        for module in self.server_modules:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", module],
                env={**os.environ},
            )
            try:
                read, write = await self._exit_stack.enter_async_context(stdio_client(params))
                session = await self._exit_stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT_S)

                listed = await session.list_tools()
                for tool in listed.tools:
                    if tool.name in self._tools:
                        print(
                            f"[System Warning]: duplicate tool '{tool.name}' from {module} "
                            f"(already provided by {self._tools[tool.name].server}); skipping."
                        )
                        continue
                    self._tools[tool.name] = _RegisteredTool(module, session, tool)
                self._servers_connected += 1
            except Exception as e:
                print(f"[System Warning]: Failed to connect to MCP server {module}: {e}")

    async def cleanup(self) -> None:
        try:
            await self._exit_stack.aclose()
        except Exception:
            pass
        self._tools.clear()

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def server_count(self) -> int:
        return self._servers_connected

    def needs_approval(self, name: str) -> bool:

        if name in self.approval_names:
            return True
        entry = self._tools.get(name)
        if entry is None:
            return False
        annotations = entry.tool.annotations
        return bool(annotations and not annotations.read_only_hint and annotations.destructive_hint)

    def gemini_tools(self) -> list[types.Tool]:
        declarations = [
            types.FunctionDeclaration(
                name=entry.tool.name,
                description=entry.tool.description or f"Executes {entry.tool.name}.",
                parameters_json_schema=entry.tool.input_schema,
            )
            for entry in self._tools.values()
        ]
        return [types.Tool(function_declarations=declarations)] if declarations else []

    def openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": entry.tool.name,
                    "description": entry.tool.description or f"Executes {entry.tool.name}.",
                    "parameters": entry.tool.input_schema or {"type": "object", "properties": {}},
                },
            }
            for entry in self._tools.values()
        ]

    async def call(self, name: str, args: dict | None = None) -> ToolResult:
        entry = self._tools.get(name)
        if entry is None:
            return ToolResult(f"Unknown tool '{name}'.", is_error=True)

        try:
            result = await entry.session.call_tool(
                name,
                args or {},
                read_timeout_seconds=float(CALL_TIMEOUT_S),
            )
        except Exception as e:
            return ToolResult(f"Calling '{name}' failed: {e}", is_error=True)

        parts = []
        for block in result.content:
            if block.type == "text":
                parts.append(block.text)
            else:
                parts.append(f"[{block.type} content omitted]")
        text = "\n".join(parts)

        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n...[truncated, {len(text) - MAX_RESULT_CHARS} more characters]"

        return ToolResult(text or "(no output)", is_error=bool(getattr(result, "is_error", getattr(result, "isError", False))))