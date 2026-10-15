"""Local TCP to MIDI relay. Networking stays outside FL's embedded Python."""
import json
import os
from pathlib import Path
import sys
import time
from midi_transport import MidiProxy

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "controller"))
from fl_codex_wire import Server


def main():
    config = json.loads((ROOT / ".local" / "connection.json").read_text(encoding="utf-8"))
    proxy = MidiProxy(config["token"])
    try:
        server = Server(proxy, config["token"], config["port"])
        try:
            (ROOT / ".local" / "relay.pid").write_text(str(os.getpid()), encoding="ascii")
            print("FL Codex MIDI relay ready on 127.0.0.1:%s" % server.port, flush=True)
            while True:
                server.poll()
                time.sleep(0.002)
        finally:
            server.close()
    finally:
        proxy.close()


if __name__ == "__main__":
    main()
