"""
Tests for Failure Diagnosis and Recovery (Requirement 5)

Validates:
  1. Accurate classification into all 5 failure categories:
     - INCORRECT_IMPLEMENTATION
     - TOOL_API_ERROR
     - ENVIRONMENT_FAILURE
     - SECURITY_VIOLATION
     - REGRESSION
  2. Test scenarios with:
     - Transient tool failures (retry & recovery)
     - Incorrect first attempts (diagnosis & recovery)
     - Retry limits (max retries enforced)
  3. Measuring recovery success, unnecessary retries, and final task success.
"""
import os
import sys
import pytest
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.environ["AGENTEVAL_SANDBOX_FALLBACK"] = "1"

from failure_diagnosis import diagnose_failure, FailureCategory
from tracing import TraceRecorder
from agent_runtime import AgentExecutionRuntime, ToolExecutionError
from tasks.task_registry import load_task


# ---------------------------------------------------------------------------
# 1. Classification into all 5 Failure Categories
# ---------------------------------------------------------------------------

def test_classify_incorrect_implementation():
    """Assertion errors and edge case failures classify as INCORRECT_IMPLEMENTATION."""
    test_results = [
        {"name": "test_negative_price", "passed": False, "error": "AssertionError: Expected ValueError"},
        {"name": "test_zero_discount", "passed": True, "error": None},
    ]
    diag = diagnose_failure(
        task_id="discount",
        task_meta={"id": "discount", "category": "BUG_FIX"},
        test_results=test_results,
        raw_output="AssertionError: Expected ValueError",
    )
    assert diag.category == FailureCategory.INCORRECT_IMPLEMENTATION
    assert len(diag.failing_tests) == 1
    assert "test_negative_price" in diag.failing_tests
    assert len(diag.suggested_fix) > 0


def test_classify_tool_api_error():
    """Signature mismatch and missing arguments classify as TOOL_API_ERROR."""
    test_results = [
        {"name": "test_api_call", "passed": False, "error": "TypeError: calculate() missing 1 required positional argument"}
    ]
    diag = diagnose_failure(
        task_id="api_endpoint",
        task_meta={"id": "api_endpoint", "category": "API"},
        test_results=test_results,
        raw_output="TypeError: calculate() missing 1 required positional argument: 'price'",
    )
    assert diag.category == FailureCategory.TOOL_API_ERROR


def test_classify_environment_failure():
    """Timeouts and sandbox faults classify as ENVIRONMENT_FAILURE."""
    test_results = [
        {"name": "test_run", "passed": False, "error": "TimeoutExpired"}
    ]
    diag = diagnose_failure(
        task_id="tax",
        task_meta={"id": "tax"},
        test_results=test_results,
        execution_error="subprocess.TimeoutExpired: command timed out after 60 seconds",
    )
    assert diag.category == FailureCategory.ENVIRONMENT_FAILURE


def test_classify_security_violation():
    """Security failures (e.g. SQL injection bypass) classify as SECURITY_VIOLATION."""
    test_results = [
        {"name": "test_sql_injection_bypass", "passed": False, "error": "Admin login succeeded with ' OR 1=1 --"},
        {"name": "test_valid_login_still_works", "passed": True, "error": None},
    ]
    diag = diagnose_failure(
        task_id="sql_injection",
        task_meta={"id": "sql_injection", "tags": ["security", "sql"]},
        test_results=test_results,
        raw_output="FAILED test_sql_injection_bypass",
    )
    assert diag.category == FailureCategory.SECURITY_VIOLATION


def test_classify_regression():
    """Breaking baseline functionality while modifying code classifies as REGRESSION."""
    test_results = [
        {"name": "test_regression_no_discount_with_tax", "passed": False, "error": "Subtotal mismatch"},
        {"name": "test_new_feature", "passed": True, "error": None},
    ]
    diag = diagnose_failure(
        task_id="orders",
        task_meta={"id": "orders"},
        test_results=test_results,
        raw_output="FAILED test_regression_no_discount_with_tax",
        regression_test_names=["test_regression_no_discount_with_tax"],
    )
    assert diag.category == FailureCategory.REGRESSION
    assert diag.is_regression is True


# ---------------------------------------------------------------------------
# 2. Recovery Scenarios (Transient tool failure, Incorrect 1st attempt, Retry limit)
# ---------------------------------------------------------------------------

def test_transient_tool_failure_recovery():
    """Agent encounters a transient tool error on attempt 1 and recovers on retry."""
    events = []
    recorder = TraceRecorder(run_id="test_transient_run", task_id="discount", on_event=lambda e: events.append(e))

    runtime = AgentExecutionRuntime(
        recorder=recorder,
        model_name="mock",
        simulate_transient_failure=True,
    )

    task = load_task("discount")
    solution, metrics = runtime.execute(task)

    assert len(solution) > 0
    # Must have recorded tool error and recovery attempt
    actions = [e["action"] for e in events]
    assert any("TOOL_ERROR" in a for a in actions)
    assert any("RECOVERY: tool_retry" in a for a in actions)


def test_incorrect_first_attempt_recovery():
    """Agent produces an incorrect attempt 1, diagnoses visible test failure, and recovers on attempt 2."""
    events = []
    recorder = TraceRecorder(run_id="test_incorrect_run", task_id="discount", on_event=lambda e: events.append(e))

    runtime = AgentExecutionRuntime(
        recorder=recorder,
        model_name="mock",
        simulate_incorrect_first_attempt=True,
        max_retries=2,
    )

    task = load_task("discount")
    solution, metrics = runtime.execute(task)

    assert metrics["recovery_attempted"] is True
    assert metrics["recovery_succeeded"] is True
    assert metrics["retry_count"] == 1

    actions = [e["action"] for e in events]
    assert any("DIAGNOSIS" in a for a in actions)
    assert any("RECOVERY_ATTEMPT" in a for a in actions)
    assert any("RECOVERY_SUCCESS" in a for a in actions)


def test_retry_limit_enforced():
    """Agent stops after hitting max_retries limit without infinite looping."""
    events = []
    recorder = TraceRecorder(run_id="test_retry_limit_run", task_id="discount", on_event=lambda e: events.append(e))

    # Force failure on all attempts
    runtime = AgentExecutionRuntime(
        recorder=recorder,
        model_name="mock",
        max_retries=2,
    )
    # Monkey-patch tool_run_visible_tests to fail always
    runtime.tool_run_visible_tests = lambda code, test_code, tid: (
        False, 1, [{"name": "test_failure", "passed": False, "error": "Always fails"}], "fail", 5.0
    )

    task = load_task("discount")
    solution, metrics = runtime.execute(task)

    assert metrics["recovery_attempted"] is True
    assert metrics["recovery_succeeded"] is False
    assert metrics["retry_count"] == 1

    actions = [e["action"] for e in events]
    assert any("RETRY_LIMIT" in a for a in actions)
