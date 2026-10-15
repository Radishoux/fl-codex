# name=FL Codex Bridge
# supportedDevices=FL Codex
"""Select FL Codex Bridge as a MIDI input's Controller type in FL Studio."""
import json
import os
import channels
import device
import general
import mixer
import plugins
import transport
import ui
from fl_codex_engine import Engine
import fl_codex_wire
from fl_codex_midi import MidiServer, PREFIX

_server = None


def OnInit():
    global _server
    OnDeInit()
    try:
        # FL executes this entry script without __file__; imported modules have it.
        config_path = os.path.join(os.path.dirname(fl_codex_wire.__file__), "connection.json")
        with open(config_path, encoding="utf-8") as stream:
            config = json.load(stream)
        engine = Engine(plugins, mixer, channels, transport, general, ui)
        _server = MidiServer(engine, config["token"], device)
        print("FL Codex Bridge ready via MIDI SysEx; output assigned: %s" % bool(device.isAssigned()))
    except Exception as exc:
        print("FL Codex Bridge could not start: %s" % exc)


def OnIdle():
    if _server is not None:
        _server.poll()


def OnMidiIn(event):
    # This dedicated virtual input must never play notes or link musical controls.
    event.handled = True
    if event.sysex and bytes(event.sysex).startswith(PREFIX):
        if _server is not None:
            _server.receive(event.sysex)


def OnProjectLoad(status):
    if _server is not None:
        _server.engine.project_load(status)


def OnDeInit():
    global _server
    if _server is not None:
        _server.close()
        _server = None
