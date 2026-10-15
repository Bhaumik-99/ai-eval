"""
AgentEval — Failure Diagnosis and Taxonomy Module

Provides deep diagnosis and classification of agent evaluation failures beyond
simple pass/fail scores. Categorizes failures according to standard benchmark taxonomy:
  1. INCORRECT_IMPLEMENTATION: Logic errors, wrong return values, unhandled edge cases
  2. TOOL_API_ERROR: Invalid tool arguments, API connection errors, unparseable outputs
  3. ENVIRONMENT_FAILURE: Timeouts, sandbox failures, missing dependencies, crash
  4. SECURITY_VIOLATION: Unsanitized queries, path traversal, sandbox escape attempt
  5. REGRESSION: Breaking previously functional baseline tests while attempting a fix
"""
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FailureCategory:
    INCORRECT_IMPLEMENTATION = "INCORRECT_IMPLEMENTATION"
    TOOL_API_ERROR = "TOOL_API_ERROR"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    REGRESSION = "REGRESSION"
    UNKNOWN = "UNKNOWN"

    ALL = [
        INCORRECT_IMPLEMENTATION,
        TOOL_API_ERROR,
        ENVIRONMENT_FAILURE,
        SECURITY_VIOLATION,
        REGRESSION,
    ]


class DiagnosisReport(BaseModel):
    category: str
    summary: str
    root_cause: str
    failing_tests: List[str] = Field(default_factory=list)
    suggested_fix: str = ""
    is_regression: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


def diagnose_failure(
    task_id: str,
    task_meta: Dict[str, Any],
    test_results: List[Dict[str, Any]],
    raw_output: str = "",
    agent_code: str = "",
    execution_error: Optional[str] = None,
    regression_test_names: Optional[List[str]] = None,
) -> DiagnosisReport:
    """
    Diagnose why an agent failed a task and classify the failure into the standard taxonomy.
    """
    regression_test_names = regression_test_names or []
    failed_tests = [t for t in test_results if not t.get("passed", False)]
    failed_test_names = [t.get("name", "unknown") for t in failed_tests]

    # 1. Check for Environment / Sandbox / Timeout Failures
    if execution_error or "Timeout" in (execution_error or "") or "TimeoutExpired" in raw_output:
        return DiagnosisReport(
            category=FailureCategory.ENVIRONMENT_FAILURE,
            summary="Execution environment timeout or container infrastructure error.",
            root_cause=f"Test runner timed out or encountered environment fault: {execution_error or 'execution timeout'}",
            failing_tests=failed_test_names,
            suggested_fix="Optimize execution time, increase sandbox timeout, or check container dependencies.",
            details={"execution_error": execution_error, "raw_snippet": raw_output[:500]},
        )

    if "ModuleNotFoundError" in raw_output or "ImportError" in raw_output or "SyntaxError" in raw_output:
        # Check if syntax error in agent solution
        if "SyntaxError" in raw_output:
            return DiagnosisReport(
                category=FailureCategory.INCORRECT_IMPLEMENTATION,
                summary="Agent produced syntactically invalid Python code.",
                root_cause="The generated solution contains a Python SyntaxError and cannot be parsed or executed.",
                failing_tests=failed_test_names,
                suggested_fix="Ensure valid Python syntax without unterminated strings or invalid block indentation.",
                details={"raw_snippet": raw_output[:400]},
            )
        return DiagnosisReport(
            category=FailureCategory.ENVIRONMENT_FAILURE,
            summary="Missing module or import error in test environment.",
            root_cause=f"Required dependency not found: {raw_output[:250]}",
            failing_tests=failed_test_names,
            suggested_fix="Install required package inside evaluation sandbox.",
        )

    # 2. Check for Security Violations
    # Specific security checks for security-related tasks like sql_injection, jwt_auth, etc.
    if task_id == "sql_injection" or "security" in task_meta.get("tags", []):
        security_fails = [
            t for t in failed_tests
            if any(k in t.get("name", "").lower() for k in ["injection", "auth", "bypass", "security", "unauthorized"])
        ]
        if security_fails:
            return DiagnosisReport(
                category=FailureCategory.SECURITY_VIOLATION,
                summary="Security vulnerability not mitigated (SQL injection or unauthorized access).",
                root_cause=f"Solution fails security tests: {', '.join(t['name'] for t in security_fails)}. Direct string interpolation or missing permission checks detected.",
                failing_tests=[t["name"] for t in security_fails],
                suggested_fix="Use parameterized database queries and verify caller authorization before returning records.",
                details={"security_failing_tests": [t["name"] for t in security_fails]},
            )

    # 3. Check for Regressions
    # Did regression tests fail while fixing the feature?
    reg_fails = [t for t in failed_tests if t.get("name") in regression_test_names]
    pure_fails = [t for t in failed_tests if t.get("name") not in regression_test_names]

    if reg_fails:
        return DiagnosisReport(
            category=FailureCategory.REGRESSION,
            summary=f"Regression introduced: {len(reg_fails)} existing baseline test(s) broken.",
            root_cause=f"Agent broke previously passing functionality: {', '.join(t['name'] for t in reg_fails)}",
            failing_tests=failed_test_names,
            is_regression=True,
            suggested_fix="Ensure existing functions maintain backward compatibility and preserve untouched behaviors.",
            details={"regression_failures": [t["name"] for t in reg_fails]},
        )

    # 4. Check for Tool / API / Schema errors
    combined_errors = (raw_output + " " + " ".join(str(t.get("error", "")) for t in failed_tests)).lower()
    if "typeerror" in combined_errors and any(k in combined_errors for k in ["takes", "unexpected keyword argument", "missing", "argument", "positional"]):
        return DiagnosisReport(
            category=FailureCategory.TOOL_API_ERROR,
            summary="Function signature or API interface mismatch.",
            root_cause="The function arguments or signature do not match the expected API specification.",
            failing_tests=failed_test_names,
            suggested_fix="Verify parameter names, positions, and default values against the task specification.",
            details={"raw_snippet": raw_output[:300]},
        )

    # 5. Default to Incorrect Implementation / Edge cases
    edge_case_keywords = ["zero", "negative", "empty", "boundary", "none", "limit", "edge"]
    is_edge_case = any(any(k in t.get("name", "").lower() for k in edge_case_keywords) for t in failed_tests)

    error_snippets = [t.get("error", "") for t in failed_tests if t.get("error")]
    summary_text = "Logic error or unhandled edge cases in implementation." if is_edge_case else "Incorrect implementation of requirements."

    return DiagnosisReport(
        category=FailureCategory.INCORRECT_IMPLEMENTATION,
        summary=summary_text,
        root_cause=f"Failed {len(failed_tests)} test(s): {', '.join(failed_test_names[:5])}. {error_snippets[0][:200] if error_snippets else ''}",
        failing_tests=failed_test_names,
        suggested_fix="Check boundary values, mathematical logic, and input validation requirements.",
        details={"failed_count": len(failed_tests), "is_edge_case": is_edge_case},
    )
