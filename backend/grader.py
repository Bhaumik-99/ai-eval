"""
AgentEval — Hidden Test Grader
Runs hidden pytest tests against the agent's solution and calculates a weighted score.

Score formula:
  final = hidden*0.40 + regression*0.30 + requirements*0.20 + quality*0.10
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

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
}

# Requirements checks per task (run as simple code analysis)
FAILURE_CATEGORIES: Dict[str, str] = {
    "discount":      "EDGE_CASE",
    "tax":           "LOGIC",
    "api_endpoint":  "API",
    "sql_injection": "SECURITY",
    "db_constraint": "DATABASE",
    "orders":        "REGRESSION",
}


def _run_pytest(app_code: str, hidden_test_path: str, task_id: str) -> Tuple[List[Dict], str]:
    """Run hidden tests against app_code in an isolated temp directory."""
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


def grade_task(task_id: str, agent_code: str, task_meta: Dict) -> Dict:
    """
    Grade a task and return a full GradeResult dict.

    Returns:
        {
            visible_score, hidden_score, regression_score,
            requirements_score, quality_score, final_score,
            hidden_total, hidden_passed,
            failure_category, failure_details: [{name, passed, error}]
        }
    """
    hidden_test_file = os.path.join(HIDDEN_TESTS_DIR, f"test_{task_id}_hidden.py")

    if not os.path.exists(hidden_test_file):
        return _zero_grade(task_id, "Hidden test file not found")

    try:
        all_tests, raw_output = _run_pytest(agent_code, hidden_test_file, task_id)
    except subprocess.TimeoutExpired:
        return _zero_grade(task_id, "Timeout running hidden tests")
    except Exception as exc:
        return _zero_grade(task_id, f"Error: {exc}")

    if not all_tests:
        return _zero_grade(task_id, "No test results parsed")

    # Separate regression tests from other hidden tests
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

    final = round(
        hidden_pct * 0.40
        + regression_pct * 0.30
        + requirements_pct * 0.20
        + quality_pct * 0.10,
        1,
    )

    failed_tests = [t for t in all_tests if not t["passed"]]
    failure_category = FAILURE_CATEGORIES.get(task_id) if failed_tests else None

    return {
        "visible_score": visible_pct,
        "hidden_score": hidden_pct,
        "regression_score": regression_pct,
        "requirements_score": requirements_pct,
        "quality_score": quality_pct,
        "final_score": final,
        "hidden_total": len(all_tests),
        "hidden_passed": sum(1 for t in all_tests if t["passed"]),
        "failure_category": failure_category,
        "failure_details": all_tests,
        "raw_output": raw_output[:2000],
    }


def _code_is_runnable(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _zero_grade(task_id: str, reason: str) -> Dict:
    return {
        "visible_score": 0,
        "hidden_score": 0,
        "regression_score": 0,
        "requirements_score": 0,
        "quality_score": 0,
        "final_score": 0,
        "hidden_total": 0,
        "hidden_passed": 0,
        "failure_category": FAILURE_CATEGORIES.get(task_id),
        "failure_details": [{"name": "grader_error", "passed": False, "error": reason}],
        "raw_output": reason,
    }
