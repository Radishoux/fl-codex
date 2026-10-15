# FL Codex architecture

## Purpose and live status

A local MIDI controller extension lets Codex inspect FL Studio and change exposed
parameters of loaded effects and instruments. The initial target is Windows
FL Studio 2025, version 25.2.5.5319. No native VST GUI is provided.

Development verification on 2026-10-15 used a loopMIDI `FL Codex` virtual port
with matching FL input/output port numbers. Real MCP calls verified FL's version,
selected mixer/channel, recording state and track discovery. The test project
had no loaded effects. Live knob acceptance is pending a stock reverb test. All 27
automated tests pass, covering guarded edits, undo, MIDI framing, relay startup
and the complete MCP/TCP/MIDI pipeline with a simulated FL host.

The public source is experimental and MIT licensed. The controller installer
detects redirected Documents folders, including OneDrive. Each installation
generates its own pairing configuration; no pairing or third-party installer
is distributed with the repository.

## Stack and why

- Python standard library and FL modules inside FL: no dependency installation
  into FL's interpreter. FL's entry script has no `__file__`; configuration is
  resolved relative to the imported helper module.
- FL MIDI SysEx API: networking sockets fail in this build's embedded Python
  with a SystemError, despite working in its standalone interpreter. The active
  implementation uses `device.midiOutSysex` and `OnMidiIn` instead.
- Official MCP Python SDK 1.x outside FL: stdio protocol, schemas and validation.
  `uv.lock` pins the dependency resolution. Codex supplies language reasoning;
  no second AI account, API key or model client is used.
- Windows WinMM via ctypes outside FL: native MIDI buffers and SysEx I/O without
  another Python dependency. Buffers stay alive until WinMM returns them.
- A local TCP relay outside FL preserves a simple CLI/MCP client interface.
  It binds only `127.0.0.1`, never an internet-facing interface.
- FL host calls stay in its event thread. `OnMidiIn` queues requests, and
  `OnIdle` dispatches at most one request and emits up to four reply frames.

## Exact commands

From the project directory:

```powershell
uv sync --locked
python install.py
python scripts/list_midi.py
python bridge_client.py status
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_mcp.py
```

The default client starts the relay hidden if needed. Keep loopMIDI running.
For foreground diagnosis, run `python relay.py` when no relay is already running.
Only one process can bind the configured TCP port. `.local/relay.log` contains
diagnostics, and `.local/relay.pid` identifies the helper. No Windows startup
service or scheduled task is installed.

Register Codex once as shown in README. `server.py` writes MCP protocol to stdout,
not a chat UI. No build step is needed: `install.py` deploys four controller files
and the pairing configuration. External Python packages stay in `.venv`.

For CLI arguments, a file avoids PowerShell quoting problems:

```powershell
'{"kind":"effect","index":1}' | Set-Content -Encoding utf8 .local/list-effects.json
python bridge_client.py list_plugins --args-file .local/list-effects.json
```

## Files and dependencies

```text
controller/device_FL_Codex.py  FL callbacks, configuration and lifecycle
controller/fl_codex_engine.py Host operations, handles, write/undo guards
controller/fl_codex_wire.py   Authenticated dispatcher + external TCP server
controller/fl_codex_midi.py   SysEx framing, bounded assembly and FL queue
bridge_client.py              CLI/RPC client and automatic relay startup
midi_transport.py             External WinMM buffers and MIDI request/reply
relay.py                      External local TCP-to-MIDI bridge
server.py                     MCP schemas and model instructions
install.py                    Controller copy, configuration and backups
scripts/list_midi.py          Read-only Windows MIDI inventory
scripts/check_mcp.py          MCP handshake/schema smoke check
tests/                        Simulated host, MIDI, TCP and MCP tests
docs/                         Handover documentation
.github/workflows/tests.yml   Windows CI on Python 3.11 and 3.14
CONTRIBUTING.md                Development and compatibility reporting
LICENSE                       MIT terms
tool_manifest.json            Generated schema, do not hand-edit
uv.lock                       Generated dependency lock, versioned
.venv/                        External Python environment, ignored
.local/                       Pairing, logs, installer and backups, ignored
```

