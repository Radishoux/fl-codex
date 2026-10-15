"""Bounded ASCII JSON framing over private, non-commercial MIDI SysEx."""
import time
from fl_codex_wire import Responder

PREFIX = bytes((0xF0, 0x7D)) + b"FLCX" + bytes((1,))
CHUNK = 512
MAX_MESSAGE = 262144


def pair(value):
    return bytes((value & 127, value >> 7))


def frames(raw, direction, message_id):
    if not raw or len(raw) > MAX_MESSAGE or any(value > 127 for value in raw):
        raise ValueError("MIDI payload must be bounded ASCII JSON")
    count = (len(raw) + CHUNK - 1) // CHUNK
    return [PREFIX + bytes((direction,)) + pair(message_id) + pair(seq) + pair(count)
            + raw[seq * CHUNK:(seq + 1) * CHUNK] + b"\xf7" for seq in range(count)]


class Assembler:
    def __init__(self, direction):
        self.direction = direction
        self.pending = {}

    def feed(self, message):
        raw = bytes(message)
        now = time.monotonic()
        self.pending = {key: state for key, state in self.pending.items() if now - state[0] < 8}
        if (not raw.startswith(PREFIX) or len(raw) < 16 or raw[-1] != 247
                or raw[7] != self.direction or any(value > 127 for value in raw[7:-1])):
            return None
        message_id = raw[8] + (raw[9] << 7)
        seq, count = raw[10] + (raw[11] << 7), raw[12] + (raw[13] << 7)
        chunk = raw[14:-1]
        if not 0 <= seq < count <= MAX_MESSAGE // CHUNK or not 1 <= len(chunk) <= CHUNK:
            return None
        if seq < count - 1 and len(chunk) != CHUNK:
            return None
        if message_id not in self.pending:
            if len(self.pending) >= 4:
                return None
            self.pending[message_id] = [now, count, {}]
        state = self.pending[message_id]
        if count != state[1] or (seq in state[2] and state[2][seq] != chunk):
            del self.pending[message_id]
            return None
        state[2][seq] = chunk
        if len(state[2]) == count:
            del self.pending[message_id]
            return message_id, b"".join(state[2][index] for index in range(count))
        return None


class MidiServer:
    def __init__(self, engine, token, device):
        self.engine, self.device = engine, device
        self.responder = Responder(engine, token)
        self.assembler = Assembler(0)
        self.requests, self.outgoing = [], []

    def receive(self, message):
        completed = self.assembler.feed(message)
        if completed is not None and len(self.requests) < 4:
            self.requests.append(completed)

    def poll(self):
        if not self.device.isAssigned():
            self.requests.clear()
            self.outgoing.clear()
            return
        if not self.outgoing and self.requests:
            message_id, raw = self.requests.pop(0)
            self.outgoing = frames(self.responder.reply(raw), 1, message_id)
        for _ in range(min(4, len(self.outgoing))):
            self.device.midiOutSysex(self.outgoing.pop(0))

    def close(self):
        self.requests.clear()
        self.outgoing.clear()
        self.assembler.pending.clear()
