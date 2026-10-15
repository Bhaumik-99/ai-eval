"""
AgentEval — Docker Sandbox Executor

Runs untrusted candidate code inside a disposable, non-root Docker container
with network disabled and resource limits enforced.

Security guarantees:
  - Candidate code runs as uid 1000 (non-root)
  - Network access disabled (--network=none)
  - CPU limited to 1 core
  - Memory capped at 256MB
  - PID count capped at 64 (prevents fork bombs)
  - Execution time capped at 120 seconds
  - Hidden tests baked into the image, inaccessible from /sandbox
  - No host filesystem access (only a temp dir is volume-mounted)
  - No environment variables or secrets leaked
  - Results validated by the trusted host process after container exits
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Configuration
SANDBOX_IMAGE = os.environ.get("AGENTEVAL_SANDBOX_IMAGE", "agenteval-sandbox:latest")
DOCKER_BINARY = os.environ.get("DOCKER_BINARY", "docker")

# Resource limits
SANDBOX_CPU_LIMIT = os.environ.get("SANDBOX_CPU_LIMIT", "1.0")        # CPU cores
SANDBOX_MEMORY_LIMIT = os.environ.get("SANDBOX_MEMORY_LIMIT", "256m") # Memory
SANDBOX_PIDS_LIMIT = os.environ.get("SANDBOX_PIDS_LIMIT", "64")       # Max PIDs
SANDBOX_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "120"))        # Seconds

# Path to sandbox build context (for building the image)
SANDBOX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sandbox"
)
GRADERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graders"
)


class SandboxError(Exception):
    """Raised when the sandbox execution fails in an unrecoverable way."""
    pass


def is_docker_available() -> bool:
    """Check if Docker daemon is running and accessible."""
    try:
        result = subprocess.run(
            [DOCKER_BINARY, "info"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def ensure_sandbox_image(force_rebuild: bool = False) -> bool:
    """
    Build the sandbox Docker image if it doesn't exist or rebuild is forced.
    
    The image bakes hidden tests into /grader/hidden_tests/ at build time,
    making them inaccessible to candidate code at /sandbox/.
    
    Returns True if the image is ready.
    """
    if not force_rebuild:
        # Check if image already exists
        result = subprocess.run(
            [DOCKER_BINARY, "image", "inspect", SANDBOX_IMAGE],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True

    # We need a build context with the hidden tests and runner script
    build_context = tempfile.mkdtemp(prefix="agenteval_build_")
    try:
        # Copy Dockerfile
        shutil.copy(
            os.path.join(SANDBOX_DIR, "Dockerfile.sandbox"),
            os.path.join(build_context, "Dockerfile"),
        )
        # Copy hidden tests
        hidden_src = os.path.join(GRADERS_DIR, "hidden_tests")
        hidden_dest = os.path.join(build_context, "hidden_tests")
        shutil.copytree(hidden_src, hidden_dest, ignore=shutil.ignore_patterns("__pycache__"))

        # Copy runner script
        shutil.copy(
            os.path.join(SANDBOX_DIR, "run_tests.py"),
            os.path.join(build_context, "run_tests.py"),
        )

        result = subprocess.run(
            [DOCKER_BINARY, "build", "-t", SANDBOX_IMAGE, "-f", "Dockerfile", "."],
            cwd=build_context,
            capture_output=True, text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error("Failed to build sandbox image:\n%s", result.stderr)
            raise SandboxError(f"Sandbox image build failed: {result.stderr[-500:]}")
        return True
    finally:
        shutil.rmtree(build_context, ignore_errors=True)


def run_in_sandbox(
    task_id: str,
    candidate_code: str,
) -> Tuple[List[Dict], str]:
    """
    Execute candidate code against hidden tests inside a disposable Docker container.

    Args:
        task_id: The task identifier (used to select the hidden test file).
        candidate_code: The candidate's app.py content.

    Returns:
        A tuple of (test_results_list, raw_output_string).
        Each test result is {"name": str, "passed": bool, "error": str|None}.

    Raises:
        SandboxError: If Docker is unavailable or execution fails catastrophically.
    """
    if not is_docker_available():
        raise SandboxError(
            "Docker is not available. Install Docker and ensure the daemon is running "
            "to use the secure sandbox. Set AGENTEVAL_SANDBOX_FALLBACK=1 to use the "
            "legacy (insecure) executor for development only."
        )

    # Ensure the sandbox image is built
    ensure_sandbox_image()

    # Create a temp directory for this execution
    with tempfile.TemporaryDirectory(prefix=f"agenteval_sandbox_{task_id}_") as tmpdir:
        # Write candidate code to temp dir
        app_path = os.path.join(tmpdir, "app.py")
        with open(app_path, "w", encoding="utf-8") as f:
            f.write(candidate_code)

        # Build docker run command with security constraints
        container_name = f"agenteval-{task_id}-{os.getpid()}"
        cmd = [
            DOCKER_BINARY, "run",
            "--rm",                              # Auto-remove container after exit
            "--name", container_name,
            "--network=none",                    # NO network access
            "--read-only",                       # Read-only root filesystem
            "--tmpfs", "/tmp:size=32m",           # Small writable /tmp
            "--cpus", SANDBOX_CPU_LIMIT,         # CPU limit
            "--memory", SANDBOX_MEMORY_LIMIT,    # Memory limit
            "--pids-limit", SANDBOX_PIDS_LIMIT,  # PID limit (anti-fork-bomb)
            "--security-opt", "no-new-privileges",  # Cannot escalate
            "--cap-drop", "ALL",                 # Drop all Linux capabilities
            "--cap-add", "SETUID",               # Allow root to drop to sandbox user
            "--cap-add", "SETGID",
            "-e", f"TASK_ID={task_id}",          # Only env var: which test to run
            "-v", f"{tmpdir}:/sandbox:rw",       # Mount candidate code + results
            SANDBOX_IMAGE,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SANDBOX_TIMEOUT + 30,  # Buffer beyond container timeout
            )
        except subprocess.TimeoutExpired:
            # Kill the container if it's still running
            subprocess.run(
                [DOCKER_BINARY, "kill", container_name],
                capture_output=True, timeout=10,
            )
            return [], "Execution timed out — container killed"

        # Read results from the shared volume
        results_path = os.path.join(tmpdir, "results.json")
        if not os.path.exists(results_path):
            logger.warning(
                "No results.json produced. Docker stdout: %s, stderr: %s",
                result.stdout[:500], result.stderr[:500],
            )
            return [], f"Sandbox produced no results. Output: {result.stdout[:1000]}"

        with open(results_path, encoding="utf-8") as f:
            results = json.load(f)

        # Validate results structure (trust boundary: host validates container output)
        tests = _validate_results(results)
        raw_output = results.get("raw_output", "")

        return tests, raw_output


def _validate_results(results: dict) -> List[Dict]:
    """
    Validate the results JSON produced by the container.
    
    This is the trust boundary — we don't blindly trust container output.
    We verify structure, types, and strip any unexpected fields.
    """
    if not isinstance(results, dict):
        return []

    if not results.get("success", False):
        error_msg = str(results.get("error", "Unknown sandbox error"))[:500]
        return [{"name": "sandbox_error", "passed": False, "error": error_msg}]

    validated_tests = []
    raw_tests = results.get("tests", [])

    if not isinstance(raw_tests, list):
        return [{"name": "invalid_results", "passed": False, "error": "Results format invalid"}]

    for t in raw_tests:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name", "unknown"))[:200]
        passed = bool(t.get("passed", False))
        error = None
        if not passed:
            error = str(t.get("error", ""))[:500] if t.get("error") else None

        validated_tests.append({
            "name": name,
            "passed": passed,
            "error": error,
        })

    return validated_tests


def cleanup_sandbox_containers():
    """Remove any orphaned sandbox containers (e.g., from crashes)."""
    try:
        result = subprocess.run(
            [DOCKER_BINARY, "ps", "-a", "--filter", "name=agenteval-", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10,
        )
        container_ids = result.stdout.strip().split("\n")
        for cid in container_ids:
            if cid.strip():
                subprocess.run(
                    [DOCKER_BINARY, "rm", "-f", cid.strip()],
                    capture_output=True, timeout=10,
                )
    except Exception as exc:
        logger.warning("Failed to cleanup sandbox containers: %s", exc)
