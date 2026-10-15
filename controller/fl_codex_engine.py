"""FL API calls only: called on FL's main thread by the device's OnIdle."""
import math
import time


class BridgeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise BridgeError("INVALID_ARGUMENT", "%s must be an integer in [%s, %s]" % (name, minimum, maximum))
    return value


def normalized(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise BridgeError("INVALID_ARGUMENT", "Value must be a finite number from 0 to 1")
    return float(value)


class Engine:
    def __init__(self, plugins, mixer, channels, transport, general, ui):
        self.plugins, self.mixer, self.channels = plugins, mixer, channels
        self.transport, self.general, self.ui = transport, general, ui
        self.session = str(time.time_ns())
        self.epoch = 0
        self.sequence = 0
        self.handles = {}
        self.history = []
        self.loading = False

    def project_load(self, status):
        self.epoch += 1
        self.handles.clear()
        self.history.clear()
        self.loading = status != 100

    def status(self):
        return {"bridge_version": "0.1.0", "fl_version": str(self.ui.getVersion()),
                "session": self.session, "epoch": self.epoch,
                "selected_mixer_track": self.mixer.trackNumber(),
                "selected_channel": self.channels.selectedChannel(1, 0, 1),
                "playing": bool(self.transport.isPlaying()),
                "recording": bool(self.transport.isRecording()),
                "ready": not self.loading and bool(self.general.safeToEdit()),
                "undo_depth": len(self.history),
                "capabilities": ["discover_loaded_plugins", "read_exposed_parameters",
                                 "set_exposed_parameter", "undo_last_change"],
                "limitations": ["No plugin insertion or audio listening",
                                "Only parameters exposed by FL's scripting API",
                                "Patcher internals need exposed controls"]}

    def list_tracks(self, kind="mixer", start=0, limit=32):
        if kind not in ("mixer", "channel"):
            raise BridgeError("INVALID_ARGUMENT", "kind must be mixer or channel")
        # Exclude the dynamic Current utility track; it aliases another insert.
        total = self.mixer.trackCount() - 1 if kind == "mixer" else self.channels.channelCount(1)
        start = integer(start, "start", 0, total)
        limit = integer(limit, "limit", 1, 64)
        rows = []
        for index in range(start, min(total, start + limit)):
            if kind == "mixer":
                rows.append({"index": index, "name": self.mixer.getTrackName(index)})
            else:
                rows.append({"index": index, "name": self.channels.getChannelName(index, True),
                             "mixer_track": self.channels.getTargetFxTrack(index, True)})
        return {"kind": kind, "tracks": rows, "total": total,
                "next_start": start + limit if start + limit < total else None}

    def address(self, kind, index, slot):
        if kind == "effect":
            integer(index, "index", 0, self.mixer.trackCount() - 2)
            integer(slot, "slot", 0, 9)
        elif kind == "instrument":
            integer(index, "index", 0, self.channels.channelCount(1) - 1)
            if slot != -1:
                raise BridgeError("INVALID_ARGUMENT", "Instrument slot must be -1")
        else:
            raise BridgeError("INVALID_ARGUMENT", "kind must be effect or instrument")
        return (index, slot, kind == "instrument")

    def describe(self, kind, index, slot):
        index, slot, global_index = self.address(kind, index, slot)
        if not self.plugins.isValid(index, slot, global_index):
            raise BridgeError("NO_PLUGIN", "No accessible plugin at this address")
        return {"kind": kind, "index": index, "slot": slot,
                "name": self.plugins.getPluginName(index, slot, 0, global_index),
                "label": self.plugins.getPluginName(index, slot, 1, global_index),
                "owner": self.channels.getChannelName(index, True) if global_index else self.mixer.getTrackName(index),
                "parameter_count": self.plugins.getParamCount(index, slot, global_index)}

    def list_plugins(self, kind, index):
        slots = range(10) if kind == "effect" else [-1]
        self.address(kind, index, 0 if kind == "effect" else -1)
        rows, errors = [], []
        for slot in slots:
            try:
                rows.append(self.describe(kind, index, slot))
            except BridgeError as exc:
                if exc.code != "NO_PLUGIN":
                    raise
            except Exception as exc:
                errors.append({"slot": slot, "message": str(exc)})
        return {"plugins": rows, "errors": errors}

    def parameter(self, target, param):
        args = (param, target["index"], target["slot"], target["kind"] == "instrument")
        result = {"index": param, "name": self.plugins.getParamName(*args),
                  "value": normalized(self.plugins.getParamValue(*args))}
        try:
            result["display_value"] = self.plugins.getParamValueString(*args)
        except Exception:
            result["display_value"] = None
        return result

    def get_parameters(self, kind, index, slot=-1, start=0, limit=64):
        target = self.describe(kind, index, slot)
        total = target["parameter_count"]
        start = integer(start, "start", 0, total)
        limit = integer(limit, "limit", 1, 128)
        rows, errors = [], []
        for param in range(start, min(total, start + limit)):
            try:
                row = self.parameter(target, param)
                self.sequence += 1
                handle = "%s:%s:%s" % (self.session, self.epoch, self.sequence)
                self.handles[handle] = {"target": target.copy(), "parameter": row.copy(), "read_at": time.monotonic()}
                if len(self.handles) > 2048:
                    del self.handles[next(iter(self.handles))]
                row["handle"] = handle
                rows.append(row)
            except Exception as exc:
                errors.append({"index": param, "message": str(exc)})
        return {"plugin": target, "parameters": rows, "errors": errors,
                "next_start": start + limit if start + limit < total else None,
                "handle_lifetime_seconds": 120}

    def editable(self):
        if self.loading or not self.general.safeToEdit():
            raise BridgeError("FL_BUSY", "FL is loading or not ready for edits")
        if self.transport.isRecording():
            raise BridgeError("RECORDING", "Stop recording before changing plugin parameters")

    def check_target(self, saved):
        target = saved["target"]
        current = self.describe(target["kind"], target["index"], target["slot"])
        if current != target:
            raise BridgeError("STALE_TARGET", "Plugin or track changed; inspect its parameters again")
        row = self.parameter(target, saved["parameter"]["index"])
        if row["name"] != saved["parameter"]["name"]:
            raise BridgeError("STALE_TARGET", "Parameter layout changed; inspect again")
        return row

    def write(self, target, param, value):
        # PIM_None = 0: explicit value, independent of controller pickup settings.
        self.plugins.setParamValue(value, param, target["index"], target["slot"], 0,
                                   target["kind"] == "instrument")

    def set_parameter(self, handle, value, max_delta=0.1):
        self.editable()
        value, max_delta = normalized(value), normalized(max_delta)
        saved = self.handles.get(handle)
        if saved is None or time.monotonic() - saved["read_at"] > 120:
            raise BridgeError("STALE_HANDLE", "Read the parameter again before changing it")
        current = self.check_target(saved)
        if abs(current["value"] - saved["parameter"]["value"]) > 0.000001:
            raise BridgeError("VALUE_CHANGED", "The knob changed since inspection; read it again")
        if abs(value - current["value"]) > max_delta + 0.000001:
            raise BridgeError("DELTA_LIMIT", "Requested move exceeds max_delta; use a smaller change or an intentional larger limit")
        target = saved["target"]
        # Consume before dispatch: a failed/uncertain write cannot reuse this handle.
        del self.handles[handle]
        try:
            self.write(target, current["index"], value)
            actual = self.parameter(target, current["index"])
        except Exception as exc:
            raise BridgeError("WRITE_UNCERTAIN", "Write/readback failed; inspect the knob before retrying: %s" % exc)
        edit = {"target": target.copy(), "parameter": actual.copy(),
                "before": current["value"], "after": actual["value"]}
        self.history.append(edit)
        self.history = self.history[-100:]
        return {"plugin": target, "parameter": current["name"], "before": current["value"],
                "requested": value, "after": actual["value"], "display_value": actual["display_value"],
                "undo_depth": len(self.history)}

    def undo_last(self):
        self.editable()
        if not self.history:
            raise BridgeError("NOTHING_TO_UNDO", "No bridge changes in this project session")
        edit = self.history[-1]
        current = self.check_target(edit)
        if abs(current["value"] - edit["after"]) > 0.000001:
            raise BridgeError("VALUE_CHANGED", "Knob changed after the bridge edit; undo would overwrite another change")
        try:
            self.write(edit["target"], current["index"], edit["before"])
            actual = self.parameter(edit["target"], current["index"])
        except Exception as exc:
            raise BridgeError("WRITE_UNCERTAIN", "Undo/readback failed; inspect the knob: %s" % exc)
        if abs(actual["value"] - edit["before"]) > 0.000001:
            raise BridgeError("UNDO_MISMATCH", "Plugin did not restore the saved value; inspect it")
        self.history.pop()
        return {"parameter": current["name"], "restored": actual["value"], "undo_depth": len(self.history)}

    def dispatch(self, method, args):
        methods = {"status": self.status, "list_tracks": self.list_tracks,
                   "list_plugins": self.list_plugins, "get_parameters": self.get_parameters,
                   "set_parameter": self.set_parameter, "undo_last": self.undo_last}
        if method not in methods:
            raise BridgeError("UNKNOWN_METHOD", "Unsupported bridge method")
        if self.loading and method != "status":
            raise BridgeError("FL_BUSY", "Wait for project loading to finish")
        return methods[method](**args)
