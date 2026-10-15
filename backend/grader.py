"""
AgentEval — Hidden Test Grader (Secure + LLM Judge)

Runs hidden pytest tests against the agent's solution inside a secure Docker
sandbox, then optionally runs an LLM judge for behavioral assessment.

Score formula (deterministic components are authoritative):
  final = hidden*0.35 + regression*0.25 + requirements*0.15 + quality*0.10 + judge*0.15

When the LLM judge is disabled or errors out, its weight is redistributed:
  final = hidden*0.40 + regression*0.30 + requirements*0.20 + quality*0.10
"""
import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

GRADERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graders")
HIDDEN_TESTS_DIR = os.path.join(GRADERS_DIR, "hidden_tests")

# Regression test names per task (subset of hidden tests that are regression tests)
REGRESSION_TESTS: Dict[str, List[str]] = {
    "discount": ["test_returns_numeric", "test_zero_discount"],
    "tax": ["test_zero_income", "test_zero_tax_rate", "test_result_rounded_two_decimals"],
    "api_endpoint": ["test_existing_endpoint_still_works"],
    "sql_injection": ["test_valid_login_still_works", "test_invalid_credentials_rejected"],
    "db_constraint": ["test_different_emails_both_succeed", "test_three_users_unique_emails"],
    "orders": ["test_regression_no_discount_with_tax", "test_regression_multiple_items_no_discount"],
    "rate_limiter": ["test_refill_recovery", "test_thread_safety_burst"],
    "jwt_auth": ["test_token_format_standard", "test_expired_token_rejected"],
    "cache_lru": ["test_miss_returns_none", "test_capacity_one"],
    "data_pipeline": ["test_sorting_by_id_ascending", "test_preserves_existing_status"],
}

# Requirements checks per task (run as simple code analysis)
FAILURE_CATEGORIES: Dict[str, str] = {
    "discount":      "EDGE_CASE",
    "tax":           "LOGIC",
    "api_endpoint":  "API",
    "sql_injection": "SECURITY",
    "db_constraint": "DATABASE",
    "orders":        "REGRESSION",
    "rate_limiter":  "CONCURRENCY",
    "jwt_auth":      "SECURITY",
    "cache_lru":      "ALGORITHMS",
    "data_pipeline": "DATA_ENGINEERING",
}

# Import failure diagnosis
try:
    from failure_diagnosis import diagnose_failure, FailureCategory
    _HAS_DIAGNOSIS = True
except ImportError:
    _HAS_DIAGNOSIS = False

# Import sandbox executor
try:
    from sandbox_executor import run_in_sandbox, is_docker_available, SandboxError
    _SANDBOX_AVAILABLE = True
except ImportError:
    _SANDBOX_AVAILABLE = False

# Import LLM judge
try:
    from llm_judge import judge_solution, JUDGE_ENABLED
    _JUDGE_AVAILABLE = True
except ImportError:
    _JUDGE_AVAILABLE = False
    JUDGE_ENABLED = False

# Allow fallback to legacy (insecure) executor for development only
_SANDBOX_FALLBACK = os.environ.get("AGENTEVAL_SANDBOX_FALLBACK", "0") == "1"



# ---------------------------------------------------------------------------
# Secure test execution (Docker sandbox)
# ---------------------------------------------------------------------------

def _run_tests_sandboxed(app_code: str, task_id: str) -> Tuple[List[Dict], str]:
    """Run hidden tests in a secure Docker sandbox container."""
    return run_in_sandbox(task_id, app_code)


# ---------------------------------------------------------------------------
# Legacy test execution (insecure — dev only)
# ---------------------------------------------------------------------------

