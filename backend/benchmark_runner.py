"""
AgentEval — Comprehensive Benchmark Runner & Quality Reporting Engine

Executes benchmark suites across multiple stochastic runs, computes pass@k,
per-category accuracy, latency percentiles, cost, and recovery rate.
Supports head-to-head comparisons (e.g. MockAgent vs OllamaAgent) under identical
configurations, explicitly distinguishing measured execution from illustrative examples.
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).parent.parent
REPORTS_DIR = ROOT_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

from grader import grade_task
from tasks.task_registry import load_task, TASK_IDS
from agent import MockAgent, OllamaAgent, _MOCK_SOLUTIONS
from agent_runtime import AgentExecutionRuntime
from tracing import TraceRecorder, PROMPT_VERSION, calculate_cost


class BenchmarkConfig:
    def __init__(
        self,
        model: str = "mock",
        runs_per_task: int = 1,
        temperature: float = 0.2,
        max_retries: int = 2,
        timeout_sec: int = 60,
    ):
        self.model = model
        self.runs_per_task = runs_per_task
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout_sec = timeout_sec
        self.execution_type = "[ILLUSTRATIVE_DEMO]" if model == "mock" else "[MEASURED_EXECUTION]"
        self.prompt_version = PROMPT_VERSION


def _compute_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values) if len(values) > 1 else 0.0
    std = math.sqrt(variance)
    return {
        "mean": round(mean, 2),
        "std": round(std, 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def run_single_model_benchmark(
    config: BenchmarkConfig,
    task_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    task_ids = task_ids or TASK_IDS
    per_task_runs: Dict[str, List[Dict[str, Any]]] = {tid: [] for tid in task_ids}
    all_latencies = []
    total_cost = 0.0
    total_tokens = {"prompt": 0, "completion": 0, "total": 0}
    total_recovery_attempts = 0
    total_recovery_successes = 0

    print(f"[*] Running benchmark for model '{config.model}' ({config.runs_per_task} run(s) per task)...")

    for tid in task_ids:
        task = load_task(tid)
        for run_idx in range(config.runs_per_task):
            run_id = f"bench_{config.model}_{tid}_r{run_idx}_{int(time.time())}"
            recorder = TraceRecorder(run_id=run_id, task_id=tid)
            runtime = AgentExecutionRuntime(
                recorder=recorder,
                model_name=config.model,
                max_retries=config.max_retries,
            )

            t0 = time.perf_counter()
            try:
                solution, metrics = runtime.execute(task)
            except Exception as e:
                solution = ""
                metrics = {"latency_ms": (time.perf_counter() - t0) * 1000, "recovery_attempted": False, "recovery_succeeded": False, "retry_count": 0}

            # Grade candidate solution
            grade = grade_task(tid, solution, task, recorder=recorder)
            recorder.save_to_file()
            dur_ms = metrics.get("latency_ms", (time.perf_counter() - t0) * 1000)
            all_latencies.append(dur_ms)

            cost = recorder.total_cost_usd
            total_cost += cost
            total_tokens["prompt"] += recorder.total_tokens["prompt"]
            total_tokens["completion"] += recorder.total_tokens["completion"]
            total_tokens["total"] += recorder.total_tokens["total"]

            if metrics.get("recovery_attempted"):
                total_recovery_attempts += 1
                if metrics.get("recovery_succeeded"):
                    total_recovery_successes += 1

            per_task_runs[tid].append({
                "score": grade["final_score"],
                "passed": grade["final_score"] >= 70,
                "latency_ms": dur_ms,
                "category": grade.get("failure_category"),
                "recovery_attempted": metrics.get("recovery_attempted", False),
                "recovery_succeeded": metrics.get("recovery_succeeded", False),
            })

    # Aggregate task statistics
    task_summaries = {}
    total_tasks_passed_at_least_once = 0
    all_scores = []

    # Category grouping
    categories: Dict[str, List[float]] = {}

    for tid, runs in per_task_runs.items():
        task = load_task(tid)
        cat = task.get("category", "OTHER")
        scores = [r["score"] for r in runs]
        all_scores.extend(scores)
        stats = _compute_stats(scores)
        pass_count = sum(1 for r in runs if r["passed"])
        pass_at_1 = pass_count / len(runs) if runs else 0.0
        pass_at_k = 1.0 if pass_count > 0 else 0.0
        if pass_at_k > 0:
            total_tasks_passed_at_least_once += 1

        categories.setdefault(cat, []).extend(scores)

        task_summaries[tid] = {
            "name": task["name"],
            "category": cat,
            "score_stats": stats,
            "pass_at_1": round(pass_at_1 * 100, 1),
            "pass_at_k": round(pass_at_k * 100, 1),
            "runs": len(runs),
        }

    # Category summaries
    category_summaries = {}
    for cat, scs in categories.items():
        pass_n = sum(1 for s in scs if s >= 70)
        category_summaries[cat] = {
            "avg_score": round(sum(scs) / len(scs), 1) if scs else 0.0,
            "pass_rate_pct": round(pass_n / len(scs) * 100, 1) if scs else 0.0,
            "sample_count": len(scs),
        }

    # Percentiles for latency
    sorted_lats = sorted(all_latencies)
    p95_idx = int(len(sorted_lats) * 0.95) if sorted_lats else 0
    p95_lat = sorted_lats[p95_idx] if sorted_lats else 0.0

    recovery_rate_pct = (
        round((total_recovery_successes / total_recovery_attempts) * 100, 1)
        if total_recovery_attempts > 0 else 0.0
    )

    return {
        "model": config.model,
        "execution_type": config.execution_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "prompt_version": config.prompt_version,
            "temperature": config.temperature,
            "max_retries": config.max_retries,
            "runs_per_task": config.runs_per_task,
            "total_evaluations": len(all_scores),
        },
        "overall": {
            "average_score": round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0,
            "pass_rate_pct": round(sum(1 for s in all_scores if s >= 70) / len(all_scores) * 100, 1) if all_scores else 0.0,
            "pass_at_k_pct": round((total_tasks_passed_at_least_once / len(task_ids)) * 100, 1) if task_ids else 0.0,
        },
        "latency": {
            "mean_ms": round(sum(all_latencies) / len(all_latencies), 1) if all_latencies else 0.0,
            "p95_ms": round(p95_lat, 1),
            "min_ms": round(sorted_lats[0], 1) if sorted_lats else 0.0,
            "max_ms": round(sorted_lats[-1], 1) if sorted_lats else 0.0,
        },
        "cost": {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_task_usd": round(total_cost / len(task_ids), 6) if task_ids else 0.0,
        },
        "recovery": {
            "attempts": total_recovery_attempts,
            "successes": total_recovery_successes,
            "recovery_rate_pct": recovery_rate_pct,
        },
        "per_category": category_summaries,
        "tasks": task_summaries,
    }


def generate_comparison_report(
    results: List[Dict[str, Any]],
    output_dir: Path = REPORTS_DIR,
) -> Tuple[str, Path, Path]:
    """Generates side-by-side benchmark comparison report in Markdown and JSON."""
    lines = [
        "# AgentEval — Benchmark & Comparative Quality Report",
        "\nComprehensive multi-run benchmark results with pass@k, category accuracy, latency, cost, and recovery rate.\n",
        "## Exact Test Configurations & Provenance\n",
        "| Model | Execution Provenance | Prompt Version | Runs / Task | Avg Score | Pass Rate | Mean Latency | Est. Cost | Recovery Rate |",
        "|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for res in results:
        m = res["model"]
        provenance = f"`{res['execution_type']}`"
        pv = res["config"]["prompt_version"]
        rpt = res["config"]["runs_per_task"]
        sc = f"{res['overall']['average_score']:.1f}%"
        pr = f"{res['overall']['pass_rate_pct']:.1f}%"
        lat = f"{res['latency']['mean_ms']:.0f}ms"
        cost = f"${res['cost']['total_cost_usd']:.4f}"
        rec = f"{res['recovery']['recovery_rate_pct']:.1f}%"
        lines.append(f"| **{m}** | {provenance} | `v{pv}` | {rpt} | **{sc}** | {pr} | {lat} | {cost} | {rec} |")

    lines.append("\n## Per-Category Accuracy & Scores\n")
    # Collect all unique categories
    all_cats = sorted(list({c for r in results for c in r.get("per_category", {})}))
    headers = ["Category"] + [f"{r['model']} ({r['execution_type']})" for r in results]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join([":---"] + [":---:"] * len(results)) + "|")

    for cat in all_cats:
        row = [f"`{cat}`"]
        for r in results:
            cat_data = r.get("per_category", {}).get(cat)
            if cat_data:
                row.append(f"{cat_data['avg_score']:.1f}% (pass: {cat_data['pass_rate_pct']:.0f}%)")
            else:
                row.append("-")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n## Task-Level Performance & Stochastic Distribution\n")
    all_tids = TASK_IDS
    task_headers = ["Task ID", "Category"] + [f"{r['model']} Mean ± Std (Pass@1)" for r in results]
    lines.append("| " + " | ".join(task_headers) + " |")
    lines.append("|" + "|".join([":---", ":---"] + [":---:"] * len(results)) + "|")

    for tid in all_tids:
        sample_task = load_task(tid)
        row = [f"`{tid}`", f"`{sample_task.get('category')}`"]
        for r in results:
            tinfo = r.get("tasks", {}).get(tid)
            if tinfo:
                st = tinfo["score_stats"]
                p1 = tinfo["pass_at_1"]
                row.append(f"{st['mean']:.1f}% ± {st['std']:.1f} ({p1:.0f}%)")
            else:
                row.append("-")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n---\n*Report generated deterministically by AgentEval Benchmark Engine.*")
    md_content = "\n".join(lines)

    md_path = output_dir / "benchmark_report.md"
    json_path = output_dir / "benchmark_report.json"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return md_content, md_path, json_path
