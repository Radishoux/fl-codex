"""Check the real stdio MCP handshake and tools without editing an FL project."""
import asyncio
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            tools = await session.list_tools()
            expected = {"get_fl_status", "list_fl_tracks", "list_fl_plugins", "get_fl_parameters", "set_fl_parameter", "undo_fl_change"}
            assert {tool.name for tool in tools.tools} == expected
            for tool in tools.tools:
                assert tool.description and tool.inputSchema["type"] == "object"
            # Verify server-side schema rejection before any bridge dispatch.
            invalid = await session.call_tool("set_fl_parameter", {"handle": "invalid", "value": 2})
            assert invalid.isError, "Out-of-range parameter should be rejected"
            manifest = {"tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools.tools]}
            (ROOT / "tool_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print("MCP handshake OK: " + info.serverInfo.name)
            print("Tool discovery and range validation OK: " + ", ".join(sorted(expected)))


if __name__ == "__main__":
    asyncio.run(main())