def _run_pytest_legacy(app_code: str, hidden_test_path: str, task_id: str) -> Tuple[List[Dict], str]:
    """
    LEGACY: Run hidden tests in a temp directory (NOT sandboxed).

    WARNING: This is insecure and should only be used for local development
    when Docker is unavailable. Set AGENTEVAL_SANDBOX_FALLBACK=1 explicitly.
    """
    logger.warning(
        "Using INSECURE legacy executor for task %s. "
        "Set up Docker for production use.", task_id
    )
    with tempfile.TemporaryDirectory(prefix=f"agenteval_{task_id}_") as tmpdir:
        # Write agent solution
        app_path = os.path.join(tmpdir, "app.py")
        with open(app_path, "w", encoding="utf-8") as f:
            f.write(app_code)

        # Copy hidden test file
        test_dest = os.path.join(tmpdir, "test_hidden.py")
        shutil.copy(hidden_test_path, test_dest)

        # Run pytest with JSON report
        report_path = os.path.join(tmpdir, "report.json")
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "test_hidden.py",
                f"--json-report",
                f"--json-report-file={report_path}",
                "--tb=short",
                "-q",
            ],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": tmpdir},
        )

        stdout = result.stdout + result.stderr

        if not os.path.exists(report_path):
            # Parse from stdout as fallback
            return _parse_stdout(stdout), stdout

        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)

        tests = []
        for test in report.get("tests", []):
            name = test.get("nodeid", "").split("::")[-1]
            passed = test.get("outcome") == "passed"
            error = None
            if not passed:
                call_info = test.get("call", {})
                error = call_info.get("longrepr", "")[:300] if call_info else ""
            tests.append({"name": name, "passed": passed, "error": error})

        return tests, stdout


def _parse_stdout(stdout: str) -> List[Dict]:
    """Fallback: parse basic PASSED/FAILED from pytest stdout."""
    tests = []
    for line in stdout.splitlines():
        if " PASSED" in line:
            name = line.split("::")[1].split(" ")[0] if "::" in line else line.strip()
            tests.append({"name": name, "passed": True, "error": None})
        elif " FAILED" in line:
            name = line.split("::")[1].split(" ")[0] if "::" in line else line.strip()
            tests.append({"name": name, "passed": False, "error": "Test failed"})
    return tests


# ---------------------------------------------------------------------------
# Test runner dispatcher
# ---------------------------------------------------------------------------

def _run_tests(app_code: str, task_id: str) -> Tuple[List[Dict], str]:
    """
    Run hidden tests using the best available executor.

    Priority:
      1. Docker sandbox (secure, production-ready)
      2. Legacy temp-dir executor (insecure, dev-only, requires explicit opt-in)

    Raises SandboxError if Docker is required but unavailable.
    """
    # Try Docker sandbox first
    if _SANDBOX_AVAILABLE and is_docker_available():
        try:
            tests, out = _run_tests_sandboxed(app_code, task_id)
            if tests:
                return tests, out
            if not _SANDBOX_FALLBACK:
                return tests, out
        except Exception as e:
            if not _SANDBOX_FALLBACK:
                raise
            logger.warning("Sandbox error: %s — falling back to legacy", e)

    # Fallback to legacy executor if explicitly allowed
    if _SANDBOX_FALLBACK:
        hidden_test_file = os.path.join(HIDDEN_TESTS_DIR, f"test_{task_id}_hidden.py")
        if not os.path.exists(hidden_test_file):
            raise FileNotFoundError(f"Hidden test file not found: {hidden_test_file}")
        return _run_pytest_legacy(app_code, hidden_test_file, task_id)

    # Neither available — fail clearly
    raise SandboxError(
        "Docker sandbox is required but unavailable. Either:\n"
        "  1. Install Docker and ensure the daemon is running, OR\n"
        "  2. Set AGENTEVAL_SANDBOX_FALLBACK=1 for insecure local dev (NOT for production)"
    )


# ---------------------------------------------------------------------------
# Requirements check (static analysis — runs on host, never in sandbox)
# ---------------------------------------------------------------------------

