"""
AgentEval — Main FastAPI Application
Serves the REST API, WebSocket live-trace stream, and static frontend.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Resolve paths
BACKEND_DIR = Path(__file__).parent
ROOT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

sys.path.insert(0, str(BACKEND_DIR))

from database import (
    init_db,
    get_conn,
    save_evaluation,
    save_task_run,
    save_trace,
    update_task_run_result,
    update_evaluation_status,
    get_all_evaluations,
    get_evaluation_detail,
)
from agent import OllamaAgent, MockAgent
from agent_runtime import AgentExecutionRuntime
from tracing import TraceRecorder, TRACES_DIR
from grader import grade_task
from tasks.task_registry import load_all_tasks, load_task, TASK_IDS

# Sandbox and judge imports (optional — degrade gracefully)
try:
    from sandbox_executor import (
        is_docker_available, ensure_sandbox_image,
        cleanup_sandbox_containers, SandboxError,
    )
    _HAS_SANDBOX = True
except ImportError:
    _HAS_SANDBOX = False

try:
    from llm_judge import JUDGE_ENABLED, RUBRIC_VERSION, compute_agreement
    _HAS_JUDGE = True
except ImportError:
    _HAS_JUDGE = False

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="AgentEval", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self._conns: Dict[str, List[WebSocket]] = {}

    async def connect(self, eval_id: str, ws: WebSocket):
        await ws.accept()
        self._conns.setdefault(eval_id, []).append(ws)

    def disconnect(self, eval_id: str, ws: WebSocket):
        conns = self._conns.get(eval_id, [])
        try:
            conns.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, eval_id: str, msg: dict):
        dead = []
        for ws in list(self._conns.get(eval_id, [])):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(eval_id, ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _startup():
    init_db()
    # Ensure traces dir exists
    (ROOT_DIR / "traces").mkdir(exist_ok=True)

    # Pre-build sandbox Docker image if Docker is available
    if _HAS_SANDBOX and is_docker_available():
        try:
            ensure_sandbox_image()
            print("[startup] Sandbox Docker image ready")
        except Exception as exc:
            print(f"[startup] WARNING: Failed to build sandbox image: {exc}")
            print("[startup] Grading will use legacy executor if AGENTEVAL_SANDBOX_FALLBACK=1")
    elif _HAS_SANDBOX:
        print("[startup] Docker not available — sandbox disabled")
    else:
        print("[startup] sandbox_executor module not found")

    # Report judge status
    if _HAS_JUDGE and JUDGE_ENABLED:
        print(f"[startup] LLM judge enabled (rubric v{RUBRIC_VERSION})")
    elif _HAS_JUDGE:
        print("[startup] LLM judge available but disabled (AGENTEVAL_JUDGE_ENABLED=0)")
    else:
        print("[startup] LLM judge module not found")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EvaluationRequest(BaseModel):
    model: str = "mock"
    task_ids: List[str] = []
    simulate_transient_failure: bool = False
    simulate_incorrect_first_attempt: bool = False
    max_retries: int = 2


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/models")
async def api_get_models():
    """List locally available Ollama models by querying Ollama's /api/tags endpoint."""
    import requests as _requests
    try:
        resp = _requests.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models = [m["name"] for m in data.get("models", [])]
        return {"ollama_running": True, "models": models}
    except Exception:
        return {"ollama_running": False, "models": []}


@app.get("/api/sandbox/status")
async def api_sandbox_status():
    """Check sandbox and judge system status."""
    sandbox_status = {
        "module_loaded": _HAS_SANDBOX,
        "docker_available": _HAS_SANDBOX and is_docker_available() if _HAS_SANDBOX else False,
        "image_ready": False,
        "fallback_enabled": os.environ.get("AGENTEVAL_SANDBOX_FALLBACK", "0") == "1",
    }

    if _HAS_SANDBOX and is_docker_available():
        try:
            import subprocess
            result = subprocess.run(
                ["docker", "image", "inspect", "agenteval-sandbox:latest"],
                capture_output=True, text=True, timeout=5,
            )
            sandbox_status["image_ready"] = result.returncode == 0
        except Exception:
            pass

    judge_status = {
        "module_loaded": _HAS_JUDGE,
        "enabled": _HAS_JUDGE and JUDGE_ENABLED if _HAS_JUDGE else False,
        "rubric_version": RUBRIC_VERSION if _HAS_JUDGE else None,
    }

    return {"sandbox": sandbox_status, "judge": judge_status}


