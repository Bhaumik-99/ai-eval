#!/usr/bin/env python3
"""
AgentEval — Candidate Worker Process

Runs untrusted candidate code inside /sandbox strictly as UID 1000 (sandbox user).

Security guarantees:
- Executes exclusively as non-root sandbox user (UID 1000, GID 1000).
- Working directory is /sandbox.
- Has ZERO access to /grader/ (mode 0700 owned by root).
- Never accesses or sees hidden test files.
- Exposes a controlled JSON-RPC interface on Unix domain socket /tmp/candidate_rpc.sock.
- For ASGI/FastAPI tasks, starts a Uvicorn server on /tmp/candidate_fastapi.sock.
"""
import base64
import importlib
import inspect
import json
import logging
import os
import socket
import sqlite3
import sys
import threading
import time
import uuid
from typing import Any, Dict

logging.basicConfig(level=logging.INFO, format="[worker] %(message)s")
logger = logging.getLogger("candidate_worker")

RPC_SOCKET_PATH = "/tmp/candidate_rpc.sock"
FASTAPI_SOCKET_PATH = "/tmp/candidate_fastapi.sock"

# Map of instance_id -> object proxy instances
_instances: Dict[str, Any] = {}
# Map of db_path -> sqlite3.Connection
_sqlite_conns: Dict[str, sqlite3.Connection] = {}


def get_sqlite_conn(path: str) -> sqlite3.Connection:
    if path not in _sqlite_conns:
        conn = sqlite3.connect(path)
        _sqlite_conns[path] = conn
    return _sqlite_conns[path]


