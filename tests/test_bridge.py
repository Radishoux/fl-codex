import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
sys.path.insert(0, str(ROOT))
from bridge_client import Client, ensure_relay
from fl_codex_engine import BridgeError, Engine
from fl_codex_wire import Server


class FakePlugins:
    def __init__(self):
        self.name = "Test Reverb"
        self.values = [0.25, 0.5]
        self.names = ["Wet", "Decay"]
        self.calls = []

    def isValid(self, index, slot, global_index):
        return (index, slot) in [(1, 0), (0, -1)]

    def getPluginName(self, index, slot, user_name, global_index):
        return self.name

    def getParamCount(self, index, slot, global_index):
        return len(self.values)

    def getParamName(self, param, index, slot, global_index):
        return self.names[param]

    def getParamValue(self, param, index, slot, global_index):
        return self.values[param]

    def getParamValueString(self, param, index, slot, global_index):
        return str(round(self.values[param] * 100)) + "%"

    def setParamValue(self, value, param, index, slot, pickup, global_index):
        self.calls.append((value, param, index, slot, pickup, global_index))
        self.values[param] = round(value, 2)  # A discretized plugin control.


def make_engine():
    return Engine(FakePlugins(),
                  SimpleNamespace(trackCount=lambda: 5, trackNumber=lambda: 1,
                                  getTrackName=lambda i: "Guitar" if i == 1 else "Insert %s" % i),
                  SimpleNamespace(channelCount=lambda flag: 1, selectedChannel=lambda *args: 0,
                                  getChannelName=lambda i, g: "Guitar instrument",
                                  getTargetFxTrack=lambda i, g: 1),
                  SimpleNamespace(isPlaying=lambda: False, isRecording=lambda: False),
                  SimpleNamespace(safeToEdit=lambda: True),
                  SimpleNamespace(getVersion=lambda: "Test FL API"))