```text
Codex -> server.py -> bridge_client.py -> TCP relay.py -> WinMM -> loopMIDI
                                                                    |
FL OnMidiIn/OnIdle -> MidiServer -> Responder -> Engine -> FL modules
```

`Engine` accepts injected FL module objects so tests can simulate the host. It
knows nothing about MCP, sockets or natural language. FL never imports ctypes,
MCP or a model SDK. The socket import is lazy and only used by the external relay.

## Data model and invariants

- Mixer/channel addresses are zero-based. Mixer 0 is Master. The dynamic Current
  utility track is excluded because it aliases another insert. Channels use
  global indexing, independent of the channel group filter.
- Effects: `kind=effect`, mixer `index`, `slot=0..9` (UI 1..10). Instruments:
  `kind=instrument`, global channel `index`, `slot=-1`.
- A plugin fingerprint holds address, original name, label, owner name and
  parameter count. It is not an instance UUID: identical replacements can be
  undetectable. Re-inspect after structural changes.
- Values and maximum deltas must be finite numbers in `[0,1]`. These coordinates
  are normalized plugin values, not dB/Hz/seconds or perceived intensity.
  Formatted display values are optional and plugin-dependent.
- Handles contain session, project epoch and sequence. Their server records hold
  the target fingerprint, parameter name/index, original value and read time.
  They expire after 120 seconds, with at most 2,048 retained.
- Writes check FL readiness, recording state, identity, parameter name, current
  value, handle lifetime and maximum delta. They consume the handle before the
  setter and return actual readback, including plugin quantization. Failed writes
  or readback return `WRITE_UNCERTAIN`; there is no automatic retry.
- Undo stores up to 100 bridge edits and checks identity/current value before
  restoring. It is not FL's global undo. It is cleared by reload/project load.
- Every project-load event clears handles/history. Loading blocks editing until
  successful completion. `general.safeToEdit()` and recording checks add guards.
- The dedicated MIDI input consumes note/CC messages; it must not replace a
  musical controller. It only processes its own token-authenticated JSON requests.

## Protocol and persisted state

The TCP request is newline-terminated UTF-8 JSON:

```json
{"id":"unique-request-id","token":"local-pairing-secret","method":"status","args":{},"expires_at":1792000000}
```

The expiry above is illustrative; clients use current epoch time plus eight
seconds. Replies echo `id` and include `ok` with `result` or an `error` containing
`code` and `message`. Allowed methods are status, list_tracks, list_plugins,
get_parameters, set_parameter and undo_last. There is no arbitrary code, shell,
file-access or FL-function execution endpoint.

The random 256-bit pairing token lives in `.local/connection.json` and the
installed controller's `connection.json`. Neither is versioned or logged. Both
the relay and FL authenticate requests. OneDrive may sync the installed pairing
file under its normal folder policy; the TCP listener itself remains local.
The installer refuses a differently paired destination and backs up matching
installations under `.local/backups/` before updates.

TCP intake allows four clients, 8 KiB reads/writes per poll, 256 KiB requests,
one dispatch per poll and a ten-second connection lifetime. MIDI exchanges are
serialized in the external relay and wait at most eight seconds for a reply.

SysEx uses `F0 7D`, signature `FLCX`, version 1, direction (request 0/reply 1),
a 14-bit message ID, 14-bit chunk index/count, at most 512 ASCII payload bytes,
and `F7`. Non-ASCII names use JSON escapes. A message is at most 256 KiB. Four
partial assemblies and four queued FL requests are permitted; assemblies expire
after eight seconds. Direction filtering prevents feedback on the single loopback
port. The JSON UUID validates replies independently of the short framing ID.

Each authenticated dispatcher caches up to 256 recent request IDs and exact
request bytes. An exact duplicate returns the old response; a reused ID with a
different payload is refused. Expired requests do not execute. A lost reply can
still mean a write happened: inspect state rather than retrying blindly.

The default client checks the relay and launches it hidden if its port is closed
or times out. Concurrent starters converge on the TCP bind; losing processes
close their MIDI handles. Custom `FL_CODEX_CONFIG` configurations do not autostart
the default relay. Restart the matching relay process after a MIDI driver/port
restart if its handles no longer work. Never terminate an unrelated Python process.

