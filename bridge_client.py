"""Standalone CLI and MCP client for the local FL controller."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent


def ensure_relay(config):
    """Start our local helper only when its TCP port is not listening."""
    try:
        with socket.create_connection(("127.0.0.1", config["port"]), timeout=0.3):
            return
    except (ConnectionRefusedError, TimeoutError):
        pass
    log_path = ROOT / ".local" / "relay.log"
    with log_path.open("a", encoding="utf-8") as log:
        helper = subprocess.Popen([sys.executable, str(ROOT / "relay.py")], cwd=ROOT,
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                  creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", config["port"]), timeout=0.3):
                return
        except (ConnectionRefusedError, TimeoutError):
            if helper.poll() is not None:
                raise RuntimeError("MIDI relay could not start. Keep loopMIDI running with FL Codex port; see .local/relay.log.") from None
            time.sleep(0.05)
    raise RuntimeError("MIDI relay startup timed out; see .local/relay.log. No request was sent.")


class Client:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else ROOT / ".local" / "connection.json"

    def call(self, method, args=None):
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RuntimeError("Bridge is not installed. Run python install.py first.") from None
        if self.config_path.resolve() == (ROOT / ".local" / "connection.json").resolve():
            try:
                ensure_relay(config)
            except (TimeoutError, socket.timeout):
                raise RuntimeError("Cannot connect to local relay; no request was sent. Check localhost access and .local/relay.log.") from None
        request_id = str(uuid.uuid4())
        payload = {"id": request_id, "token": config["token"], "method": method,
                   "args": args or {}, "expires_at": time.time() + 8}
        encoded = (json.dumps(payload, allow_nan=False) + "\n").encode("utf-8")
        sent = False
        try:
            with socket.create_connection(("127.0.0.1", config["port"]), timeout=2) as conn:
                conn.settimeout(10)
                # sendall can fail after partially sending; any attempted send is uncertain.
                sent = True
                conn.sendall(encoded)
                response = bytearray()
                while b"\n" not in response:
                    chunk = conn.recv(8192)
                    if not chunk:
                        raise RuntimeError("FL closed the connection before replying. Inspect state before retrying a write.")
                    response.extend(chunk)
                    if len(response) > 1048576:
                        raise RuntimeError("Bridge response exceeded 1 MiB")
        except ConnectionRefusedError:
            raise RuntimeError("FL bridge is offline. In FL MIDI settings, enable its input and select FL Codex Bridge; check View > Script output.") from None
        except (TimeoutError, socket.timeout):
            if not sent:
                raise RuntimeError("Cannot connect to FL bridge on localhost. Enable its MIDI controller in FL and check View > Script output; no request was sent.") from None
            raise RuntimeError("FL did not reply in time. A sent write may have happened; inspect its value before retrying.") from None
        reply = json.loads(response.split(b"\n", 1)[0])
        if reply.get("id") != request_id:
            raise RuntimeError("Bridge response id mismatch")
        if not reply.get("ok"):
            error = reply.get("error", {})
            raise RuntimeError("%s: %s" % (error.get("code"), error.get("message")))
        return reply["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=["status", "list_tracks", "list_plugins", "get_parameters", "set_parameter", "undo_last"])
    parser.add_argument("--args", default="{}", help="JSON argument object")
    parser.add_argument("--args-file", type=Path, help="Read arguments from a JSON file (avoids shell quoting)")
    parser.add_argument("--config", type=Path)
    options = parser.parse_args()
    try:
        args = json.loads(options.args_file.read_text(encoding="utf-8-sig") if options.args_file else options.args)
        print(json.dumps(Client(options.config).call(options.method, args), indent=2, ensure_ascii=False))
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