class ControllerStartupTests(unittest.TestCase):
    def test_entry_script_starts_without_file_global(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "connection.json").write_text(
                json.dumps({"token": "test-pairing", "port": 32123}), encoding="utf-8")
            engine = make_engine()
            server_factory = MagicMock()
            modules = {name: SimpleNamespace() for name in
                       ("channels", "general", "mixer", "plugins", "transport", "ui")}
            modules["device"] = SimpleNamespace(isAssigned=lambda: True)
            modules["fl_codex_engine"] = SimpleNamespace(Engine=lambda *args: engine)
            modules["fl_codex_wire"] = SimpleNamespace(
                __file__=str(folder / "fl_codex_wire.py"))
            modules["fl_codex_midi"] = SimpleNamespace(MidiServer=server_factory, PREFIX=b"\xf0\x7dFLCX\x01")
            namespace = {"__name__": "fl_controller_entry"}
            source = (ROOT / "controller" / "device_FL_Codex.py").read_text(encoding="utf-8")
            with patch.dict(sys.modules, modules), patch("builtins.print"):
                exec(compile(source, "device_FL_Codex.py", "exec"), namespace)
                namespace["OnInit"]()
                server_factory.assert_called_once_with(engine, "test-pairing", modules["device"])
                namespace["OnIdle"]()
                server_factory.return_value.poll.assert_called_once_with()
                namespace["OnDeInit"]()
                server_factory.return_value.close.assert_called_once_with()
                self.assertIsNone(namespace["_server"])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def handle(self):
        return self.engine.get_parameters("effect", 1, 0)["parameters"][0]["handle"]

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(BridgeError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_change_reads_back_quantized_value_and_undo_restores(self):
        result = self.engine.set_parameter(self.handle(), 0.333)
        self.assertEqual(result["after"], 0.33)
        self.assertEqual(result["before"], 0.25)
        self.assertEqual(self.engine.undo_last()["restored"], 0.25)
        self.assertEqual(self.engine.history, [])

    def test_global_instrument_address(self):
        row = self.engine.get_parameters("instrument", 0)["parameters"][0]
        self.engine.set_parameter(row["handle"], 0.3)
        self.assertEqual(self.engine.plugins.calls[-1][2:], (0, -1, 0, True))

    def test_reject_nonfinite_out_of_range_and_boolean(self):
        handle = self.handle()
        for value in [float("nan"), float("inf"), -0.01, 1.01, True, "0.3"]:
            self.assert_error("INVALID_ARGUMENT", self.engine.set_parameter, handle, value)
        self.assertEqual(self.engine.plugins.calls, [])

    def test_reject_large_jump(self):
        self.assert_error("DELTA_LIMIT", self.engine.set_parameter, self.handle(), 0.8)
        self.assertEqual(self.engine.plugins.calls, [])

    def test_manual_change_is_not_overwritten(self):
        handle = self.handle()
        self.engine.plugins.values[0] = 0.27
        self.assert_error("VALUE_CHANGED", self.engine.set_parameter, handle, 0.3)
        self.assertEqual(self.engine.plugins.calls, [])

    def test_replaced_plugin_or_parameter_rejected(self):
        handle = self.handle()
        self.engine.plugins.name = "Other effect"
        self.assert_error("STALE_TARGET", self.engine.set_parameter, handle, 0.3)
        handle = self.handle()
        self.engine.plugins.names[0] = "Output gain"
        self.assert_error("STALE_TARGET", self.engine.set_parameter, handle, 0.3)

    def test_project_load_invalidates_handles_and_history(self):
        handle = self.handle()
        self.engine.project_load(0)
        self.assert_error("FL_BUSY", self.engine.set_parameter, handle, 0.3)
        self.engine.project_load(100)
        self.assert_error("STALE_HANDLE", self.engine.set_parameter, handle, 0.3)
        self.assert_error("NOTHING_TO_UNDO", self.engine.undo_last)

    def test_expired_handle_rejected(self):
        handle = self.handle()
        self.engine.handles[handle]["read_at"] -= 121
        self.assert_error("STALE_HANDLE", self.engine.set_parameter, handle, 0.3)

    def test_recording_refuses_changes(self):
        self.engine.transport.isRecording = lambda: True
        self.assert_error("RECORDING", self.engine.set_parameter, self.handle(), 0.3)

    def test_undo_does_not_overwrite_manual_change(self):
        self.engine.set_parameter(self.handle(), 0.3)
        self.engine.plugins.values[0] = 0.4
        self.assert_error("VALUE_CHANGED", self.engine.undo_last)
        self.assertEqual(len(self.engine.history), 1)

    def test_current_alias_track_excluded_and_pages_complete(self):
        page = self.engine.list_tracks(limit=2)
        self.assertEqual(page["total"], 4)
        self.assertEqual(page["next_start"], 2)
        second = self.engine.list_tracks(start=2, limit=2)
        self.assertIsNone(second["next_start"])
        self.assert_error("INVALID_ARGUMENT", self.engine.list_plugins, "effect", 4)

    def test_unknown_methods_rejected(self):
        self.assert_error("UNKNOWN_METHOD", self.engine.dispatch, "eval", {"code": "anything"})


class WireTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        self.server = Server(self.engine, "test-token-" * 4, 0)
        self.addCleanup(self.server.close)

    def request(self, **changes):
        request = {"id": "test-1", "token": "test-token-" * 4,
                   "expires_at": time.time() + 8, "method": "status", "args": {}}
        request.update(changes)
        return json.dumps(request).encode()

    def test_wrong_token_rejected(self):
        result = json.loads(self.server.reply(self.request(token="wrong")))
        self.assertEqual(result["error"]["code"], "UNAUTHORIZED")

    def test_expired_write_does_not_run(self):
        result = json.loads(self.server.reply(self.request(method="undo_last", expires_at=time.time() - 1)))
        self.assertEqual(result["error"]["code"], "EXPIRED")
        self.assertEqual(self.engine.plugins.calls, [])

    def test_duplicate_write_runs_once(self):
        handle = self.engine.get_parameters("effect", 1, 0)["parameters"][0]["handle"]
        request = self.request(method="set_parameter", args={"handle": handle, "value": 0.3})
        first = self.server.reply(request)
        self.assertEqual(first, self.server.reply(request))
        self.assertTrue(json.loads(first)["ok"])
        self.assertEqual(len(self.engine.plugins.calls), 1)
        reused = json.loads(self.server.reply(self.request()))
        self.assertEqual(reused["error"]["code"], "ID_REUSED")

    def test_malformed_requests_do_not_crash_server(self):
        for request in [b"broken", b"[]", b"null", self.request(args=[]), self.request(expires_at=float("nan"))]:
            self.assertFalse(json.loads(self.server.reply(request))["ok"])

    def test_real_socket_partial_frame_and_response(self):
        received, errors = [], []

        def external_client():
            try:
                with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as conn:
                    payload = self.request() + b"\n"
                    conn.sendall(payload[:12])
                    time.sleep(0.01)
                    conn.sendall(payload[12:])
                    response = b""
                    while b"\n" not in response:
                        chunk = conn.recv(8192)
                        if not chunk:
                            raise RuntimeError("Unexpected EOF")
                        response += chunk
                    received.append(json.loads(response))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=external_client)
        worker.start()
        deadline = time.monotonic() + 3
        while worker.is_alive() and time.monotonic() < deadline:
            self.server.poll()
            time.sleep(0.002)
        worker.join(timeout=1)
        self.assertEqual(errors, [])
        self.assertTrue(received[0]["ok"])
        self.assertEqual(received[0]["result"]["fl_version"], "Test FL API")


class ClientTests(unittest.TestCase):
    def test_relay_autostarts_after_windows_connect_timeout(self):
        with patch("socket.create_connection", side_effect=[TimeoutError, MagicMock()]), \
             patch("pathlib.Path.open", mock_open()), patch("subprocess.Popen") as launch:
            ensure_relay({"port": 32123})
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0][1], str(ROOT / "relay.py"))

    def test_existing_relay_does_not_start_duplicate(self):
        with patch("socket.create_connection", return_value=MagicMock()), patch("subprocess.Popen") as launch:
            ensure_relay({"port": 32123})
            launch.assert_not_called()

    def test_connect_timeout_reports_no_request_sent(self):
        with patch("pathlib.Path.read_text", return_value=json.dumps({"port": 32123, "token": "test"})), \
             patch("socket.create_connection", side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, "no request was sent"):
                Client(ROOT / ".local" / "test-config.json").call("status")


if __name__ == "__main__":
    unittest.main()
