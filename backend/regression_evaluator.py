"""
AgentEval — Automated Regression Evaluation Engine

Compares new agent evaluations against stored baselines to detect:
  - Newly failing tasks (regressions)
  - Score degradation exceeding a configured threshold
  - Changes in failure categories or test results
Generates before/after comparison reports in Markdown and JSON.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).parent.parent
REPORTS_DIR = ROOT_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

from grader import grade_task
from tasks.task_registry import load_task, TASK_IDS
from agent import MockAgent, OllamaAgent, _MOCK_SOLUTIONS
from tracing import PROMPT_VERSION


class RegressionResult:
    def __init__(
        self,
        baseline_data: Dict[str, Any],
        current_data: Dict[str, Any],
        task_comparisons: Dict[str, Dict[str, Any]],
        regressed_tasks: List[str],
        score_delta: float,
        threshold: float,
        passed: bool,
    ):
        self.baseline_data = baseline_data
        self.current_data = current_data
        self.task_comparisons = task_comparisons
        self.regressed_tasks = regressed_tasks
        self.score_delta = score_delta
        self.threshold = threshold
        self.passed = passed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluation_timestamp": datetime.utcnow().isoformat(),
            "passed": self.passed,
            "threshold": self.threshold,
            "overall_score_delta": round(self.score_delta, 2),
            "baseline_avg": self.baseline_data.get("average_score", 0),
            "current_avg": self.current_data.get("average_score", 0),
            "regressed_tasks_count": len(self.regressed_tasks),
            "regressed_tasks": self.regressed_tasks,
            "task_comparisons": self.task_comparisons,
        }

    def to_markdown(self) -> str:
        verdict = "PASSED (No regression detected)" if self.passed else "FAILED (Regression threshold exceeded)"
        verdict_badge = "**[PASSED]**" if self.passed else "**[REGRESSION DETECTED]**"

        lines = [
            "# Automated Regression Evaluation Report",
            f"\n**Verdict**: {verdict_badge} — {verdict}\n",
            f"- **Timestamp**: `{datetime.utcnow().isoformat()}`",
            f"- **Baseline**: `{self.baseline_data.get('baseline_name', 'default')}` (v{self.baseline_data.get('benchmark_version', '1.0')})",
            f"- **Baseline Avg Score**: `{self.baseline_data.get('average_score', 0):.1f}%`",
            f"- **Current Avg Score**: `{self.current_data.get('average_score', 0):.1f}%`",
            f"- **Score Delta**: `{'%+.1f' % self.score_delta}%` (Allowed degradation threshold: `{self.threshold:.1f}%`)",
            f"- **Regressed Tasks**: `{len(self.regressed_tasks)}`",
            "\n## Before / After Task Comparison\n",
            "| Task ID | Baseline Score | Current Score | Delta | Baseline Status | Current Status | Failure Category |",
            "|:---|:---:|:---:|:---:|:---:|:---:|:---|",
        ]

        for tid, comp in self.task_comparisons.items():
            b_score = comp["baseline_score"]
            c_score = comp["current_score"]
            delta = comp["score_delta"]
            delta_str = f"+{delta:.1f}%" if delta > 0 else f"{delta:.1f}%"
            b_stat = comp["baseline_status"].upper()
            c_stat = comp["current_status"].upper()
            cat = comp["current_category"] or "-"
            if comp["is_regression"]:
                status_display = f"**{c_stat} (REGRESSED)**"
            else:
                status_display = c_stat

            lines.append(f"| `{tid}` | {b_score:.1f}% | {c_score:.1f}% | `{delta_str}` | {b_stat} | {status_display} | `{cat}` |")

        if self.regressed_tasks:
            lines.append("\n## Newly Failing Tasks (Regressions)\n")
            for tid in self.regressed_tasks:
                comp = self.task_comparisons[tid]
                lines.append(f"- **`{tid}`**: Dropped from {comp['baseline_score']:.1f}% ({comp['baseline_status']}) to {comp['current_score']:.1f}% ({comp['current_status']}). Category: `{comp['current_category']}`")
                if comp.get("diagnosis"):
                    lines.append(f"  - *Root Cause*: {comp['diagnosis'].get('root_cause', 'Unknown')}")
                    lines.append(f"  - *Suggested Fix*: {comp['diagnosis'].get('suggested_fix', 'None')}")

        lines.append("\n---\n*Generated automatically by AgentEval Regression Suite.*")
        return "\n".join(lines)


def run_regression_evaluation(
    baseline_path: Path,
    model_name: str = "mock",
    threshold: float = 5.0,
    fail_on_any_regression: bool = True,
    override_solutions: Optional[Dict[str, str]] = None,
) -> RegressionResult:
    """
    Runs fixed benchmark suite, compares against stored baseline, and flags regressions.
    """
    override_solutions = override_solutions or {}

    if not baseline_path.exists():
        raise FileNotFoundError(f"Baseline file not found at: {baseline_path}")

    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline_data = json.load(f)

    baseline_tasks = baseline_data.get("tasks", {})
    task_comparisons = {}
    regressed_tasks = []
    current_scores = []

    # Run benchmark tasks
    for tid in TASK_IDS:
        task = load_task(tid)

        # Get solution (support deliberate regression override)
        if tid in override_solutions:
            solution = override_solutions[tid]
        elif model_name == "mock":
            solution = _MOCK_SOLUTIONS.get(tid, "")
        else:
            agent = OllamaAgent(model_name)
            solution = agent.solve(task)

        # Grade candidate
        grade = grade_task(tid, solution, task)
        curr_score = grade["final_score"]
        curr_status = "passed" if curr_score >= 70 else "failed"
        curr_category = grade.get("failure_category")
        current_scores.append(curr_score)

        b_entry = baseline_tasks.get(tid, {"score": 0.0, "status": "failed", "category": None})
        b_score = b_entry["score"]
        b_status = b_entry["status"]

        delta = round(curr_score - b_score, 1)

        # A task is a regression if it previously passed and now fails, or lost significant score
        is_regression = (b_status == "passed" and curr_status == "failed") or (delta <= -threshold)
        if is_regression:
            regressed_tasks.append(tid)

        task_comparisons[tid] = {
            "baseline_score": b_score,
            "current_score": curr_score,
            "score_delta": delta,
            "baseline_status": b_status,
            "current_status": curr_status,
            "baseline_category": b_entry.get("category"),
            "current_category": curr_category,
            "is_regression": is_regression,
            "diagnosis": grade.get("diagnosis"),
        }

    current_avg = round(sum(current_scores) / len(current_scores), 1) if current_scores else 0.0
    baseline_avg = baseline_data.get("average_score", 0.0)
    score_delta = round(current_avg - baseline_avg, 1)

    # Determine pass/fail
    passed = True
    if fail_on_any_regression and len(regressed_tasks) > 0:
        passed = False
    elif score_delta < -threshold:
        passed = False

    current_summary = {
        "average_score": current_avg,
        "pass_rate_pct": round(sum(1 for s in current_scores if s >= 70) / len(current_scores) * 100, 1),
    }

    result = RegressionResult(
        baseline_data=baseline_data,
        current_data=current_summary,
        task_comparisons=task_comparisons,
        regressed_tasks=regressed_tasks,
        score_delta=score_delta,
        threshold=threshold,
        passed=passed,
    )

    # Save reports
    md_report_path = REPORTS_DIR / "regression_report.md"
    json_report_path = REPORTS_DIR / "regression_report.json"

    with open(md_report_path, "w", encoding="utf-8") as f:
        f.write(result.to_markdown())

    with open(json_report_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2)

    return result
