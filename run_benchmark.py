#!/usr/bin/env python3
"""
AgentEval — Benchmark Quality & Comparative Reporting CLI

Proves Requirement 6:
  - Runs full benchmark suite across all 10 tasks
  - Tracks multiple stochastic runs per task (pass@1, pass@k, std)
  - Reports pass rate, per-category accuracy, latency, cost, and recovery rate
  - Compares MockAgent and OllamaAgent side-by-side using the same limits
  - Publishes exact configurations & distinguishes [MEASURED_EXECUTION] from [ILLUSTRATIVE_DEMO]

Usage:
    python run_benchmark.py
    python run_benchmark.py --runs 3
    python run_benchmark.py --compare --runs 2
"""
import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

if os.environ.get("AGENTEVAL_SANDBOX_FALLBACK") is None:
    os.environ["AGENTEVAL_SANDBOX_FALLBACK"] = "1"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from benchmark_runner import BenchmarkConfig, run_single_model_benchmark, generate_comparison_report


def main():
    parser = argparse.ArgumentParser(description="AgentEval Benchmark Runner & Reporter")
    parser.add_argument(
        "--model",
        type=str,
        default="mock",
        help="Primary model to evaluate (default: 'mock')",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Run head-to-head comparison between MockAgent and OllamaAgent (or second model)",
    )
    parser.add_argument(
        "--compare-model",
        type=str,
        default="qwen2.5-coder:7b",
        help="Secondary model to compare against when --compare is set (default: 'qwen2.5-coder:7b')",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=2,
        help="Number of stochastic evaluation runs per task (default: 2)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature (default: 0.2)",
    )
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=None,
        help="Subset of task IDs to benchmark (default: all 10 tasks)",
    )

    args = parser.parse_args()

    print("=" * 75)
    print("AGENTEVAL — BENCHMARK QUALITY & COMPARATIVE REPORTING")
    print(f"Runs per task: {args.runs} | Temperature: {args.temperature}")
    print("=" * 75)

    all_results = []

    # Run primary model
    cfg1 = BenchmarkConfig(
        model=args.model,
        runs_per_task=args.runs,
        temperature=args.temperature,
    )
    res1 = run_single_model_benchmark(cfg1, task_ids=args.tasks)
    all_results.append(res1)

    # Run comparison model if requested
    if args.compare:
        cfg2 = BenchmarkConfig(
            model=args.compare_model,
            runs_per_task=args.runs,
            temperature=args.temperature,
        )
        try:
            res2 = run_single_model_benchmark(cfg2, task_ids=args.tasks)
            all_results.append(res2)
        except Exception as e:
            print(f"[!] Warning: Comparison model '{args.compare_model}' failed or unavailable: {e}")
            print("    Proceeding with single model report.")

    # Generate Markdown and JSON reports
    md_content, md_path, json_path = generate_comparison_report(all_results)

    print("\n" + md_content)
    print("\n" + "=" * 75)
    print(f"[+] Benchmark report saved to: {md_path}")
    print(f"[+] Machine-readable data:    {json_path}")
    print("=" * 75)


if __name__ == "__main__":
    main()
