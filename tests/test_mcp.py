"""End-to-end MCP -> TCP -> MIDI frames -> simulated FL -> write/undo."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
from fl_codex_wire import Server
from test_bridge import make_engine
from test_midi import FakeMidi
from midi_transport import MidiProxy

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


@unittest.skipUnless(HAS_MCP, "Install project dependencies to test MCP")
class MCPIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_discover_set_readback_undo_via_stdio(self):
        engine = make_engine()
        midi = FakeMidi(engine)
        proxy = MidiProxy("midi-test-token-" * 3, midi)
        bridge = Server(proxy, "integration-token-" * 3, 0)
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "connection.json"
            config.write_text(json.dumps({"port": bridge.port, "token": "integration-token-" * 3}))
            env = dict(os.environ, FL_CODEX_CONFIG=str(config))
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], env=env)

            async def poll():
                while True:
                    bridge.poll()
                    await asyncio.sleep(0.002)

            polling = asyncio.create_task(poll())
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()

                        async def call(name, args=None):
                            response = await session.call_tool(name, args or {})
                            self.assertFalse(response.isError, str(response.content))
                            return json.loads(response.content[0].text)

                        status = await call("get_fl_status")
                        self.assertEqual(status["fl_version"], "Test FL API")
                        tracks = await call("list_fl_tracks")
                        self.assertEqual(tracks["tracks"][1]["name"], "Guitar")
                        plugins = await call("list_fl_plugins", {"kind": "effect", "index": 1})
                        self.assertEqual(plugins["plugins"][0]["name"], "Test Reverb")
                        page = await call("get_fl_parameters", {"kind": "effect", "index": 1, "slot": 0})
                        handle = page["parameters"][0]["handle"]
                        changed = await call("set_fl_parameter", {"handle": handle, "value": 0.333})
                        self.assertEqual(changed["after"], 0.33)
                        restored = await call("undo_fl_change")
                        self.assertEqual(restored["restored"], 0.25)
            finally:
                polling.cancel()
                try:
                    await polling
                except asyncio.CancelledError:
                    pass
                bridge.close()
