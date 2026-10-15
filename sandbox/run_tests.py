#!/usr/bin/env python3
"""
Trusted test runner — runs inside the sandbox container.

This script runs as root (PID 1 inside the container). It:
1. Reads the candidate code from /sandbox/app.py (volume-mounted by host).
2. Copies the requested hidden test into /sandbox/test_hidden.py.
3. Drops privileges to the `sandbox` user (uid 1000).
4. Executes pytest with JSON report output.
5. Writes structured results to /sandbox/results.json.

The candidate code can only see /sandbox. The hidden test is copied at
runtime so its original location (/grader/hidden_tests/) stays unreadable.
"""
import json
import os
import pwd
import subprocess
import sys
import shutil


def drop_privileges(uid: int = 1000, gid: int = 1000):
    """Drop from root to sandbox user."""
    os.setgid(gid)
    os.setuid(uid)


def main():
    task_id = os.environ.get("TASK_ID", "")
    if not task_id:
        _fail("TASK_ID environment variable not set")

    hidden_test_src = f"/grader/hidden_tests/test_{task_id}_hidden.py"
    if not os.path.exists(hidden_test_src):
        _fail(f"Hidden test not found for task: {task_id}")

    candidate_code = "/sandbox/app.py"
    if not os.path.exists(candidate_code):
        _fail("Candidate code not found at /sandbox/app.py")

    # Copy hidden test into sandbox workspace (so pytest can find it)
    test_dest = "/sandbox/test_hidden.py"
    shutil.copy(hidden_test_src, test_dest)
    os.chmod(test_dest, 0o444)  # read-only

    # Ensure files are owned by sandbox user and have appropriate permissions
    try:
        os.chown(candidate_code, 1000, 1000)
        os.chown(test_dest, 1000, 1000)
    except (PermissionError, OSError):
        pass
    try:
        os.chmod(candidate_code, 0o644)
        os.chmod(test_dest, 0o444)
    except (PermissionError, OSError):
        pass

    # Remove any symlinks or tricks in /sandbox
    for name in os.listdir("/sandbox"):
        path = os.path.join("/sandbox", name)
        if os.path.islink(path) and name not in ("app.py", "test_hidden.py", "results.json"):
            os.unlink(path)

    report_path = "/sandbox/report.json"

    # Run pytest inside the sandbox workspace as user 1000
    run_kwargs = {}
    if os.getuid() == 0:
        run_kwargs["user"] = 1000
        run_kwargs["group"] = 1000

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "test_hidden.py",
                "--json-report",
                f"--json-report-file={report_path}",
                "--tb=short",
                "-q",
            ],
            cwd="/sandbox",
            capture_output=True,
            text=True,
            timeout=120,  # overall process timeout
            env={
                "HOME": "/tmp",
                "PYTHONPATH": "/sandbox",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                # Strip all other env vars — no secrets leak
            },
            **run_kwargs,
        )
        stdout = result.stdout + result.stderr
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        _write_results({
            "success": False,
            "error": "Execution timed out (120s limit)",
            "tests": [],
            "raw_output": "",
        })
        return
    except Exception as exc:
        _fail(f"Failed to run pytest: {exc}")

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


def _fail(msg: str):
    _write_results({"success": False, "error": msg, "tests": [], "raw_output": ""})
    sys.exit(1)


def _write_results(data: dict):
    with open("/sandbox/results.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