def _check_requirements(task_id: str, code: str) -> float:
    """
    Simple code-analysis requirements score (0–100).
    Checks for obvious signs the agent addressed the task.
    """
    checks = {
        "discount": [
            ("raises ValueError", r"raise\s+ValueError"),
            ("percentage validation", r"percentage\s*[<>]|percentage.*100|percentage.*0"),
            ("price validation", r"price\s*[<>]|price.*0|price.*negative"),
        ],
        "tax": [
            ("divides by 100", r"/ 100|/100"),
            ("raises or handles negative", r"raise\s+ValueError|income\s*<\s*0"),
        ],
        "api_endpoint": [
            ("new route defined", r"@app\.get.*user.*{"),
            ("raises HTTPException", r"HTTPException|raise.*404|status_code.*404"),
        ],
        "sql_injection": [
            ("parameterized query", r"execute\(.*\?.*,|\?.*\).*cursor|cursor.*\?"),
            ("no f-string in SQL", r"(?!.*f\".*SELECT|f'.*SELECT)"),
            ("authorization check", r"role.*admin|admin.*role|requesting_user_role"),
        ],
        "db_constraint": [
            ("UNIQUE constraint in schema", r"UNIQUE|unique"),
            ("error handling on duplicate", r"IntegrityError|except.*Error|raise"),
        ],
        "orders": [
            ("divides discount by 100", r"discount_pct\s*/\s*100|/ 100"),
            ("preserves no-discount path", r"discount_code.*None|if.*discount"),
        ],
        "rate_limiter": [
            ("validates capacity", r"raise\s+ValueError|capacity\s*<=?\s*0"),
            ("uses lock", r"self\.lock|with\s+.*lock"),
        ],
        "jwt_auth": [
            ("checks exp claim", r"exp|time\.time"),
            ("verifies signature", r"hmac|sha256|compare_digest"),
        ],
        "cache_lru": [
            ("validates capacity", r"capacity\s*<=?\s*0|raise\s+ValueError"),
            ("evicts least recent", r"del\s+|pop|OrderedDict"),
        ],
        "data_pipeline": [
            ("deduplicates records", r"id|dict|seen"),
            ("validates email", r"email|@"),
        ],
    }
    task_checks = checks.get(task_id, [])
    if not task_checks:
        return 80.0

    passed = 0
    for _label, pattern in task_checks:
        if re.search(pattern, code, re.IGNORECASE | re.MULTILINE):
            passed += 1
    return round(passed / len(task_checks) * 100, 1)


def _check_quality(code: str) -> float:
    """
    Code quality score (0–100).
    Checks for docstrings, reasonable line length, no bare except.
    """
    score = 100.0
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return 0.0

    # Penalize bare except
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            score -= 20

    # Penalize very long lines
    long_lines = sum(1 for line in code.splitlines() if len(line) > 120)
    score -= min(long_lines * 5, 20)

    # Reward docstrings on functions
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    if funcs:
        with_docs = sum(1 for f in funcs if ast.get_docstring(f))
        score += (with_docs / len(funcs)) * 10 - 5

    return max(0.0, min(100.0, round(score, 1)))


# ---------------------------------------------------------------------------
# Main grading function
# ---------------------------------------------------------------------------

