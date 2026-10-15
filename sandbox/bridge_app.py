"""
AgentEval — Trusted Bridge Application

Runs exclusively inside /grader/ as part of the trusted test process.

Security Boundary & Controlled Interface:
- Never imports /sandbox/app.py directly into the grader process.
- Pytest tests import from this module (`from app import ...`).
- Transparently proxies function calls and class instances to the candidate
  worker process (running strictly as UID 1000 in /sandbox) via JSON-RPC
  over /tmp/candidate_rpc.sock.
- Forwards ASGI/FastAPI HTTP requests across /tmp/candidate_fastapi.sock.
- Bridges SQLite connections to shared /tmp files with 0666 permissions.
"""
import base64
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import uuid
from typing import Any

RPC_SOCKET_PATH = "/tmp/candidate_rpc.sock"
FASTAPI_SOCKET_PATH = "/tmp/candidate_fastapi.sock"


class PatchedConnection(sqlite3.Connection):
    """Subclass of sqlite3.Connection to allow storing the backing database file path."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._db_path = None


_orig_connect = sqlite3.connect


def _patched_connect(*args, **kwargs):
    if args and args[0] == ":memory:":
        db_path = f"/tmp/sqlite_shared_{uuid.uuid4().hex}.db"
        kwargs["factory"] = PatchedConnection
        conn = _orig_connect(db_path, *args[1:], **kwargs)
        conn._db_path = db_path
        try:
            os.chmod(db_path, 0o666)
        except OSError:
            pass
        return conn
    return _orig_connect(*args, **kwargs)


sqlite3.connect = _patched_connect


class RpcClient:
    """Thread-safe JSON-RPC client connected to candidate worker process."""

    def __init__(self, socket_path: str = RPC_SOCKET_PATH):
        self.socket_path = socket_path
        self._lock = threading.Lock()
        self._sock = None
        self._file = None

    def _ensure_connected(self):
        if self._sock is None:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            # Retry connection for up to 5 seconds
            for _ in range(50):
                try:
                    s.connect(self.socket_path)
                    self._sock = s
                    self._file = s.makefile("rwb")
                    return
                except (ConnectionRefusedError, FileNotFoundError, OSError):
                    time.sleep(0.1)
            raise ConnectionError(f"Could not connect to candidate worker at {self.socket_path}")

    def send(self, req: dict) -> dict:
        with self._lock:
            self._ensure_connected()
            try:
                line = json.dumps(req).encode("utf-8") + b"\n"
                self._file.write(line)
                self._file.flush()
                resp_line = self._file.readline()
                if not resp_line:
                    raise ConnectionResetError("Worker closed connection")
                return json.loads(resp_line.decode("utf-8"))
            except Exception:
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                self._sock = None
                self._file = None
                raise


_client = RpcClient()


def serialize_arg(val: Any) -> Any:
    if isinstance(val, sqlite3.Connection):
        db_path = getattr(val, "_db_path", None)
        if not db_path:
            db_path = f"/tmp/sqlite_shared_{uuid.uuid4().hex}.db"
            try:
                os.chmod(db_path, 0o666)
            except OSError:
                pass
        return {"__type__": "sqlite_conn", "path": db_path}
    elif isinstance(val, bytes):
        return {"__type__": "bytes", "data": base64.b64encode(val).decode("ascii")}
    elif isinstance(val, tuple):
        return {"__type__": "tuple", "items": [serialize_arg(x) for x in val]}
    elif isinstance(val, list):
        return [serialize_arg(x) for x in val]
    elif isinstance(val, dict):
        return {k: serialize_arg(v) for k, v in val.items()}
    elif isinstance(val, (int, float, str, bool)) or val is None:
        return val
    return str(val)


def deserialize_result(val: Any) -> Any:
    if isinstance(val, dict):
        type_tag = val.get("__type__")
        if type_tag == "bytes":
            return base64.b64decode(val["data"])
        elif type_tag == "tuple":
            return tuple(deserialize_result(x) for x in val["items"])
        return {k: deserialize_result(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [deserialize_result(x) for x in val]
    return val


def _raise_error(exc_cls_name: str, message: str):
    import builtins
    cls = getattr(builtins, exc_cls_name, None)
    if cls is None:
        cls = getattr(sqlite3, exc_cls_name, None)
    if cls is None:
        cls = Exception
    raise cls(message)


def _call_function(func_name: str, *args, **kwargs):
    req = {
        "action": "call",
        "func": func_name,
        "args": [serialize_arg(a) for a in args],
        "kwargs": {k: serialize_arg(v) for k, v in kwargs.items()},
    }
    resp = _client.send(req)
    if resp.get("status") == "error":
        _raise_error(resp.get("exc_cls", "Exception"), resp.get("message", "Error"))
    return deserialize_result(resp.get("result"))


class ProxyObject:
    def __init__(self, obj_id: str):
        self._obj_id = obj_id

    def __getattr__(self, name: str):
        def method(*args, **kwargs):
            req = {
                "action": "method",
                "obj_id": self._obj_id,
                "method": name,
                "args": [serialize_arg(a) for a in args],
                "kwargs": {k: serialize_arg(v) for k, v in kwargs.items()},
            }
            resp = _client.send(req)
            if resp.get("status") == "error":
                _raise_error(resp.get("exc_cls", "Exception"), resp.get("message", "Error"))
            return deserialize_result(resp.get("result"))
        return method

    def __del__(self):
        try:
            _client.send({"action": "del", "obj_id": self._obj_id})
        except Exception:
            pass


def _create_proxy_class(class_name: str):
    class DynamicProxy(ProxyObject):
        def __init__(self, *args, **kwargs):
            req = {
                "action": "instantiate",
                "cls": class_name,
                "args": [serialize_arg(a) for a in args],
                "kwargs": {k: serialize_arg(v) for k, v in kwargs.items()},
            }
            resp = _client.send(req)
            if resp.get("status") == "error":
                _raise_error(resp.get("exc_cls", "Exception"), resp.get("message", "Error"))
            super().__init__(resp["obj_id"])
    DynamicProxy.__name__ = class_name
    return DynamicProxy


class AsgiUdsProxy:
    """ASGI application proxy forwarding requests to candidate's Uvicorn over Unix domain socket."""

    def __init__(self, socket_path: str = FASTAPI_SOCKET_PATH):
        self.socket_path = socket_path
        self._transport = None

    def _get_transport(self):
        import httpx
        if self._transport is None:
            self._transport = httpx.AsyncHTTPTransport(uds=self.socket_path)
        return self._transport

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            import httpx
            path = scope.get("path", "/")
            query = scope.get("query_string", b"").decode("latin-1")
            url = f"http://localhost{path}" + (f"?{query}" if query else "")
            method = scope["method"]
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body", False):
                    break

            headers = []
            for k, v in scope.get("headers", []):
                headers.append((k.decode("latin-1"), v.decode("latin-1")))

            async with httpx.AsyncClient(transport=self._get_transport()) as client:
                res = await client.request(method, url, headers=headers, content=body)

            await send({
                "type": "http.response.start",
                "status": res.status_code,
                "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in res.headers.items()],
            })
            await send({
                "type": "http.response.body",
                "body": res.content,
                "more_body": False,
            })


# Inspect candidate worker on import
_manifest = _client.send({"action": "inspect"})
_import_err = None

if _manifest.get("status") == "import_error":
    _import_err = _manifest.get("error")
else:
    for _fn in _manifest.get("functions", []):
        def _make_fn(f_name):
            def wrapped(*args, **kwargs):
                return _call_function(f_name, *args, **kwargs)
            wrapped.__name__ = f_name
            return wrapped
        globals()[_fn] = _make_fn(_fn)

    for _c in _manifest.get("classes", []):
        globals()[_c] = _create_proxy_class(_c)

    if _manifest.get("has_asgi_app"):
        app = AsgiUdsProxy()


def __getattr__(name: str):
    if _import_err:
        raise ImportError(f"Failed to import candidate app: {_import_err}")
    def dynamic_caller(*args, **kwargs):
        return _call_function(name, *args, **kwargs)
    return dynamic_caller
