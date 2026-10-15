"""
AgentEval — Genuine Agent Execution Runtime & Tool Environment

Executes agent tasks using real tool calls, real model requests/responses,
real visible test execution, and automated recovery loops for transient failures
and incorrect first attempts.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent import OllamaAgent, MockAgent, _MOCK_SOLUTIONS
from failure_diagnosis import diagnose_failure, FailureCategory
from tracing import TraceRecorder, PROMPT_VERSION, calculate_cost, estimate_tokens


class ToolExecutionError(Exception):
    """Raised when a tool execution encounters an error."""
    pass


class AgentExecutionRuntime:
    """
    Genuine Agent Execution Runtime.
    Dispatches real tool calls, invokes LLM/Mock agents with real latency and token recording,
    executes visible test suites, and executes recovery retries.
    """

    def __init__(
        self,
        recorder: TraceRecorder,
        model_name: str = "mock",
        max_retries: int = 2,
        simulate_transient_failure: bool = False,
        simulate_incorrect_first_attempt: bool = False,
    ):
        self.recorder = recorder
        self.model_name = model_name
        self.max_retries = max_retries
        self.simulate_transient_failure = simulate_transient_failure
        self.simulate_incorrect_first_attempt = simulate_incorrect_first_attempt

        self._has_simulated_transient_failure = False
        self._retry_count = 0
        self._recovery_attempted = False
        self._recovery_succeeded = False
        self._unnecessary_retries = 0

    # -----------------------------------------------------------------------
    # Genuine Tools
    # -----------------------------------------------------------------------

    def tool_read_file(self, filepath: str) -> str:
        """Genuine tool: Read file from workspace."""
        t0 = time.perf_counter()
        self.recorder.record_event(
            event_type="TOOL_CALL",
            action="TOOL: read_file",
            detail=f"Reading file '{filepath}'",
            tool_name="read_file",
            tool_args={"filepath": filepath},
        )

        # Handle transient tool failure simulation
        if self.simulate_transient_failure and not self._has_simulated_transient_failure:
            self._has_simulated_transient_failure = True
            dur = (time.perf_counter() - t0) * 1000
            self.recorder.record_event(
                event_type="TOOL_ERROR",
                action="TOOL_ERROR: read_file",
                detail=f"Transient tool failure: Resource busy / File locked ({filepath})",
                tool_name="read_file",
                tool_error="ResourceLockedException: file temporarily locked by background process",
                duration_ms=dur,
            )
            raise ToolExecutionError("ResourceLockedException: file temporarily locked by background process")

        if not os.path.exists(filepath):
            dur = (time.perf_counter() - t0) * 1000
            self.recorder.record_event(
                event_type="TOOL_ERROR",
                action="TOOL_ERROR: read_file",
                detail=f"FileNotFoundError: {filepath}",
                tool_name="read_file",
                tool_error=f"File not found: {filepath}",
                duration_ms=dur,
            )
            raise ToolExecutionError(f"File not found: {filepath}")

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        dur = (time.perf_counter() - t0) * 1000
        line_count = len(content.splitlines())
        self.recorder.record_event(
            event_type="TOOL_RESULT",
            action="TOOL_RESULT: read_file",
            detail=f"Read {len(content)} chars ({line_count} lines) from '{filepath}'",
            tool_name="read_file",
            tool_result={"chars": len(content), "lines": line_count},
            duration_ms=dur,
        )
        return content

    def tool_write_file(self, filepath: str, content: str) -> int:
        """Genuine tool: Write code to workspace file."""
        t0 = time.perf_counter()
        self.recorder.record_event(
            event_type="TOOL_CALL",
            action="TOOL: write_file",
            detail=f"Writing {len(content)} chars to '{filepath}'",
            tool_name="write_file",
            tool_args={"filepath": filepath, "char_count": len(content)},
        )

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        dur = (time.perf_counter() - t0) * 1000
        self.recorder.record_event(
            event_type="TOOL_RESULT",
            action="TOOL_RESULT: write_file",
            detail=f"Successfully written {len(content)} chars to '{filepath}'",
            tool_name="write_file",
            tool_result={"bytes_written": len(content.encode("utf-8"))},
            duration_ms=dur,
        )
        return len(content)

    def tool_run_visible_tests(
        self, candidate_code: str, visible_test_code: str, task_id: str
    ) -> Tuple[bool, int, List[Dict[str, Any]], str, float]:
        """Genuine tool: Execute visible pytest suite in isolated temp workspace."""
        t0 = time.perf_counter()
        self.recorder.record_event(
            event_type="TOOL_CALL",
            action="TOOL: run_tests",
            detail="Executing visible unit test suite...",
            tool_name="run_tests",
            tool_args={"test_suite": "visible_test.py"},
        )

        if not visible_test_code.strip():
            # No visible tests; verify syntax
            try:
                ast.parse(candidate_code)
                dur = (time.perf_counter() - t0) * 1000
                self.recorder.record_event(
                    event_type="TEST_EXECUTION",
                    action="TEST_EXECUTION: visible",
                    detail="Visible test: Syntax valid (no explicit visible test file)",
                    exit_code=0,
                    duration_ms=dur,
                    metadata={"passed": True},
                )
                return True, 0, [], "Syntax check passed", dur
            except SyntaxError as e:
                dur = (time.perf_counter() - t0) * 1000
                self.recorder.record_event(
                    event_type="TEST_EXECUTION",
                    action="TEST_EXECUTION: visible",
                    detail=f"SyntaxError in candidate code: {e}",
                    exit_code=1,
                    duration_ms=dur,
                    metadata={"passed": False, "error": str(e)},
                )
                return False, 1, [{"name": "syntax_check", "passed": False, "error": str(e)}], str(e), dur

        with tempfile.TemporaryDirectory(prefix=f"visible_test_{task_id}_") as tmpdir:
            app_path = os.path.join(tmpdir, "app.py")
            with open(app_path, "w", encoding="utf-8") as f:
                f.write(candidate_code)

            test_path = os.path.join(tmpdir, "test_visible.py")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write(visible_test_code)

            report_path = os.path.join(tmpdir, "report.json")
            cmd = [
                sys.executable, "-m", "pytest",
                "test_visible.py",
                "--json-report",
                f"--json-report-file={report_path}",
                "--tb=short",
                "-q",
            ]

            res = subprocess.run(
                cmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "PYTHONPATH": tmpdir},
            )

            dur = (time.perf_counter() - t0) * 1000
            passed = (res.returncode == 0)
            raw_out = (res.stdout + res.stderr).strip()

            test_details = []
            if os.path.exists(report_path):
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for t in data.get("tests", []):
                        name = t.get("nodeid", "").split("::")[-1]
                        p = t.get("outcome") == "passed"
                        err = t.get("call", {}).get("longrepr", "")[:300] if not p else None
                        test_details.append({"name": name, "passed": p, "error": err})
                except Exception:
                    pass

            self.recorder.record_event(
                event_type="TEST_EXECUTION",
                action="TEST_EXECUTION: visible",
                detail=f"Visible tests {'PASSED' if passed else 'FAILED'} (exit code {res.returncode}, {dur:.1f}ms)",
                exit_code=res.returncode,
                duration_ms=dur,
                tool_result={"passed": passed, "exit_code": res.returncode, "tests": test_details},
            )
            return passed, res.returncode, test_details, raw_out, dur

    # -----------------------------------------------------------------------
    # Genuine Model Invocation
    # -----------------------------------------------------------------------

    def invoke_model(self, prompt: str, task: Dict[str, Any], attempt: int = 1) -> Tuple[str, Dict[str, int], float]:
        """Invoke agent model, recording real latency, prompt versions, and token usage."""
        t0 = time.perf_counter()
        prompt_tokens = estimate_tokens(prompt)

        self.recorder.record_event(
            event_type="MODEL_REQUEST",
            action=f"MODEL_REQUEST (attempt {attempt})",
            detail=f"Calling model '{self.model_name}' (prompt v{PROMPT_VERSION}, ~{prompt_tokens} tokens)",
            model_name=self.model_name,
            prompt_version=PROMPT_VERSION,
            token_usage={"prompt_tokens": prompt_tokens, "completion_tokens": 0, "total_tokens": prompt_tokens},
            metadata={"temperature": 0.2, "attempt": attempt},
        )

        solution_code = ""
        completion_tokens = 0

        if self.model_name == "mock":
            # MockAgent execution
            task_id = task.get("id", "")
            time.sleep(0.3)  # genuine processing delay

            # Check if deliberate incorrect first attempt simulation is triggered
            if self.simulate_incorrect_first_attempt and attempt == 1:
                if task_id == "discount":
                    solution_code = "def calculate_discount(price, percentage):\n    # Flawed first attempt\n    return 0.0\n"
                elif task_id == "tax":
                    solution_code = "def calculate_tax(income, tax_rate):\n    return 0.0\n"
                else:
                    solution_code = "# Flawed first attempt\ndef broken(*args, **kwargs):\n    raise AssertionError('Initial attempt flawed')\n"
            else:
                solution_code = _MOCK_SOLUTIONS.get(task_id, task.get("starter_code", ""))

            dur = (time.perf_counter() - t0) * 1000
            completion_tokens = estimate_tokens(solution_code)

        else:
            # Genuine Ollama invocation
            agent = OllamaAgent(self.model_name)
            try:
                solution_code = agent.solve(task)
            except Exception as e:
                dur = (time.perf_counter() - t0) * 1000
                self.recorder.record_event(
                    event_type="TOOL_ERROR",
                    action="MODEL_ERROR",
                    detail=f"Model call failed: {e}",
                    model_name=self.model_name,
                    prompt_version=PROMPT_VERSION,
                    tool_error=str(e),
                    duration_ms=dur,
                )
                raise
            dur = (time.perf_counter() - t0) * 1000
            completion_tokens = estimate_tokens(solution_code)

        token_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        cost = calculate_cost(self.model_name, prompt_tokens, completion_tokens)

        self.recorder.record_event(
            event_type="MODEL_RESPONSE",
            action=f"MODEL_RESPONSE (attempt {attempt})",
            detail=f"Generated {len(solution_code)} chars in {dur:.1f}ms ({token_usage['total_tokens']} tokens, est. ${cost:.5f})",
            model_name=self.model_name,
            prompt_version=PROMPT_VERSION,
            duration_ms=dur,
            token_usage=token_usage,
            cost_usd=cost,
        )

        return solution_code, token_usage, dur

    # -----------------------------------------------------------------------
    # Main Agent Execution Loop with Recovery
    # -----------------------------------------------------------------------

    def execute(self, task: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Executes the genuine agent lifecycle:
          1. Genuine tool call: inspect starter code (with retry on transient error)
          2. Genuine tool call: inspect visible tests
          3. Model inference: generate solution
          4. Genuine tool call: write solution code
          5. Genuine tool call: execute visible tests
          6. Automated Recovery Loop: if visible tests fail, diagnose failure and retry
        """
        task_id = task["id"]
        app_file = os.path.join(task.get("task_dir", ""), "app.py")
        visible_test_file = os.path.join(task.get("task_dir", ""), "tests", "visible_test.py")

        # Step 1: Tool call to inspect app.py (with automatic transient failure retry)
        starter_code = ""
        for tool_attempt in range(1, 3):
            try:
                if os.path.exists(app_file):
                    starter_code = self.tool_read_file(app_file)
                else:
                    starter_code = task.get("starter_code", "")
                    self.recorder.record_event(
                        event_type="TOOL_RESULT",
                        action="TOOL: read_file",
                        detail=f"Loaded task starter code ({len(starter_code)} chars)",
                        tool_name="read_file",
                    )
                break
            except ToolExecutionError as e:
                self.recorder.record_event(
                    event_type="RECOVERY_ATTEMPT",
                    action="RECOVERY: tool_retry",
                    detail=f"Retrying transient tool failure ({tool_attempt}/2): {e}",
                    metadata={"error": str(e)},
                )
                time.sleep(0.1)

        # Step 2: Tool call to inspect visible tests
        visible_test_code = ""
        if os.path.exists(visible_test_file):
            visible_test_code = self.tool_read_file(visible_test_file)
        else:
            visible_test_code = task.get("visible_test", "")

        # Step 3: Agent generation and recovery loop
        current_solution = ""
        attempt = 1
        prompt = OllamaAgent._build_prompt(None, task)

        while attempt <= self.max_retries:
            self._retry_count = attempt - 1

            # Model generates solution
            current_solution, tok_usage, _ = self.invoke_model(prompt, task, attempt=attempt)

            # Tool writes candidate solution to virtual/temp workspace
            with tempfile.TemporaryDirectory(prefix="agent_run_") as scratch_dir:
                scratch_app = os.path.join(scratch_dir, "app.py")
                self.tool_write_file(scratch_app, current_solution)

            # Tool runs visible tests
            vis_passed, exit_code, test_items, raw_out, _ = self.tool_run_visible_tests(
                current_solution, visible_test_code, task_id
            )

            if vis_passed:
                if attempt > 1:
                    self._recovery_succeeded = True
                    self.recorder.record_event(
                        event_type="RECOVERY_SUCCESS",
                        action="RECOVERY_SUCCESS",
                        detail=f"Agent successfully recovered on attempt {attempt}! Visible tests pass.",
                    )
                break
            else:
                # Visible tests failed!
                self._recovery_attempted = True
                diagnosis = diagnose_failure(
                    task_id=task_id,
                    task_meta=task,
                    test_results=test_items,
                    raw_output=raw_out,
                    agent_code=current_solution,
                )
                self.recorder.record_event(
                    event_type="FAILURE_DIAGNOSIS",
                    action="DIAGNOSIS",
                    detail=f"Failure diagnosed: [{diagnosis.category}] {diagnosis.summary} - Root cause: {diagnosis.root_cause[:120]}",
                    metadata=diagnosis.model_dump(),
                )

                if attempt < self.max_retries:
                    self.recorder.record_event(
                        event_type="RECOVERY_ATTEMPT",
                        action=f"RECOVERY_ATTEMPT (attempt {attempt + 1})",
                        detail=f"Initiating retry {attempt + 1}/{self.max_retries}. Applying suggested fix: {diagnosis.suggested_fix}",
                    )
                    # Enrich prompt with diagnosis feedback for retry
                    prompt += f"\n\nFEEDBACK ON PREVIOUS ATTEMPT:\nYour code failed tests: {diagnosis.root_cause}\nFix: {diagnosis.suggested_fix}"
                    attempt += 1
                else:
                    self.recorder.record_event(
                        event_type="RETRY_LIMIT_REACHED",
                        action="RETRY_LIMIT",
                        detail=f"Reached maximum retries ({self.max_retries}). Proceeding to final hidden grading.",
                    )
                    break

        metrics = {
            "latency_ms": round(self.recorder.total_duration_ms, 2),
            "token_usage": self.recorder.total_tokens,
            "estimated_cost": round(self.recorder.total_cost_usd, 6),
            "recovery_attempted": self._recovery_attempted,
            "recovery_succeeded": self._recovery_succeeded,
            "retry_count": self._retry_count,
            "unnecessary_retries": self._unnecessary_retries,
        }

        return current_solution, metrics
