"""Read Windows MIDI input/output names without opening or reconfiguring them."""
import ctypes
from ctypes import wintypes
import json


class MIDIINCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                ("vDriverVersion", wintypes.DWORD), ("szPname", wintypes.WCHAR * 32),
                ("dwSupport", wintypes.DWORD)]


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                ("vDriverVersion", wintypes.DWORD), ("szPname", wintypes.WCHAR * 32),
                ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD),
                ("dwSupport", wintypes.DWORD)]


def devices():
    winmm = ctypes.WinDLL("winmm")
    result = {"inputs": [], "outputs": []}
    for kind, count, function, struct in [
        ("inputs", winmm.midiInGetNumDevs, winmm.midiInGetDevCapsW, MIDIINCAPSW),
        ("outputs", winmm.midiOutGetNumDevs, winmm.midiOutGetDevCapsW, MIDIOUTCAPSW),
    ]:
        function.argtypes = [ctypes.c_size_t, ctypes.POINTER(struct), wintypes.UINT]
        function.restype = wintypes.UINT
        for index in range(count()):
            caps = struct()
            code = function(index, ctypes.byref(caps), ctypes.sizeof(caps))
            if code == 0:
                result[kind].append({"index": index, "name": caps.szPname})
    return result


if __name__ == "__main__":
    print(json.dumps(devices(), indent=2))
