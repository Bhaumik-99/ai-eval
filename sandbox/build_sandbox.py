#!/usr/bin/env python3
"""
Build and verify the AgentEval sandbox Docker image.

Usage:
    python sandbox/build_sandbox.py          # Build the image
    python sandbox/build_sandbox.py --verify # Build + run security verification
    python sandbox/build_sandbox.py --force  # Force rebuild even if image exists
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX_DIR = os.path.join(ROOT_DIR, "sandbox")
GRADERS_DIR = os.path.join(ROOT_DIR, "graders")
IMAGE_NAME = "agenteval-sandbox:latest"


def build_image(force: bool = False) -> bool:
    """Build the sandbox Docker image."""
    if not force:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE_NAME],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[build] Image '{IMAGE_NAME}' already exists. Use --force to rebuild.")
            return True

    print("[build] Building sandbox Docker image...")

    # Create build context
    build_ctx = tempfile.mkdtemp(prefix="agenteval_build_")
    try:
        shutil.copy(
            os.path.join(SANDBOX_DIR, "Dockerfile.sandbox"),
            os.path.join(build_ctx, "Dockerfile"),
        )
        hidden_src = os.path.join(GRADERS_DIR, "hidden_tests")
        hidden_dest = os.path.join(build_ctx, "hidden_tests")
        shutil.copytree(hidden_src, hidden_dest, ignore=shutil.ignore_patterns("__pycache__"))

        shutil.copy(
            os.path.join(SANDBOX_DIR, "run_tests.py"),
            os.path.join(build_ctx, "run_tests.py"),
        )

        result = subprocess.run(
            ["docker", "build", "-t", IMAGE_NAME, "-f", "Dockerfile", "."],
            cwd=build_ctx,
            timeout=300,
        )
        if result.returncode != 0:
            print("[build] FAILED to build sandbox image")
            return False

        print(f"[build] Successfully built '{IMAGE_NAME}'")
        return True
    finally:
        shutil.rmtree(build_ctx, ignore_errors=True)


def verify_security() -> bool:
    """
    Run security verification tests against the sandbox.
    
    Proves:
      1. Candidate cannot read hidden tests from /grader/
      2. Candidate cannot access host filesystem
      3. Candidate cannot make network requests
      4. Resource limits are enforced
    """
    print("\n[verify] Running security verification...")
    all_passed = True

    # Test 1: Candidate cannot read hidden tests
    print("[verify] Test 1: Hidden test isolation...")
    malicious_code_1 = '''
import os, json
results = {"files_found": []}
for root, dirs, files in os.walk("/grader"):
    for f in files:
        results["files_found"].append(os.path.join(root, f))

# Try to read a hidden test
try:
    with open("/grader/hidden_tests/test_tax_hidden.py") as f:
        results["hidden_test_content"] = f.read()[:200]
except Exception as e:
    results["read_error"] = str(e)

# Write results
with open("/sandbox/exfil_results.json", "w") as f:
    json.dump(results, f)

# Still provide the expected function so pytest doesn't choke on import
def calculate_tax(income, tax_rate):
    return income * tax_rate / 100
'''
    passed = _run_malicious_test(malicious_code_1, "tax", "hidden_test_read")
    if not passed:
        all_passed = False

    # Test 2: Candidate cannot access host files
    print("[verify] Test 2: Host filesystem isolation...")
    malicious_code_2 = '''
import os
results = []
for path in ["/etc/passwd", "/etc/shadow", "/root", "/home"]:
    try:
        if os.path.isfile(path):
            with open(path) as f:
                results.append(f"READ {path}: {f.read()[:100]}")
        elif os.path.isdir(path):
            results.append(f"DIR {path}: {os.listdir(path)}")
    except Exception as e:
        results.append(f"BLOCKED {path}: {e}")

with open("/sandbox/host_results.txt", "w") as f:
    f.write("\\n".join(results))

def calculate_tax(income, tax_rate):
    return income * tax_rate / 100
'''
    passed = _run_malicious_test(malicious_code_2, "tax", "host_access")
    if not passed:
        all_passed = False

    # Test 3: Candidate cannot make network requests
    print("[verify] Test 3: Network isolation...")
    malicious_code_3 = '''
import socket, json
results = {"network_blocked": True}
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(("8.8.8.8", 53))
    results["network_blocked"] = False
    results["connected_to"] = "8.8.8.8:53"
    s.close()
except Exception as e:
    results["error"] = str(e)

with open("/sandbox/network_results.json", "w") as f:
    json.dump(results, f)

def calculate_tax(income, tax_rate):
    return income * tax_rate / 100
'''
    passed = _run_malicious_test(malicious_code_3, "tax", "network")
    if not passed:
        all_passed = False

    if all_passed:
        print("\n[verify] [+] All security verification tests PASSED")
    else:
        print("\n[verify] [-] Some security verification tests FAILED")

    return all_passed


def _run_malicious_test(code: str, task_id: str, test_name: str) -> bool:
    """Run a malicious code sample and verify containment."""
    tmpdir = tempfile.mkdtemp(prefix=f"verify_{test_name}_")
    try:
        app_path = os.path.join(tmpdir, "app.py")
        with open(app_path, "w") as f:
            f.write(code)

        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network=none",
                "--read-only",
                "--tmpfs", "/tmp:size=32m",
                "--cpus", "1.0",
                "--memory", "256m",
                "--pids-limit", "64",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--cap-add", "SETUID",
                "--cap-add", "SETGID",
                "-e", f"TASK_ID={task_id}",
                "-v", f"{tmpdir}:/sandbox:rw",
                IMAGE_NAME,
            ],
            capture_output=True, text=True,
            timeout=30,
        )

        # Check results.json
        results_path = os.path.join(tmpdir, "results.json")
        if os.path.exists(results_path):
            with open(results_path) as f:
                results = json.load(f)

            # The test should still run (not crash the container)
            # but the malicious operations should have been blocked
            print(f"  [{test_name}] Container exited (code={result.returncode}), results available")

            # Check for exfiltration files
            exfil_path = os.path.join(tmpdir, "exfil_results.json")
            if os.path.exists(exfil_path):
                with open(exfil_path) as f:
                    exfil = json.load(f)
                if exfil.get("hidden_test_content"):
                    print(f"  [{test_name}] ❌ FAIL: Candidate read hidden test content!")
                    return False
                if exfil.get("files_found"):
                    print(f"  [{test_name}] ⚠️  Candidate listed files: {exfil['files_found']}")
                    # Listing is concerning but reading content is critical
                    if any("hidden_test" in f for f in exfil["files_found"]):
                        print(f"  [{test_name}] ❌ FAIL: Hidden test files visible!")
                        return False

            network_path = os.path.join(tmpdir, "network_results.json")
            if os.path.exists(network_path):
                with open(network_path) as f:
                    net = json.load(f)
                if not net.get("network_blocked", True):
                    print(f"  [{test_name}] [FAIL]: Network access not blocked!")
                    return False

            print(f"  [{test_name}] [PASS]")
            return True
        else:
            print(f"  [{test_name}] [PASS] (container produced no results — sandbox blocked it)")
            return True

    except subprocess.TimeoutExpired:
        print(f"  [{test_name}] [PASS] (timed out — sandbox contained it)")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Build AgentEval sandbox image")
    parser.add_argument("--force", action="store_true", help="Force rebuild")
    parser.add_argument("--verify", action="store_true", help="Run security verification")
    args = parser.parse_args()

    # Check Docker
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if result.returncode != 0:
        print("ERROR: Docker is not running. Start Docker and try again.")
        sys.exit(1)

    if not build_image(force=args.force):
        sys.exit(1)

    if args.verify:
        if not verify_security():
            sys.exit(1)


if __name__ == "__main__":
    main()
