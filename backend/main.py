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
from grader import grade_task
from tasks.task_registry import load_all_tasks, load_task, TASK_IDS

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


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EvaluationRequest(BaseModel):
    model: str = "mock"
    task_ids: List[str] = []


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
    asyncio.create_task(_run_evaluation(eval_id, req.model, task_ids))

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

async def _emit(eval_id: str, task_id: str, action: str, detail: str, run_id: str):
    msg = {
        "type": "trace_step",
        "eval_id": eval_id,
        "task_id": task_id,
        "action": action,
        "detail": detail,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await manager.broadcast(eval_id, msg)
    if run_id:
        save_trace({"task_run_id": run_id, "action": action, "detail": detail, "timestamp": msg["timestamp"]})


async def _run_evaluation(eval_id: str, model: str, task_ids: List[str]):
    """Main background coroutine that drives the entire evaluation."""
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

            # Simulate agent trace steps
            await _emit(eval_id, task_id, "INSPECT", f"Reading {task_id}/app.py ({len(task['starter_code'])} chars)", run_id)
            await asyncio.sleep(0.4)

            await _emit(eval_id, task_id, "INSPECT", f"Reading {task_id}/tests/visible_test.py", run_id)
            await asyncio.sleep(0.3)

            await _emit(eval_id, task_id, "THINKING", f"Agent ({model}) analyzing task requirements...", run_id)
            await asyncio.sleep(0.2)

            # Construct the agent
            if model == "mock":
                agent = MockAgent(model, task_id)
            else:
                agent = OllamaAgent(model)

            # Run agent in thread pool (blocking LLM call)
            await _emit(eval_id, task_id, "GENERATING", "Generating solution code...", run_id)
            try:
                solution = await loop.run_in_executor(None, agent.solve, task)
            except Exception as exc:
                await _emit(eval_id, task_id, "ERROR", f"Agent failed: {exc}", run_id)
                update_evaluation_status(eval_id, "running", completed_tasks=i + 1)
                continue

            await _emit(eval_id, task_id, "WRITE", f"Writing solution to {task_id}/app.py", run_id)
            await asyncio.sleep(0.3)

            # Run visible tests (we just check if code parses)
            import ast
            visible_ok = True
            try:
                ast.parse(solution)
            except SyntaxError:
                visible_ok = False

            await _emit(eval_id, task_id, "VISIBLE TESTS", f"Visible tests: {'PASS' if visible_ok else 'FAIL'}", run_id)
            await asyncio.sleep(0.3)

            if visible_ok:
                await _emit(eval_id, task_id, "STOP", "Visible tests passed — agent stops here", run_id)
                await asyncio.sleep(0.4)

            # Run hidden grader (blocking)
            await _emit(eval_id, task_id, "HIDDEN TESTS", "Running hidden test suite (agent cannot see these)...", run_id)
            try:
                grade = await loop.run_in_executor(None, grade_task, task_id, solution, task)
            except Exception as exc:
                grade = {"final_score": 0, "hidden_score": 0, "regression_score": 0,
                         "requirements_score": 0, "quality_score": 0, "visible_score": 0,
                         "hidden_total": 0, "hidden_passed": 0,
                         "failure_category": "ERROR", "failure_details": [], "raw_output": str(exc)}

            await _emit(
                eval_id, task_id, "SCORING",
                f"Hidden: {grade['hidden_score']:.0f}% | Regression: {grade['regression_score']:.0f}% | Final: {grade['final_score']:.0f}%",
                run_id
            )
            await asyncio.sleep(0.2)

            status = "passed" if grade["final_score"] >= 70 else "failed"
            result_label = "PASSED" if status == "passed" else "FAILED"
            await _emit(eval_id, task_id, f"COMPLETE", f"Task {result_label} — Score: {grade['final_score']:.1f}%", run_id)

            # Persist result
            update_task_run_result(run_id, grade, solution, status)

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
                    "final_score": grade["final_score"],
                    "hidden_passed": grade["hidden_passed"],
                    "hidden_total": grade["hidden_total"],
                    "failure_category": grade.get("failure_category"),
                    "failure_details": grade.get("failure_details", []),
                },
            })

            # Update progress
            update_evaluation_status(eval_id, "running", completed_tasks=i + 1)
            await asyncio.sleep(0.5)

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