def grade_task(task_id: str, agent_code: str, task_meta: Dict, recorder: Optional[Any] = None) -> Dict:
    """
    Grade a task and return a full GradeResult dict.

    Execution flow:
      1. Run hidden tests in Docker sandbox (secure)
      2. Compute deterministic scores (regression, requirements, quality)
      3. Run LLM judge for behavioral assessment (if enabled)
      4. Diagnose any failures into the standard 5-category taxonomy
      5. Combine scores using weighted formula
      6. Return comprehensive result with diagnosis
    """
    # Step 1: Run hidden tests
    execution_method = "unknown"
    try:
        all_tests, raw_output = _run_tests(agent_code, task_id)
        execution_method = "sandbox" if (_SANDBOX_AVAILABLE and is_docker_available()) else "legacy"
    except subprocess.TimeoutExpired:
        return _zero_grade(task_id, "Timeout running hidden tests", task_meta=task_meta, recorder=recorder)
    except Exception as exc:
        return _zero_grade(task_id, f"Error: {exc}", task_meta=task_meta, recorder=recorder)

    if not all_tests:
        return _zero_grade(task_id, "No test results parsed", task_meta=task_meta, recorder=recorder)

    # Step 2: Separate regression tests from other hidden tests
    regression_names = set(REGRESSION_TESTS.get(task_id, []))
    regression_tests = [t for t in all_tests if t["name"] in regression_names]
    pure_hidden_tests = [t for t in all_tests if t["name"] not in regression_names]

    def pct(tests: List[Dict]) -> float:
        if not tests:
            return 100.0
        return round(sum(1 for t in tests if t["passed"]) / len(tests) * 100, 1)

    hidden_pct = pct(pure_hidden_tests)
    regression_pct = pct(regression_tests)
    requirements_pct = _check_requirements(task_id, agent_code)
    quality_pct = _check_quality(agent_code)

    # Visible score: simple syntax/import check
    visible_pct = 100.0 if _code_is_runnable(agent_code) else 0.0

    # Step 3: Run LLM judge (if available and enabled)
    judge_result = None
    judge_score = 0.0
    judge_active = False

    if _JUDGE_AVAILABLE and JUDGE_ENABLED:
        try:
            judge_result = judge_solution(
                task_id=task_id,
                candidate_code=agent_code,
                task_meta=task_meta,
                test_results=all_tests,
            )
            if judge_result.get("enabled") and not judge_result.get("error"):
                judge_score = judge_result.get("weighted_score", 0.0)
                judge_active = True
        except Exception as exc:
            logger.warning("LLM judge failed for task %s: %s", task_id, exc)
            judge_result = {"error": str(exc), "enabled": False}

    # Step 4: Compute final score
    if judge_active:
        final = round(
            hidden_pct * 0.35
            + regression_pct * 0.25
            + requirements_pct * 0.15
            + quality_pct * 0.10
            + judge_score * 0.15,
            1,
        )
    else:
        final = round(
            hidden_pct * 0.40
            + regression_pct * 0.30
            + requirements_pct * 0.20
            + quality_pct * 0.10,
            1,
        )

    failed_tests = [t for t in all_tests if not t["passed"]]
    diagnosis = None
    failure_category = None

    if failed_tests:
        if _HAS_DIAGNOSIS:
            diag_report = diagnose_failure(
                task_id=task_id,
                task_meta=task_meta,
                test_results=all_tests,
                raw_output=raw_output,
                agent_code=agent_code,
                regression_test_names=REGRESSION_TESTS.get(task_id, []),
            )
            diagnosis = diag_report.dict()
            failure_category = diag_report.category
        else:
            failure_category = FAILURE_CATEGORIES.get(task_id, "INCORRECT_IMPLEMENTATION")

    if recorder:
        recorder.record_event(
            event_type="TEST_EXECUTION",
            action="HIDDEN_TESTS",
            detail=f"Hidden tests ({execution_method}): {sum(1 for t in all_tests if t['passed'])}/{len(all_tests)} passed",
            exit_code=0 if not failed_tests else 1,
            tool_result={"passed": len(failed_tests) == 0, "passed_count": sum(1 for t in all_tests if t['passed']), "total": len(all_tests)},
            metadata={"execution_method": execution_method},
        )
        if diagnosis:
            recorder.record_event(
                event_type="FAILURE_DIAGNOSIS",
                action="DIAGNOSIS",
                detail=f"Diagnosed failure [{failure_category}]: {diagnosis.get('summary')} (root cause: {diagnosis.get('root_cause', '')[:100]})",
                metadata=diagnosis,
            )

    return {
        "visible_score": visible_pct,
        "hidden_score": hidden_pct,
        "regression_score": regression_pct,
        "requirements_score": requirements_pct,
        "quality_score": quality_pct,
        "judge_score": round(judge_score, 1),
        "final_score": final,
        "hidden_total": len(all_tests),
        "hidden_passed": sum(1 for t in all_tests if t["passed"]),
        "failure_category": failure_category,
        "failure_details": all_tests,
        "raw_output": raw_output[:2000],
        "judge_result": judge_result,
        "execution_method": execution_method,
        "diagnosis": diagnosis,
    }


def _code_is_runnable(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _zero_grade(task_id: str, reason: str, task_meta: Optional[Dict] = None, recorder: Optional[Any] = None) -> Dict:
    diag_dict = None
    cat = FAILURE_CATEGORIES.get(task_id, "ENVIRONMENT_FAILURE")
    if _HAS_DIAGNOSIS:
        diag = diagnose_failure(
            task_id=task_id,
            task_meta=task_meta or {},
            test_results=[{"name": "execution", "passed": False, "error": reason}],
            raw_output=reason,
            execution_error=reason,
        )
        cat = diag.category
        diag_dict = diag.dict()

    if recorder:
        recorder.record_event(
            event_type="FAILURE_DIAGNOSIS",
            action="DIAGNOSIS",
            detail=f"Fatal execution error: {reason}",
            metadata=diag_dict or {"error": reason},
        )

    return {
        "visible_score": 0,
        "hidden_score": 0,
        "regression_score": 0,
        "requirements_score": 0,
        "quality_score": 0,
        "judge_score": 0,
        "final_score": 0,
        "hidden_total": 0,
        "hidden_passed": 0,
        "failure_category": cat,
        "failure_details": [{"name": "grader_error", "passed": False, "error": reason}],
        "raw_output": reason,
        "judge_result": None,
        "execution_method": "none",
        "diagnosis": diag_dict,
    }

