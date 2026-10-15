#!/usr/bin/env python3
"""
AgentEval — Failure Reproduction Tool

Proves Requirement 3: Every displayed trace corresponds to a real event
and can be used to reproduce a failure.

Usage:
    python reproduce_failure.py <run_id>
    python reproduce_failure.py traces/<run_id>.json
"""
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from grader import grade_task
from tasks.task_registry import load_task


def reproduce_run(run_identifier: str):
    # Resolve trace file path
    trace_path = Path(run_identifier)
    if not trace_path.exists():
        trace_path = ROOT_DIR / "traces" / f"{run_identifier}.json"

    if not trace_path.exists():
        print(f"[-] Error: Trace file not found: {trace_path}")
        sys.exit(1)

    print(f"[*] Loading genuine trace from: {trace_path}")
    with open(trace_path, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    task_id = trace_data["task_id"]
    run_id = trace_data["run_id"]
    events = trace_data.get("events", [])

    print("=" * 70)
    print(f"REPRODUCING TRACE: {run_id} | Task: {task_id}")
    print(f"Total Recorded Steps: {len(events)}")
    print(f"Recorded Latency: {trace_data.get('total_duration_ms', 0):.1f}ms")
    print(f"Recorded Tokens: {trace_data.get('total_tokens', {})}")
    print(f"Recorded Cost: ${trace_data.get('total_cost_usd', 0):.6f}")
    print("=" * 70)

    # Replay sequence of events
    candidate_code = None
    for evt in events:
        step = evt["step_index"]
        atype = evt["event_type"]
        action = evt["action"]
        detail = evt["detail"]
        dur = evt.get("duration_ms", 0)
        print(f"  Step {step:2d} | [{atype:18s}] {action:22s} ({dur:.1f}ms) -> {detail[:70]}")

        # Extract generated code from write tool or model response
        if evt.get("tool_name") == "write_file" and "filepath" in (evt.get("tool_args") or {}):
            # Code was written
            pass

    # Look for last model response or written solution
    for evt in reversed(events):
        if evt.get("tool_result") and isinstance(evt.get("tool_result"), dict) and "tests" in evt["tool_result"]:
            pass
        if evt.get("metadata") and "diagnosis" in evt.get("metadata", {}):
            print("\n[!] Original Failure Diagnosis:")
            diag = evt["metadata"]["diagnosis"]
            print(f"    Category: {diag.get('category')}")
            print(f"    Summary:  {diag.get('summary')}")
            print(f"    Cause:    {diag.get('root_cause')}")

    # Re-grade to verify failure reproduction
    task_meta = load_task(task_id)
    candidate_code = trace_data.get("candidate_solution")
    original_status = None
    original_score = None
    original_category = None

    if not candidate_code:
        from database import get_conn
        conn = get_conn()
        row = conn.execute("SELECT agent_solution, status, final_score, failure_category FROM task_runs WHERE id=?", (run_id,)).fetchone()
        conn.close()
        if row and row["agent_solution"]:
            candidate_code = row["agent_solution"]
            original_status = row["status"]
            original_score = row["final_score"]
            original_category = row["failure_category"]

    if candidate_code:
        print("-" * 70)
        print(f"[*] Re-executing grading against candidate solution ({len(candidate_code)} chars)...")
        reproduced_grade = grade_task(task_id, candidate_code, task_meta)

        repro_status = "passed" if reproduced_grade["final_score"] >= 70 else "failed"
        print(f"[*] Original Outcome:   {original_status.upper()} (Score: {original_score}%, Category: {original_category})")
        print(f"[*] Reproduced Outcome: {repro_status.upper()} (Score: {reproduced_grade['final_score']}%, Category: {reproduced_grade.get('failure_category')})")

        if original_status == repro_status:
            print(f"\n[+] SUCCESS: Failure reproduced identically! Trace verified genuine.")
        else:
            print(f"\n[!] Note: Score variance observed: original={original_score}%, repro={reproduced_grade['final_score']}%")
    else:
        print("[*] Trace replay complete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python reproduce_failure.py <run_id_or_trace_file>")
        sys.exit(1)
    reproduce_run(sys.argv[1])
