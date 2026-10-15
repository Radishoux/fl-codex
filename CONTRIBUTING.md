# Contributing

FL Codex is an experimental Windows project. Bug reports, plugin compatibility
reports and focused pull requests are welcome.

## Development setup

```powershell
git clone https://github.com/Radishoux/fl-codex.git
cd fl-codex
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/check_mcp.py
```

The automated tests use a simulated FL host and do not need FL Studio, loopMIDI
or a pairing token. GitHub Actions runs them on Windows with Python 3.11 and 3.14.
The live integration requires the setup in [README](README.md).

Read [the architecture](docs/ARCHITECTURE.md) before changing the protocol or
host interaction. Keep FL API calls on FL's event thread. Preserve freshness,
recording, range and undo guards. Add regression coverage for behavior changes
and update the documentation in the same commit. Regenerate `tool_manifest.json`
with `scripts/check_mcp.py` when tool schemas change.

## Reporting a problem

Include Windows/FL/Python versions, the affected plugin and version, steps to
reproduce, expected behavior and actual behavior. Distinguish simulated-host
test results from live FL testing. For compatibility reports, include which
parameters are exposed and whether write/readback/undo were verified on a test
project. Do not attach proprietary plugin installers, project audio, pairing
tokens, or the `.local` directory. Redact private paths from relevant log excerpts.

Current priorities are a reproducible live stock-plugin acceptance test,
third-party plugin compatibility reports and relay reconnect behavior after
MIDI device restarts. Changes to insert effects or edit notes need a separate
design because those operations are outside the current tool interface.

Contributions are distributed under the project's [MIT license](LICENSE).
