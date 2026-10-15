#!/usr/bin/env python3
"""
Trusted test runner — runs inside the sandbox container as root.

Security guarantees:
1. Hidden tests stay strictly in /grader/hidden_tests/ (root-only, mode 0700).
2. Hidden tests are NEVER copied into /sandbox or made accessible to candidate code.
3. Candidate code runs strictly in a worker process as UID 1000 in /sandbox.
4. Grader and candidate are completely isolated in separate security boundaries.
5. The grader interacts with candidate execution only through a controlled interface:
   - JSON-RPC over Unix domain socket /tmp/candidate_rpc.sock.
   - ASGI HTTP over /tmp/candidate_fastapi.sock for endpoint tests.
   - Database schemas bridged via shared 0666 SQLite temp files.
6. Writes structured test results to /sandbox/results.json.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time


def main():
    task_id = os.environ.get("TASK_ID", "").strip()
    if not task_id:
        _fail("TASK_ID environment variable not set")

    hidden_test_src = f"/grader/hidden_tests/test_{task_id}_hidden.py"
    if not os.path.exists(hidden_test_src):
        _fail(f"Hidden test not found for task: {task_id}")

    candidate_code = "/sandbox/app.py"
    if not os.path.exists(candidate_code):
        _fail("Candidate code not found at /sandbox/app.py")

    # Clean sandbox workspace: ensure only app.py exists
    # NEVER copy hidden tests into /sandbox!
    for name in os.listdir("/sandbox"):
        path = os.path.join("/sandbox", name)
        if name not in ("app.py", "results.json"):
            if os.path.islink(path) or os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            elif os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)

    # Ensure candidate code is owned by sandbox user (UID 1000)
    try:
        os.chown(candidate_code, 1000, 1000)
        os.chmod(candidate_code, 0o644)
    except (PermissionError, OSError):
        pass

    # Launch candidate worker process as UID 1000 in /sandbox
    worker_cmd = [sys.executable, "/opt/agenteval/candidate_worker.py", task_id]
    worker_proc = subprocess.Popen(
        worker_cmd,
        cwd="/sandbox",
        user=1000,
        group=1000,
        env={
            "HOME": "/tmp",
            "PYTHONPATH": "/sandbox",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        },
    )

    # Wait for candidate worker RPC socket to be ready
    rpc_socket = "/tmp/candidate_rpc.sock"
    sock_ready = False
    for _ in range(100):  # Wait up to 10s
        if os.path.exists(rpc_socket):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(rpc_socket)
                s.close()
                sock_ready = True
                break
            except (ConnectionRefusedError, OSError):
                pass
        if worker_proc.poll() is not None:
            break
        time.sleep(0.1)

    if not sock_ready:
        worker_proc.kill()
        _fail("Candidate worker process failed to initialize RPC socket")

    # Run pytest inside /grader with PYTHONPATH pointing to /grader/bridge
    # Pytest runs trusted hidden tests from /grader/hidden_tests/
    report_path = "/tmp/report.json"
    if os.path.exists(report_path):
        os.unlink(report_path)

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                hidden_test_src,
                "--json-report",
                f"--json-report-file={report_path}",
                "--tb=short",
                "-q",
            ],
            cwd="/grader",
            capture_output=True,
            text=True,
            timeout=120,
            env={
                "HOME": "/tmp",
                "PYTHONPATH": "/grader/bridge",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
        )
        stdout = result.stdout + result.stderr
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        _stop_worker(worker_proc)
        _cleanup_tmp()
        _write_results({
            "success": False,
            "error": "Execution timed out (120s limit)",
            "tests": [],
            "raw_output": "",
        })
        return
    except Exception as exc:
        _stop_worker(worker_proc)
        _cleanup_tmp()
        _fail(f"Failed to run pytest: {exc}")
        return

    # Stop candidate worker
    _stop_worker(worker_proc)
    _cleanup_tmp()

    # Parse JSON report
    tests = []
    if os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
        for test in report.get("tests", []):
            name = test.get("nodeid", "").split("::")[-1]
            passed = test.get("outcome") == "passed"
            error = None
            if not passed:
                call_info = test.get("call", {})
                error = (call_info.get("longrepr", "") or "")[:500] if call_info else ""
            tests.append({"name": name, "passed": passed, "error": error})
    else:
        # Fallback: parse stdout
        for line in stdout.splitlines():
            if " PASSED" in line:
                name = line.split("::")[1].split(" ")[0] if "::" in line else line.strip()
                tests.append({"name": name, "passed": True, "error": None})
            elif " FAILED" in line:
                name = line.split("::")[1].split(" ")[0] if "::" in line else line.strip()
                tests.append({"name": name, "passed": False, "error": "Test failed"})

    _write_results({
        "success": True,
        "tests": tests,
        "raw_output": stdout[:3000],
        "returncode": returncode,
    })


def _stop_worker(proc: subprocess.Popen):
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _cleanup_tmp():
    for f in glob.glob("/tmp/*.sock") + glob.glob("/tmp/*.db"):
        try:
            os.unlink(f)
        except OSError:
            pass


def _fail(msg: str):
    _write_results({"success": False, "error": msg, "tests": [], "raw_output": ""})
    sys.exit(1)


def _write_results(data: dict):
    with open("/sandbox/results.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod("/sandbox/results.json", 0o644)
    except OSError:
        pass


if __name__ == "__main__":
    main()
