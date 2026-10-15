"""External-process Windows WinMM SysEx transport. Never imported by FL."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import secrets
import sys
import time
import uuid
from scripts.list_midi import devices

sys.path.insert(0, str(Path(__file__).resolve().parent / "controller"))
from fl_codex_midi import Assembler, frames


class MIDIHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD), ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_size_t), ("dwOffset", wintypes.DWORD),
                ("dwReserved", ctypes.c_size_t * 8)]


def check(code, operation):
    if code:
        raise RuntimeError("WinMM %s failed (%s); keep loopMIDI running with FL Codex port" % (operation, code))


class WinMidi:
    def __init__(self, name="FL Codex"):
        self.api = ctypes.WinDLL("winmm")
        for prefix in ("midiIn", "midiOut"):
            function = getattr(self.api, prefix + "Open")
            function.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT,
                                 ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
            function.restype = wintypes.UINT
            for suffix in ("PrepareHeader", "UnprepareHeader", "AddBuffer" if prefix == "midiIn" else "LongMsg"):
                function = getattr(self.api, prefix + suffix)
                function.argtypes = [ctypes.c_void_p, ctypes.POINTER(MIDIHDR), wintypes.UINT]
                function.restype = wintypes.UINT
            for suffix in (("Start", "Stop", "Reset", "Close") if prefix == "midiIn" else ("Reset", "Close")):
                function = getattr(self.api, prefix + suffix)
                function.argtypes = [ctypes.c_void_p]
                function.restype = wintypes.UINT
        self.input, self.output = ctypes.c_void_p(), ctypes.c_void_p()
        self.buffers, self.sending = [], []
        ports = devices()
        indices = {}
        for kind in ("inputs", "outputs"):
            matches = [port["index"] for port in ports[kind] if port["name"] == name]
            if len(matches) != 1:
                raise RuntimeError("Expected exactly one MIDI %s named %s; found %s" % (kind, name, len(matches)))
            indices[kind] = matches[0]
        try:
            check(self.api.midiInOpen(ctypes.byref(self.input), indices["inputs"], 0, 0, 0), "input open")
            check(self.api.midiOutOpen(ctypes.byref(self.output), indices["outputs"], 0, 0, 0), "output open")
            for _ in range(64):
                buffer = ctypes.create_string_buffer(1024)
                header = MIDIHDR(lpData=ctypes.addressof(buffer), dwBufferLength=1024)
                check(self.api.midiInPrepareHeader(self.input, ctypes.byref(header), ctypes.sizeof(header)), "input prepare")
                self.buffers.append((buffer, header))
                check(self.api.midiInAddBuffer(self.input, ctypes.byref(header), ctypes.sizeof(header)), "input queue")
            check(self.api.midiInStart(self.input), "input start")
        except Exception:
            self.close()
            raise

    def send(self, message):
        buffer = ctypes.create_string_buffer(message)
        header = MIDIHDR(lpData=ctypes.addressof(buffer), dwBufferLength=len(message))
        check(self.api.midiOutPrepareHeader(self.output, ctypes.byref(header), ctypes.sizeof(header)), "output prepare")
        self.sending.append((buffer, header))
        check(self.api.midiOutLongMsg(self.output, ctypes.byref(header), ctypes.sizeof(header)), "SysEx send")

    def receive(self):
        result = []
        for buffer, header in self.buffers:
            if header.dwFlags & 1:
                result.append(buffer.raw[:header.dwBytesRecorded])
                header.dwBytesRecorded = 0
                check(self.api.midiInAddBuffer(self.input, ctypes.byref(header), ctypes.sizeof(header)), "input requeue")
        for item in list(self.sending):
            if item[1].dwFlags & 1:
                check(self.api.midiOutUnprepareHeader(self.output, ctypes.byref(item[1]), ctypes.sizeof(item[1])), "output release")
                self.sending.remove(item)
        return result

    def close(self):
        if self.input.value:
            self.api.midiInStop(self.input)
            self.api.midiInReset(self.input)
            for _, header in self.buffers:
                check(self.api.midiInUnprepareHeader(self.input, ctypes.byref(header), ctypes.sizeof(header)), "input release")
            check(self.api.midiInClose(self.input), "input close")
            self.input.value = None
        if self.output.value:
            # Only the dedicated bridge port is ever opened; no musical controller.
            self.api.midiOutReset(self.output)
            for _, header in self.sending:
                check(self.api.midiOutUnprepareHeader(self.output, ctypes.byref(header), ctypes.sizeof(header)), "output release")
            check(self.api.midiOutClose(self.output), "output close")
            self.output.value = None
        self.buffers.clear()
        self.sending.clear()


class MidiProxy:
    def __init__(self, token, midi=None):
        self.token = token
        self.midi = midi if midi is not None else WinMidi()
        self.sequence = secrets.randbelow(16384)

    def dispatch(self, method, args):
        request_id = str(uuid.uuid4())
        self.sequence = (self.sequence + 1) % 16384
        raw = json.dumps({"id": request_id, "token": self.token, "method": method,
                          "args": args, "expires_at": time.time() + 8}, ensure_ascii=True, allow_nan=False).encode("ascii")
        self.midi.receive()  # Discard old/own frames before starting a new exchange.
        assembler = Assembler(1)
        for frame in frames(raw, 0, self.sequence):
            self.midi.send(frame)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            for message in self.midi.receive():
                completed = assembler.feed(message)
                if completed is None or completed[0] != self.sequence:
                    continue
                reply = json.loads(completed[1])
                if reply.get("id") != request_id:
                    continue
                if not reply.get("ok"):
                    from fl_codex_engine import BridgeError
                    error = reply.get("error", {})
                    raise BridgeError(error.get("code", "MIDI_ERROR"), error.get("message", "Unknown FL error"))
                return reply["result"]
            time.sleep(0.002)
        raise RuntimeError("FL did not answer MIDI. Match input/output port numbers and enable FL Codex Bridge. A sent write may have happened; inspect before retrying.")

    def close(self):
        self.midi.close()
