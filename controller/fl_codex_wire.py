"""Authenticated request dispatch; TCP server used only outside FL."""
import json
import time
from fl_codex_engine import BridgeError

MAX_BYTES = 262144


class Responder:
    def __init__(self, engine, token):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("A generated bridge token is required")
        self.engine, self.token = engine, token
        self.cache = {}

    def reply(self, raw):
        request_id = None
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise BridgeError("INVALID_REQUEST", "Request must be an object")
            request_id = request.get("id")
            if request.get("token") != self.token:
                raise BridgeError("UNAUTHORIZED", "Invalid bridge token")
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
                raise BridgeError("INVALID_REQUEST", "A short request id is required")
            # Cache lookup precedes expiry: a repeat reports the original outcome.
            if request_id in self.cache:
                old_raw, response = self.cache[request_id]
                if raw != old_raw:
                    raise BridgeError("ID_REUSED", "Request id already used with different content")
                return response
            deadline = request.get("expires_at")
            if type(deadline) not in (float, int) or not 0 < deadline - time.time() <= 30:
                raise BridgeError("EXPIRED", "Request expired or has an invalid deadline; no action taken")
            args = request.get("args", {})
            if not isinstance(args, dict):
                raise BridgeError("INVALID_REQUEST", "args must be an object")
            result = self.engine.dispatch(request.get("method"), args)
            body = {"id": request_id, "ok": True, "result": result}
        except BridgeError as exc:
            body = {"id": request_id, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            body = {"id": request_id, "ok": False, "error": {"code": "BRIDGE_ERROR", "message": str(exc)}}
        response = (json.dumps(body, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        # Cache failures too, including uncertain writes. Never automatically retry.
        if isinstance(request_id, str) and body.get("error", {}).get("code") not in ("UNAUTHORIZED", "ID_REUSED", "INVALID_REQUEST"):
            self.cache[request_id] = (raw, response)
            if len(self.cache) > 256:
                del self.cache[next(iter(self.cache))]
        return response


class Server(Responder):
    def __init__(self, engine, token, port=32123):
        import socket
        super().__init__(engine, token)
        self.clients = []
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.bind(("127.0.0.1", port))
            self.listener.listen(4)
            self.listener.setblocking(False)
        except Exception:
            self.listener.close()
            raise
        self.port = self.listener.getsockname()[1]

    def poll(self):
        try:
            conn, _ = self.listener.accept()
            conn.setblocking(False)
            if len(self.clients) >= 4:
                conn.close()
            else:
                self.clients.append({"socket": conn, "input": b"", "output": None, "since": time.monotonic()})
        except BlockingIOError:
            pass
        dispatched = False
        for client in list(self.clients):
            conn = client["socket"]
            try:
                if time.monotonic() - client["since"] > 10:
                    self.drop(client)
                    continue
                if client["output"] is None:
                    if b"\n" not in client["input"]:
                        chunk = conn.recv(8192)
                        if not chunk:
                            self.drop(client)
                            continue
                        client["input"] += chunk
                    if len(client["input"]) > MAX_BYTES:
                        self.drop(client)
                        continue
                    if b"\n" in client["input"] and not dispatched:
                        client["output"] = self.reply(client["input"].split(b"\n", 1)[0])
                        dispatched = True
                if client["output"] is not None:
                    sent = conn.send(client["output"][:8192])
                    client["output"] = client["output"][sent:]
                    if not client["output"]:
                        self.drop(client)
            except BlockingIOError:
                pass
            except OSError:
                self.drop(client)

    def drop(self, client):
        client["socket"].close()
        self.clients.remove(client)

    def close(self):
        for client in list(self.clients):
            self.drop(client)
        self.listener.close()
