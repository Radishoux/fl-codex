# FL Codex

[![Tests](https://github.com/Radishoux/fl-codex/actions/workflows/tests.yml/badge.svg)](https://github.com/Radishoux/fl-codex/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Type a request in Codex, such as **"a little more reverb on the guitar"**, and
let Codex inspect the actual FL Studio project and adjust exposed plugin knobs.

This is an **FL Studio MIDI controller extension + local MCP server** for Windows. The
extension runs inside FL, selected in **MIDI settings > Controller type**.
It is not a VST DLL to put in a mixer effect slot. Codex supplies the language
reasoning; no additional AI API account or API key is needed.

## Current status

**Experimental.** Live connection and discovery have been verified on FL Studio
25.2.5. A real plugin knob write/readback/undo acceptance test is still pending;
automated tests use a simulated host. Universal plugin compatibility is not claimed.

Implemented: live track/plugin discovery, paginated parameter inspection,
normalized parameter writes with readback, and undo of bridge changes.
Live connection verified with FL Studio 25.2.5: version, selection, tracks and
loaded-plugin discovery. Automated tests cover guarded edits and undo, MIDI
framing, and local TCP/MCP transports. Plugin knob acceptance is recorded in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Scope: loaded effects and instruments that expose parameters through FL's
scripting API. Not every visual knob is exposed. Patcher internals need exposed
controls. This version does not insert plugins, listen to audio, create notes,
or save projects. An existing distortion plugin can be adjusted; adding a new
distortion effect is not implemented.

## Windows setup

Prerequisites: Windows, FL Studio 2025 (development target: 25.2.5), Python 3.11+,
[uv](https://docs.astral.sh/uv/getting-started/installation/), Codex with its CLI,
and [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html).
The MIDI transport currently uses Windows WinMM; macOS and Linux are unsupported.

Clone the repository and open PowerShell in it:

```powershell
git clone https://github.com/Radishoux/fl-codex.git
cd fl-codex
```

1. Install Python dependencies from this directory:

   ```powershell
   uv sync --locked
   ```

2. Install our extension in FL's user-data folder:

   ```powershell
   uv run --locked python install.py
   ```

   Custom location: `uv run --locked python install.py --user-data 'D:\Music\FL Studio'`.
   The installer detects Windows' Documents folder, including OneDrive
   redirection. It copies only our controller and pairing configuration.

3. In loopMIDI, create a dedicated virtual port named exactly **`FL Codex`**.
   The relay looks up this name. Keep loopMIDI running: the virtual port exists
   only while it runs. No MIDI notes are sent by this bridge.

4. In FL Studio, press **F10 > MIDI**, refresh the device list if needed,
   select the `FL Codex` **input**, enable it, and choose **FL Codex Bridge**
   under **Controller type**. Set its input **Port** to `0`. Select `FL Codex`
   in the **Output** list and set its **Port** to the same `0`; leave **Send
   master sync** off. Use another matching number if `0` belongs to another device.
   Check **View > Script output** for `FL Codex Bridge ready via MIDI SysEx;
   output assigned: True`.
   If the new controller type is absent, refresh/reopen MIDI settings or restart
   FL after saving your own work. The installer never closes FL.

5. Verify the live connection:

   ```powershell
   uv run --locked python bridge_client.py status
   ```

   The client starts the local MIDI relay automatically when needed. Keep
   loopMIDI running. Startup errors go to `.local/relay.log`. To run the relay
   visibly for diagnosis: `uv run --locked python relay.py`
   (only one relay can own port 32123).

6. Register the MCP server with Codex (one-time, from this directory):

   ```powershell
   $flCodexRoot = (Get-Location).Path
   codex mcp add fl-studio -- "$flCodexRoot\.venv\Scripts\python.exe" "$flCodexRoot\server.py"
   ```

   Reload Codex's MCP connections or open a new chat if the new tools are not
   available in the current chat. The CLI remains usable immediately.

## Use

Name mixer tracks clearly, for example `Guitar`, `Lead vocal`, and `Drums`.
Select the target track or include its name in the instruction:

- "List the effects on Guitar and their wet/mix parameters."
- "Increase the guitar reverb wet amount slightly."
- "Add a little drive using the distortion effect already on Guitar."
- "Undo your last knob change."

Codex first reads the actual plugin and parameter names. Writes use a fresh
handle and refuse to overwrite a knob that changed after inspection. Small
requests default to a maximum normalized movement of 0.1; this is not a dB or
percentage-of-perceived-loudness scale. Changes are refused while recording.

No manual MIDI CC assignment is needed for exposed plugin parameters. MIDI SysEx
carries commands and replies; the FL script calls FL's parameter API. The virtual
input is reserved for this bridge and consumes musical MIDI messages.

## Verify / develop

```powershell
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/check_mcp.py
uv run --locked python scripts/list_midi.py
```

`check_mcp.py` regenerates `tool_manifest.json` from the real server. Tests bind
localhost sockets; a sandbox that prohibits loopback needs host permission.

The live acceptance procedure and implementation details are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and compatibility reports.

## License

[MIT](LICENSE). FL Studio, loopMIDI and other dependencies remain under their
respective licenses and are not included in this repository. This is an
independent project, not an official Image-Line or OpenAI product.
