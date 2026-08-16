"""Phase 0 check B: a real MCP client launches the stdio server and calls the tool."""

import asyncio
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(ROOT, "houdini_mcp", "server.py")],
        env={**os.environ, "PYTHONPATH": ROOT},
        cwd=ROOT,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("SERVER:", init.server_info.name, init.server_info.version)

            tools = await session.list_tools()
            print("TOOLS:", [(t.name, t.description[:60] + "...") for t in tools.tools])

            result = await session.call_tool("houdini_ping", {"message": "round trip through MCP"})
            print("is_error:", result.is_error)
            for block in result.content:
                print("CONTENT:", block.text)
            if getattr(result, "structured_content", None):
                print("STRUCTURED:", result.structured_content)


asyncio.run(main())
