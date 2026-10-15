"""Install the controller into FL's user-data folder. Does not change MIDI routing."""
import argparse
import ctypes
import json
from pathlib import Path
import secrets
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parent
CONTROLLER_FILES = ("device_FL_Codex.py", "fl_codex_engine.py", "fl_codex_wire.py", "fl_codex_midi.py")


def documents():
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
        if result == 0 and buffer.value:
            return Path(buffer.value)
    return Path.home() / "Documents"


def install(user_data, port=32123):
    destination = user_data.resolve() / "Settings" / "Hardware" / "FL Codex Bridge"
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    config_path = local / "connection.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config["port"] != port:
            raise RuntimeError("An installation exists on another port; retain its port when updating")
    else:
        config = {"port": port, "token": secrets.token_urlsafe(32)}
    payload = json.dumps(config, indent=2) + "\n"
    # Never silently replace a differently paired controller.
    installed_config = destination / "connection.json"
    if installed_config.exists() and json.loads(installed_config.read_text(encoding="utf-8")) != config:
        raise RuntimeError("Destination belongs to another bridge pairing; use its matching workspace")
    if destination.exists():
        backup = local / "backups" / str(time.time_ns())
        shutil.copytree(destination, backup)
    destination.mkdir(parents=True, exist_ok=True)
    for filename in CONTROLLER_FILES:
        shutil.copy2(ROOT / "controller" / filename, destination / filename)
    installed_config.write_text(payload, encoding="utf-8")
    config_path.write_text(payload, encoding="utf-8")
    (local / "installation.json").write_text(json.dumps({"controller_folder": str(destination)}, indent=2), encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-data", type=Path, default=documents() / "Image-Line" / "FL Studio",
                        help="FL's User data folder shown in Options > File settings")
    parser.add_argument("--port", type=int, choices=range(1024, 65536), default=32123, metavar="PORT")
    options = parser.parse_args()
    try:
        folder = install(options.user_data, options.port)
        print("Installed FL Codex Bridge in " + str(folder))
        print("Create a dedicated loopMIDI port named exactly FL Codex and keep loopMIDI running.")
        print("In FL Studio: F10 > MIDI > select FL Codex input > Enable > Controller type: FL Codex Bridge.")
        print("Set FL Codex input AND output to the same port number (e.g. 0); leave Send master sync off.")
        print("Then run: python bridge_client.py status")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")