@app.get("/api/tasks")
async def api_get_tasks():
    """List all available tasks."""
    return load_all_tasks()


@app.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str):
    """Get full task details including starter code and visible test."""
    try:
        task = load_task(task_id)
        # Don't expose task_dir path
        return {k: v for k, v in task.items() if k != "task_dir"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")


@app.post("/api/evaluations")
async def api_create_evaluation(req: EvaluationRequest):
    """Start a new evaluation run."""
    eval_id = str(uuid.uuid4())[:8]
    task_ids = req.task_ids if req.task_ids else TASK_IDS

    # Validate task IDs
    invalid = [t for t in task_ids if t not in TASK_IDS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown task IDs: {invalid}")

    eval_data = {
        "id": eval_id,
        "created_at": datetime.utcnow().isoformat(),
        "agent_model": req.model,
        "total_tasks": len(task_ids),
        "completed_tasks": 0,
        "status": "running",
        "task_ids": task_ids,
    }
    save_evaluation(eval_data)

    # Start background evaluation task
    asyncio.create_task(
        _run_evaluation(
            eval_id=eval_id,
            model=req.model,
            task_ids=task_ids,
            simulate_transient_failure=req.simulate_transient_failure,
            simulate_incorrect_first_attempt=req.simulate_incorrect_first_attempt,
            max_retries=req.max_retries,
        )
    )

    return {"evaluation_id": eval_id, "status": "running"}


@app.get("/api/evaluations")
async def api_list_evaluations():
    """List all evaluation runs with summary stats."""
    return get_all_evaluations()


@app.get("/api/evaluations/{eval_id}")
async def api_get_evaluation(eval_id: str):
    """Get full evaluation details including all task runs and traces."""
    detail = get_evaluation_detail(eval_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return detail


@app.delete("/api/evaluations/{eval_id}")
async def api_delete_evaluation(eval_id: str):
    """Delete an evaluation and all its associated data."""
    conn = get_conn()
    conn.execute("DELETE FROM traces WHERE task_run_id IN (SELECT id FROM task_runs WHERE evaluation_id=?)", (eval_id,))
    conn.execute("DELETE FROM task_runs WHERE evaluation_id=?", (eval_id,))
    conn.execute("DELETE FROM evaluations WHERE id=?", (eval_id,))
    conn.commit()
    conn.close()
    return {"deleted": eval_id}


@app.get("/api/traces/{run_id}")
async def api_get_trace(run_id: str):
    """Get genuine trace file for a specific task run."""
    trace_file = TRACES_DIR / f"{run_id}.json"
    if not trace_file.exists():
        raise HTTPException(status_code=404, detail="Trace file not found")
    with open(trace_file, "r", encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/traces/{run_id}/reproduce")
async def api_reproduce_trace(run_id: str):
    """Reproduce the failure or outcome of a specific task run using its trace."""
    conn = get_conn()
    row = conn.execute(
        "SELECT task_id, agent_solution, status, final_score, failure_category FROM task_runs WHERE id=?",
        (run_id,)
    ).fetchone()
    conn.close()

    if not row or not row["agent_solution"]:
        raise HTTPException(status_code=404, detail="Task run or candidate solution not found")

    task_id = row["task_id"]
    task_meta = load_task(task_id)
    reproduced = grade_task(task_id, row["agent_solution"], task_meta)
    reproduced_status = "passed" if reproduced["final_score"] >= 70 else "failed"

    return {
        "run_id": run_id,
        "task_id": task_id,
        "original": {
            "status": row["status"],
            "score": row["final_score"],
            "category": row["failure_category"],
        },
        "reproduced": {
            "status": reproduced_status,
            "score": reproduced["final_score"],
            "category": reproduced.get("failure_category"),
            "diagnosis": reproduced.get("diagnosis"),
        },
        "reproduced_identically": (row["status"] == reproduced_status),
    }


@app.get("/api/benchmark/summary")
async def api_benchmark_summary():
    """Aggregate benchmark metrics: pass rate, per-category accuracy, latency, cost, and recovery rate."""
    conn = get_conn()
    total_runs = conn.execute("SELECT COUNT(*) FROM task_runs WHERE status != 'pending'").fetchone()[0]
    if total_runs == 0:
        conn.close()
        return {
            "total_runs": 0,
            "pass_rate": 0.0,
            "per_category": {},
            "latency": {"avg_ms": 0.0, "max_ms": 0.0},
            "cost": {"total_usd": 0.0, "avg_usd": 0.0},
            "recovery": {"attempts": 0, "successes": 0, "recovery_rate_pct": 0.0},
        }

    passed_runs = conn.execute("SELECT COUNT(*) FROM task_runs WHERE status = 'passed'").fetchone()[0]
    pass_rate = round((passed_runs / total_runs) * 100, 1)

    cats = conn.execute("""
        SELECT category, COUNT(*) as total, SUM(CASE WHEN status='passed' THEN 1 ELSE 0 END) as passed,
               AVG(final_score) as avg_score
        FROM task_runs
        WHERE status != 'pending'
        GROUP BY category
    """).fetchall()

    per_category = {}
    for c in cats:
        tot = c["total"]
        pas = c["passed"] or 0
        per_category[c["category"] or "UNKNOWN"] = {
            "total": tot,
            "passed": pas,
            "pass_rate": round((pas / tot) * 100, 1) if tot else 0.0,
            "avg_score": round(c["avg_score"] or 0, 1),
        }

    metrics_row = conn.execute("""
        SELECT AVG(latency_ms) as avg_lat, MAX(latency_ms) as max_lat,
               SUM(estimated_cost) as total_cost, AVG(estimated_cost) as avg_cost,
               SUM(recovery_attempted) as recovery_attempts,
               SUM(recovery_succeeded) as recovery_successes
        FROM task_runs
        WHERE status != 'pending'
    """).fetchone()

    conn.close()

    rec_attempts = metrics_row["recovery_attempts"] or 0
    rec_success = metrics_row["recovery_successes"] or 0
    recovery_rate = round((rec_success / rec_attempts) * 100, 1) if rec_attempts > 0 else 0.0

    return {
        "total_runs": total_runs,
        "pass_rate": pass_rate,
        "per_category": per_category,
        "latency": {
            "avg_ms": round(metrics_row["avg_lat"] or 0, 1),
            "max_ms": round(metrics_row["max_lat"] or 0, 1),
        },
        "cost": {
            "total_usd": round(metrics_row["total_cost"] or 0, 4),
            "avg_usd": round(metrics_row["avg_cost"] or 0, 6),
        },
        "recovery": {
            "attempts": rec_attempts,
            "successes": rec_success,
            "recovery_rate_pct": recovery_rate,
        },
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws/evaluations/{eval_id}")
async def ws_evaluation(websocket: WebSocket, eval_id: str):
    await manager.connect(eval_id, websocket)
    try:
        # Replay any already-stored traces for this evaluation
        conn = get_conn()
        existing = conn.execute("""
            SELECT t.action, t.detail, t.timestamp, tr.task_id
            FROM traces t
            JOIN task_runs tr ON t.task_run_id = tr.id
            WHERE tr.evaluation_id = ?
            ORDER BY t.timestamp
        """, (eval_id,)).fetchall()
        conn.close()

        for row in existing:
            await websocket.send_json({
                "type": "trace_step",
                "task_id": row["task_id"],
                "action": row["action"],
                "detail": row["detail"],
                "timestamp": row["timestamp"],
            })

        # Keep connection alive
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(eval_id, websocket)


# ---------------------------------------------------------------------------
# Background evaluation runner
# ---------------------------------------------------------------------------

async def _run_evaluation(
    eval_id: str,
    model: str,
    task_ids: List[str],
    simulate_transient_failure: bool = False,
    simulate_incorrect_first_attempt: bool = False,
    max_retries: int = 2,
):
    """Main background coroutine that drives genuine agent execution & evaluation."""
    loop = asyncio.get_event_loop()
    results = []

    await asyncio.sleep(0.3)  # Give WebSocket time to connect

    try:
        for i, task_id in enumerate(task_ids):
            run_id = f"{eval_id}_{task_id}"
            try:
                task = load_task(task_id)
            except Exception as exc:
                await manager.broadcast(eval_id, {
                    "type": "error", "task_id": task_id,
                    "detail": f"Could not load task: {exc}"
                })
                continue

            # Persist task run
            save_task_run({
                "id": run_id,
                "evaluation_id": eval_id,
                "task_id": task_id,
                "task_name": task["name"],
                "category": task["category"],
                "status": "running",
                "created_at": datetime.utcnow().isoformat(),
            })

            # Announce task start
            await manager.broadcast(eval_id, {
                "type": "task_start",
                "eval_id": eval_id,
                "task_id": task_id,
                "task_name": task["name"],
                "task_index": i,
                "total_tasks": len(task_ids),
            })
            await asyncio.sleep(0.1)

            # Define real-time trace callback for genuine execution events
            def _on_trace_event(evt_data: Dict[str, Any]):
                ws_msg = {
                    "type": "trace_step",
                    "eval_id": eval_id,
                    "task_id": task_id,
                    "action": evt_data["action"],
                    "detail": evt_data["detail"],
                    "timestamp": evt_data["timestamp"],
                    "event_type": evt_data.get("event_type", "ACTION"),
                    "duration_ms": evt_data.get("duration_ms", 0.0),
                    "token_usage": evt_data.get("token_usage"),
                    "cost_usd": evt_data.get("estimated_cost_usd", 0.0),
                    "tool_name": evt_data.get("tool_name"),
                    "exit_code": evt_data.get("exit_code"),
                }
                asyncio.run_coroutine_threadsafe(manager.broadcast(eval_id, ws_msg), loop)
                save_trace({
                    "task_run_id": run_id,
                    "step_index": evt_data.get("step_index"),
                    "event_type": evt_data.get("event_type", "ACTION"),
                    "action": evt_data["action"],
                    "detail": evt_data["detail"],
                    "timestamp": evt_data["timestamp"],
                    "tool_name": evt_data.get("tool_name"),
                    "tool_args": evt_data.get("tool_args"),
                    "tool_result": evt_data.get("tool_result"),
                    "tool_error": evt_data.get("tool_error"),
                    "exit_code": evt_data.get("exit_code"),
                    "duration_ms": evt_data.get("duration_ms", 0.0),
                    "token_usage": evt_data.get("token_usage"),
                    "estimated_cost": evt_data.get("estimated_cost_usd", 0.0),
                    "model_name": evt_data.get("model_name"),
                    "prompt_version": evt_data.get("prompt_version"),
                    "metadata": evt_data.get("metadata", {}),
                })

            recorder = TraceRecorder(run_id=run_id, task_id=task_id, on_event=_on_trace_event)
            runtime = AgentExecutionRuntime(
                recorder=recorder,
                model_name=model,
                max_retries=max_retries,
                simulate_transient_failure=simulate_transient_failure,
                simulate_incorrect_first_attempt=simulate_incorrect_first_attempt,
            )

            # Execute genuine agent tools, inference, and recovery retries
            try:
                solution, metrics = await loop.run_in_executor(None, runtime.execute, task)
            except Exception as exc:
                _on_trace_event({
                    "action": "AGENT_ERROR",
                    "detail": f"Agent runtime execution error: {exc}",
                    "timestamp": datetime.utcnow().isoformat(),
                    "event_type": "ERROR",
                    "duration_ms": 0.0,
                    "token_usage": None,
                    "estimated_cost_usd": 0.0,
                    "tool_error": str(exc),
                })
                update_evaluation_status(eval_id, "running", completed_tasks=i + 1)
                continue

            # Run hidden tests in Docker sandbox with genuine trace recording
            try:
                grade = await loop.run_in_executor(None, grade_task, task_id, solution, task, recorder)
            except Exception as exc:
                grade = {
                    "final_score": 0, "hidden_score": 0, "regression_score": 0,
                    "requirements_score": 0, "quality_score": 0, "judge_score": 0,
                    "visible_score": 0, "hidden_total": 0, "hidden_passed": 0,
                    "failure_category": "ENVIRONMENT_FAILURE", "failure_details": [],
                    "raw_output": str(exc), "judge_result": None, "execution_method": "error",
                    "diagnosis": {"category": "ENVIRONMENT_FAILURE", "summary": str(exc), "root_cause": str(exc)},
                }

            # Report judge results if available
            judge_result = grade.get("judge_result")
            if judge_result and judge_result.get("enabled") and not judge_result.get("error"):
                judge_score = grade.get("judge_score", 0)
                _on_trace_event({
                    "action": "LLM_JUDGE",
                    "detail": f"LLM Judge score: {judge_score:.0f}% (model: {judge_result.get('model', '?')}, rubric: v{judge_result.get('rubric_version', '?')})",
                    "timestamp": datetime.utcnow().isoformat(),
                    "event_type": "JUDGE_EVALUATION",
                    "metadata": judge_result,
                })
            elif judge_result and judge_result.get("error"):
                _on_trace_event({
                    "action": "LLM_JUDGE",
                    "detail": f"Judge skipped: {judge_result['error'][:100]}",
                    "timestamp": datetime.utcnow().isoformat(),
                    "event_type": "JUDGE_EVALUATION",
                    "metadata": judge_result,
                })

            _on_trace_event({
                "action": "SCORING",
                "detail": f"Hidden: {grade['hidden_score']:.0f}% | Regression: {grade['regression_score']:.0f}% | "
                          f"Judge: {grade.get('judge_score', 0):.0f}% | Final: {grade['final_score']:.0f}%",
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": "SCORING_SUMMARY",
            })

            status = "passed" if grade["final_score"] >= 70 else "failed"
            result_label = "PASSED" if status == "passed" else "FAILED"
            _on_trace_event({
                "action": "COMPLETE",
                "detail": f"Task {result_label} — Final Score: {grade['final_score']:.1f}% "
                          f"({metrics.get('retry_count', 0)} retries, {metrics.get('latency_ms', 0):.0f}ms)",
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": "TASK_COMPLETE",
            })

            # Save full reproducible trace file to disk
            recorder.save_to_file(candidate_solution=solution)

            # Persist result with genuine metrics and diagnosis
            update_task_run_result(run_id, grade, solution, status, metrics=metrics)

            results.append({
                "task_id": task_id,
                "score": grade["final_score"],
                "status": status,
            })

            # Broadcast task complete event
            await manager.broadcast(eval_id, {
                "type": "task_complete",
                "eval_id": eval_id,
                "task_id": task_id,
                "run_id": run_id,
                "score": grade["final_score"],
                "status": status,
                "grade": {
                    "visible_score": grade["visible_score"],
                    "hidden_score": grade["hidden_score"],
                    "regression_score": grade["regression_score"],
                    "requirements_score": grade["requirements_score"],
                    "quality_score": grade["quality_score"],
                    "judge_score": grade.get("judge_score", 0),
                    "final_score": grade["final_score"],
                    "hidden_passed": grade["hidden_passed"],
                    "hidden_total": grade["hidden_total"],
                    "failure_category": grade.get("failure_category"),
                    "failure_details": grade.get("failure_details", []),
                    "judge_result": grade.get("judge_result"),
                    "execution_method": grade.get("execution_method", "unknown"),
                    "diagnosis": grade.get("diagnosis"),
                },
                "metrics": metrics,
            })

            # Update progress
            update_evaluation_status(eval_id, "running", completed_tasks=i + 1)
            await asyncio.sleep(0.3)

        # Finalize
        update_evaluation_status(eval_id, "completed", completed_tasks=len(task_ids))

        avg_score = sum(r["score"] for r in results) / len(results) if results else 0
        passed = sum(1 for r in results if r["status"] == "passed")

        await manager.broadcast(eval_id, {
            "type": "evaluation_complete",
            "eval_id": eval_id,
            "summary": {
                "total": len(results),
                "passed": passed,
                "failed": len(results) - passed,
                "avg_score": round(avg_score, 1),
            }
        })
    except Exception as exc:
        update_evaluation_status(eval_id, "interrupted")
        await manager.broadcast(eval_id, {
            "type": "error", "task_id": "eval",
            "detail": f"Evaluation interrupted: {exc}"
        })


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"message": "AgentEval API running", "docs": "/docs"})


@app.get("/{path:path}")
async def catch_all(path: str):
    """Serve frontend for all non-API routes (SPA routing)."""
    # Try to serve as a static file first
    static_file = FRONTEND_DIR / path
    if static_file.exists() and static_file.is_file():
        return FileResponse(str(static_file))
    # Otherwise serve index.html for SPA routing
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404)