def deserialize_val(val: Any) -> Any:
    if isinstance(val, dict):
        type_tag = val.get("__type__")
        if type_tag == "bytes":
            return base64.b64decode(val["data"])
        elif type_tag == "sqlite_conn":
            return get_sqlite_conn(val["path"])
        elif type_tag == "tuple":
            return tuple(deserialize_val(x) for x in val["items"])
        return {k: deserialize_val(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [deserialize_val(x) for x in val]
    return val


def serialize_val(val: Any) -> Any:
    if isinstance(val, bytes):
        return {"__type__": "bytes", "data": base64.b64encode(val).decode("ascii")}
    elif isinstance(val, tuple):
        return {"__type__": "tuple", "items": [serialize_val(x) for x in val]}
    elif isinstance(val, list):
        return [serialize_val(x) for x in val]
    elif isinstance(val, dict):
        return {k: serialize_val(v) for k, v in val.items()}
    elif isinstance(val, (int, float, str, bool)) or val is None:
        return val
    return val


def handle_request(req: dict, candidate_module: Any, import_error: str) -> dict:
    action = req.get("action")
    if action == "ping":
        return {"status": "ok", "result": "pong"}

    if action == "inspect":
        if import_error:
            return {"status": "import_error", "error": import_error}
        classes = []
        functions = []
        for name in dir(candidate_module):
            if name.startswith("__") and name.endswith("__"):
                continue
            try:
                attr = getattr(candidate_module, name)
                if inspect.isclass(attr):
                    classes.append(name)
                elif callable(attr):
                    functions.append(name)
            except Exception:
                pass
        has_asgi = hasattr(candidate_module, "app")
        return {
            "status": "ok",
            "classes": classes,
            "functions": functions,
            "has_asgi_app": has_asgi,
        }

    if import_error:
        return {"status": "error", "exc_cls": "ImportError", "message": import_error}

    if action == "call":
        func_name = req["func"]
        args = [deserialize_val(a) for a in req.get("args", [])]
        kwargs = {k: deserialize_val(v) for k, v in req.get("kwargs", {}).items()}
        fn = getattr(candidate_module, func_name, None)
        if fn is None or not callable(fn):
            return {
                "status": "error",
                "exc_cls": "AttributeError",
                "message": f"Module 'app' has no function or attribute '{func_name}'",
            }
        try:
            res = fn(*args, **kwargs)
            return {"status": "ok", "result": serialize_val(res)}
        except Exception as exc:
            return {"status": "error", "exc_cls": exc.__class__.__name__, "message": str(exc)}

    if action == "instantiate":
        cls_name = req["cls"]
        args = [deserialize_val(a) for a in req.get("args", [])]
        kwargs = {k: deserialize_val(v) for k, v in req.get("kwargs", {}).items()}
        cls = getattr(candidate_module, cls_name, None)
        if cls is None or not inspect.isclass(cls):
            return {
                "status": "error",
                "exc_cls": "AttributeError",
                "message": f"Module 'app' has no class '{cls_name}'",
            }
        try:
            inst = cls(*args, **kwargs)
            obj_id = str(uuid.uuid4())
            _instances[obj_id] = inst
            return {"status": "ok", "obj_id": obj_id}
        except Exception as exc:
            return {"status": "error", "exc_cls": exc.__class__.__name__, "message": str(exc)}

    if action == "method":
        obj_id = req["obj_id"]
        method_name = req["method"]
        args = [deserialize_val(a) for a in req.get("args", [])]
        kwargs = {k: deserialize_val(v) for k, v in req.get("kwargs", {}).items()}
        inst = _instances.get(obj_id)
        if inst is None:
            return {
                "status": "error",
                "exc_cls": "RuntimeError",
                "message": f"Object instance '{obj_id}' not found",
            }
        method = getattr(inst, method_name, None)
        if method is None or not callable(method):
            return {
                "status": "error",
                "exc_cls": "AttributeError",
                "message": f"Object has no method '{method_name}'",
            }
        try:
            res = method(*args, **kwargs)
            return {"status": "ok", "result": serialize_val(res)}
        except Exception as exc:
            return {"status": "error", "exc_cls": exc.__class__.__name__, "message": str(exc)}

    if action == "del":
        obj_id = req.get("obj_id")
        _instances.pop(obj_id, None)
        return {"status": "ok"}

    return {"status": "error", "exc_cls": "ValueError", "message": f"Unknown action: {action}"}


def run_rpc_server(candidate_module: Any, import_error: str):
    if os.path.exists(RPC_SOCKET_PATH):
        try:
            os.unlink(RPC_SOCKET_PATH)
        except OSError:
            pass

    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(RPC_SOCKET_PATH)
    try:
        os.chmod(RPC_SOCKET_PATH, 0o666)
    except OSError:
        pass
    server_sock.listen(32)

    def client_worker(conn: socket.socket):
        with conn:
            f = conn.makefile("rwb")
            while True:
                line = f.readline()
                if not line:
                    break
                try:
                    req = json.loads(line.decode("utf-8"))
                    resp = handle_request(req, candidate_module, import_error)
                except Exception as e:
                    resp = {"status": "error", "exc_cls": e.__class__.__name__, "message": str(e)}
                out_line = json.dumps(resp).encode("utf-8") + b"\n"
                f.write(out_line)
                f.flush()

    while True:
        try:
            conn, _ = server_sock.accept()
            t = threading.Thread(target=client_worker, args=(conn,), daemon=True)
            t.start()
        except Exception:
            break


def main():
    sys.path.insert(0, "/sandbox")
    candidate_module = None
    import_error = None
    try:
        import app as candidate_module
    except Exception as e:
        import_error = f"{e.__class__.__name__}: {str(e)}"

    # If candidate defines an ASGI/FastAPI app, launch uvicorn bound to a Unix domain socket
    if candidate_module and hasattr(candidate_module, "app"):
        asgi_app = getattr(candidate_module, "app")
        if os.path.exists(FASTAPI_SOCKET_PATH):
            try:
                os.unlink(FASTAPI_SOCKET_PATH)
            except OSError:
                pass
        try:
            import uvicorn
            config = uvicorn.Config(asgi_app, uds=FASTAPI_SOCKET_PATH, log_level="warning")
            server = uvicorn.Server(config)
            t = threading.Thread(target=server.run, daemon=True)
            t.start()
            for _ in range(50):
                if os.path.exists(FASTAPI_SOCKET_PATH):
                    try:
                        os.chmod(FASTAPI_SOCKET_PATH, 0o666)
                    except OSError:
                        pass
                    break
                time.sleep(0.05)
        except Exception as e:
            logger.warning("Failed to start FastAPI server: %s", e)

    run_rpc_server(candidate_module, import_error)


if __name__ == "__main__":
    main()
