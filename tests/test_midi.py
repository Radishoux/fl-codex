"""Exercise framing and real request guards without a hardware MIDI driver."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
sys.path.insert(0, str(ROOT))
from fl_codex_midi import Assembler, MidiServer, frames
from midi_transport import MidiProxy
from test_bridge import make_engine


class FakeMidi:
    def __init__(self, engine):
        self.output = []
        self.server = MidiServer(engine, "midi-test-token-" * 3,
                                 SimpleNamespace(isAssigned=lambda: True, midiOutSysex=self.output.append))

    def send(self, frame):
        self.server.receive(frame)

    def receive(self):
        self.server.poll()
        result, self.output = self.output[:], []
        # Keep the device callback bound to the current output queue.
        self.server.device.midiOutSysex = self.output.append
        return result


class MidiTests(unittest.TestCase):
    def test_chunked_reply_reordered_and_duplicates(self):
        payload = json.dumps({"name": "Guitare \u00e9", "data": "x" * 1800}).encode("ascii")
        packets = frames(payload, 1, 999)
        assembler = Assembler(1)
        self.assertIsNone(assembler.feed(packets[-1]))
        self.assertIsNone(assembler.feed(packets[-1]))
        result = None
        for packet in packets[:-1]:
            result = assembler.feed(packet)
        self.assertEqual(result, (999, payload))

    def test_wrong_direction_and_invalid_frames_ignored(self):
        assembler = Assembler(0)
        for packet in (b"garbage", frames(b"{}", 1, 1)[0], frames(b"{}", 0, 1)[0][:-1]):
            self.assertIsNone(assembler.feed(packet))
        self.assertEqual(assembler.pending, {})

    def test_partial_assemblies_are_bounded_and_expire(self):
        assembler = Assembler(0)
        for index in range(10):
            assembler.feed(frames(b"x" * 1000, 0, index)[0])
        self.assertEqual(len(assembler.pending), 4)
        for state in assembler.pending.values():
            state[0] -= 10
        self.assertEqual(assembler.feed(frames(b"{}", 0, 12)[0]), (12, b"{}"))
        self.assertEqual(assembler.pending, {})

    def test_midi_discovery_set_readback_undo_and_wrong_token(self):
        engine = make_engine()
        midi = FakeMidi(engine)
        proxy = MidiProxy("midi-test-token-" * 3, midi)
        self.assertEqual(proxy.dispatch("status", {})["fl_version"], "Test FL API")
        row = proxy.dispatch("get_parameters", {"kind": "effect", "index": 1, "slot": 0})["parameters"][0]
        self.assertEqual(proxy.dispatch("set_parameter", {"handle": row["handle"], "value": 0.333})["after"], 0.33)
        self.assertEqual(proxy.dispatch("undo_last", {})["restored"], 0.25)
        proxy.token = "wrong"
        with self.assertRaisesRegex(Exception, "Invalid bridge token"):
            proxy.dispatch("status", {})

    def test_unassigned_output_never_executes_request(self):
        engine = make_engine()
        midi = FakeMidi(engine)
        midi.server.device.isAssigned = lambda: False
        raw = json.dumps({"id": "test", "token": "midi-test-token-" * 3,
                          "expires_at": time.time() + 8, "method": "status"}).encode()
        midi.server.receive(frames(raw, 0, 1)[0])
        midi.server.poll()
        self.assertEqual(midi.server.responder.cache, {})


if __name__ == "__main__":
    unittest.main()
