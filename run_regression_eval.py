#!/usr/bin/env python3
"""
AgentEval — Automated Regression Evaluation CLI

Runs fixed benchmark suite, compares against stored baseline,
saves reports to reports/regression_report.md and reports/regression_report.json,
and fails with non-zero exit code if regressions exceed the defined threshold.

Usage:
    python run_regression_eval.py
    python run_regression_eval.py --threshold 5.0
    python run_regression_eval.py --simulate-regression
"""
import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Ensure dev fallback is allowed if running locally without Docker daemon
if os.environ.get("AGENTEVAL_SANDBOX_FALLBACK") is None:
    os.environ["AGENTEVAL_SANDBOX_FALLBACK"] = "1"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from regression_evaluator import run_regression_evaluation


def main():
    parser = argparse.ArgumentParser(description="AgentEval Automated Regression Evaluation")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT_DIR / "benchmarks" / "baseline.json",
        help="Path to stored golden baseline JSON",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="mock",
        help="Model to evaluate (e.g. 'mock' or local Ollama model)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=5.0,
        help="Maximum allowed total score drop in points before failing workflow (default: 5.0)",
    )
    parser.add_argument(
        "--allow-task-regression",
        action="store_true",
        help="Allow individual task regressions as long as overall score is within threshold",
    )
    parser.add_argument(
        "--simulate-regression",
        action="store_true",
        help="Deliberately introduce a code/prompt regression to verify regression detection and failure reporting",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("AGENTEVAL — AUTOMATED REGRESSION EVALUATION")
    print(f"Model: {args.model} | Baseline: {args.baseline} | Threshold: {args.threshold}%")
    print("=" * 70)

    override_solutions = {}
    if args.simulate_regression:
        print("\n[!] SIMULATING DELIBERATE REGRESSION in 'discount' calculator...")
        # Deliberately introduce a broken solution that fails tests and regresses from baseline
        override_solutions["discount"] = """
def calculate_discount(price, percentage):
    # DELIBERATE REGRESSION: Broken arithmetic and missing validation
    return price * 999.0
"""

    result = run_regression_evaluation(
        baseline_path=args.baseline,
        model_name=args.model,
        threshold=args.threshold,
        fail_on_any_regression=not args.allow_task_regression,
        override_solutions=override_solutions,
    )

    print("\n" + result.to_markdown())
    print("\n" + "=" * 70)

    if result.passed:
        print("[+] SUCCESS: No regressions detected. Current build meets quality bar.")
        sys.exit(0)
    else:
        print(f"[-] FAILURE: Regression detected! {len(result.regressed_tasks)} task(s) regressed.")
        print("    Failing workflow to prevent regression merge.")
        sys.exit(1)


if __name__ == "__main__":
    main()