## One end-to-end request

For "a little more reverb on Guitar":

1. Codex reads status and discovers the intended track by selection/name/routing.
2. It reads loaded effects, then all relevant reverb parameter pages.
3. It identifies the actual wet/mix parameter and current/display values. Missing
   effects or ambiguous targets are reported instead of guessed.
4. It sends a fresh handle and small requested value through MCP and local TCP.
5. The relay authenticates and frames the request as SysEx. FL queues it on
   OnMidiIn, validates it during OnIdle and calls `plugins.setParamValue`.
6. Readback and undo state return through MIDI/TCP/MCP. Codex reports the actual
   change. No project file is automatically saved and no audio is listened to.

## Live acceptance and remaining coverage

Connection/discovery and relay automatic restart were verified against the real
FL process. A stock-plugin write/readback/undo test is still pending. Further
plugin compatibility requires individual live checks; automated fake-host tests
cannot establish support for every third-party plugin.

For live verification, use an unused mixer insert or a project copy:

1. Compare status/selection to the UI.
2. Load a stock reverb, discover its slot/parameters and compare visible wet value.
3. Make a small change, verify the visible control and API readback, then bridge undo.
4. Repeat with an actual third-party effect; record absent controls/display units.
5. Check manual-change refusal, project-load invalidation and recording refusal.
   These guards are covered by simulated-host tests; live checks remain separate.

## Gotchas and recovery

- Both FL Codex MIDI input/output must use the same FL port number, for example 0.
  The virtual port must be named exactly `FL Codex`; the relay resolves that name.
  Keep loopMIDI running and Send master sync off. No manual CC linking is needed.
- Select the script in MIDI Controller type, not a mixer effect slot.
- This version cannot insert effects, listen to audio, write notes or save projects.
  Sampler/audio-clip controls and Patcher internals may not be exposed as plugins.
- Automation or modulation may change a knob after readback. The bridge does not
  disable automation, smooth ramps or record automation clips.
- Discrete switches may need an intentional max_delta above the default 0.1.
- Multiple knob changes require multiple undo calls; there is no atomic batch.
- Do not reinstate networking inside FL: its embedded socket initialization failed.
  Entry-script `__file__` is also absent. See View > Script output for FL errors.
- Relay port collisions/errors appear in `.local/relay.log`. The current system
  targets one FL instance; two instances must not use the same virtual bridge port.
- Update with `python install.py`, then Reload script in Script output. Restart FL
  only after saving your own work if modules remain cached. Tests require localhost
  access; a sandbox may need approved host execution.
- To uninstall, unassign the controller, stop only its matching relay process,
  run `codex mcp remove fl-studio`, and remove only the controller folder recorded
  in `.local/installation.json`. Other controllers/projects are independent.

## Decisions — 2026-10-15

Chose FL's MIDI controller API for host-wide parameter access. Rejected a generic
VST effect as the starting point because it needs a separate host-control path.
The typing interface is Codex via MCP; rejected a second embedded chatbot and
custom model billing for this first version.

The initial local-socket design worked in standalone Python tests but failed in
FL's embedded interpreter. Switched to supported MIDI SysEx and an external relay.
Rejected bypassing FL's runtime restrictions. Host calls remain on the event
thread; rejected worker-thread FL calls and UI coordinates for knob control.

Chose fresh handles and bridge undo over remembered numeric parameter addresses.
Cost: reinspection after edits/timeouts, no persistent undo and imperfect identity
for identical plugin replacements.

Published the initial source under MIT with Windows CI and an explicit
experimental status. Rejected claiming universal plugin support or marking the
pending live knob test as complete. Third-party installers and local pairing
files remain excluded from the public source.

## Primary references

- [FL MIDI scripting API](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/midi_scripting.htm)
- [Patcher control activation](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/Patcher.htm)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
- [loopMIDI lifecycle](https://www.tobias-erichsen.de/software/loopmidi.html)
- [WinMM MIDIHDR](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/ns-mmeapi-midihdr)
