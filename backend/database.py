"""
AgentEval — SQLite Database Layer
Stores evaluations, task runs, genuine trace events, metrics, and failure diagnoses.
"""
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "agenteval.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _run_migrations(conn: sqlite3.Connection):
    """Safely adds new columns if upgrading from an older DB version."""
    task_run_cols = {
        "latency_ms": "REAL DEFAULT 0",
        "token_usage": "TEXT",
        "estimated_cost": "REAL DEFAULT 0",
        "recovery_attempted": "INTEGER DEFAULT 0",
        "recovery_succeeded": "INTEGER DEFAULT 0",
        "retry_count": "INTEGER DEFAULT 0",
        "unnecessary_retries": "INTEGER DEFAULT 0",
        "diagnosis_json": "TEXT",
    }
    for col, ctype in task_run_cols.items():
        try:
            conn.execute(f"ALTER TABLE task_runs ADD COLUMN {col} {ctype}")
        except sqlite3.OperationalError:
            pass  # column already exists

    trace_cols = {
        "event_type": "TEXT DEFAULT 'ACTION'",
        "tool_name": "TEXT",
        "tool_args": "TEXT",
        "tool_result": "TEXT",
        "tool_error": "TEXT",
        "exit_code": "INTEGER",
        "duration_ms": "REAL DEFAULT 0",
        "token_usage": "TEXT",
        "estimated_cost": "REAL DEFAULT 0",
        "model_name": "TEXT",
        "prompt_version": "TEXT",
        "metadata": "TEXT",
    }
    for col, ctype in trace_cols.items():
        try:
            conn.execute(f"ALTER TABLE traces ADD COLUMN {col} {ctype}")
        except sqlite3.OperationalError:
            pass  # column already exists


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id             TEXT PRIMARY KEY,
            created_at     TEXT NOT NULL,
            agent_model    TEXT NOT NULL,
            total_tasks    INTEGER DEFAULT 0,
            completed_tasks INTEGER DEFAULT 0,
            status         TEXT DEFAULT 'pending',
            task_ids       TEXT
        );

        CREATE TABLE IF NOT EXISTS task_runs (
            id                  TEXT PRIMARY KEY,
            evaluation_id       TEXT NOT NULL,
            task_id             TEXT NOT NULL,
            task_name           TEXT,
            category            TEXT,
            status              TEXT DEFAULT 'pending',
            visible_score       REAL DEFAULT 0,
            hidden_score        REAL DEFAULT 0,
            regression_score    REAL DEFAULT 0,
            requirements_score  REAL DEFAULT 0,
            quality_score       REAL DEFAULT 0,
            judge_score         REAL DEFAULT 0,
            final_score         REAL DEFAULT 0,
            failure_category    TEXT,
            failure_details     TEXT,
            judge_result        TEXT,
            execution_method    TEXT DEFAULT 'unknown',
            agent_solution      TEXT,
            created_at          TEXT,
            completed_at        TEXT,
            latency_ms          REAL DEFAULT 0,
            token_usage         TEXT,
            estimated_cost      REAL DEFAULT 0,
            recovery_attempted  INTEGER DEFAULT 0,
            recovery_succeeded  INTEGER DEFAULT 0,
            retry_count         INTEGER DEFAULT 0,
            unnecessary_retries INTEGER DEFAULT 0,
            diagnosis_json      TEXT,
            FOREIGN KEY (evaluation_id) REFERENCES evaluations(id)
        );

        CREATE TABLE IF NOT EXISTS traces (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_run_id  TEXT NOT NULL,
            step_index   INTEGER DEFAULT 0,
            event_type   TEXT DEFAULT 'ACTION',
            action       TEXT NOT NULL,
            detail       TEXT,
            timestamp    TEXT NOT NULL,
            tool_name    TEXT,
            tool_args    TEXT,
            tool_result  TEXT,
            tool_error   TEXT,
            exit_code    INTEGER,
            duration_ms  REAL DEFAULT 0,
            token_usage  TEXT,
            estimated_cost REAL DEFAULT 0,
            model_name   TEXT,
            prompt_version TEXT,
            metadata     TEXT,
            FOREIGN KEY (task_run_id) REFERENCES task_runs(id)
        );
    """)
    _run_migrations(conn)
    conn.commit()
    conn.close()
    cleanup_stale_evaluations()


def cleanup_stale_evaluations():
    """Mark leftover 'running' evaluations or task_runs from previous crashed/interrupted sessions."""
    conn = get_conn()
    conn.execute("UPDATE task_runs SET status='failed' WHERE status='running'")
    conn.execute("UPDATE evaluations SET status='completed' WHERE status='running' AND completed_tasks >= total_tasks AND total_tasks > 0")
    conn.execute("UPDATE evaluations SET status='interrupted' WHERE status='running'")
    conn.commit()
    conn.close()


def save_evaluation(data: Dict):
    conn = get_conn()
    conn.execute(
        """INSERT INTO evaluations (id, created_at, agent_model, total_tasks, completed_tasks, status, task_ids)
           VALUES (:id, :created_at, :agent_model, :total_tasks, :completed_tasks, :status, :task_ids)""",
        {**data, "task_ids": json.dumps(data.get("task_ids", []))}
    )
    conn.commit()
    conn.close()


def update_evaluation_status(eval_id: str, status: str, completed_tasks: Optional[int] = None):
    conn = get_conn()
    if completed_tasks is not None:
        conn.execute(
            "UPDATE evaluations SET status=?, completed_tasks=? WHERE id=?",
            (status, completed_tasks, eval_id)
        )
    else:
        conn.execute("UPDATE evaluations SET status=? WHERE id=?", (status, eval_id))
    conn.commit()
    conn.close()


def save_task_run(data: Dict):
    conn = get_conn()
    conn.execute(
        """INSERT INTO task_runs
           (id, evaluation_id, task_id, task_name, category, status, created_at)
           VALUES (:id, :evaluation_id, :task_id, :task_name, :category, :status, :created_at)""",
        data
    )
    conn.commit()
    conn.close()


def update_task_run_result(run_id: str, grade: Dict, solution: str, status: str, metrics: Optional[Dict] = None):
    metrics = metrics or {}
    conn = get_conn()
    conn.execute(
        """UPDATE task_runs SET
               status=?, visible_score=?, hidden_score=?, regression_score=?,
               requirements_score=?, quality_score=?, judge_score=?,
               final_score=?,
               failure_category=?, failure_details=?,
               judge_result=?, execution_method=?,
               agent_solution=?, completed_at=?,
               latency_ms=?, token_usage=?, estimated_cost=?,
               recovery_attempted=?, recovery_succeeded=?,
               retry_count=?, unnecessary_retries=?,
               diagnosis_json=?
           WHERE id=?""",
        (
            status,
            grade.get("visible_score", 0),
            grade.get("hidden_score", 0),
            grade.get("regression_score", 0),
            grade.get("requirements_score", 0),
            grade.get("quality_score", 0),
            grade.get("judge_score", 0),
            grade.get("final_score", 0),
            grade.get("failure_category"),
            json.dumps(grade.get("failure_details", [])),
            json.dumps(grade.get("judge_result")) if grade.get("judge_result") else None,
            grade.get("execution_method", "unknown"),
            solution,
            datetime.utcnow().isoformat(),
            metrics.get("latency_ms", 0.0),
            json.dumps(metrics.get("token_usage", {})),
            metrics.get("estimated_cost", 0.0),
            1 if metrics.get("recovery_attempted") else 0,
            1 if metrics.get("recovery_succeeded") else 0,
            metrics.get("retry_count", 0),
            metrics.get("unnecessary_retries", 0),
            json.dumps(grade.get("diagnosis", {})),
            run_id,
        )
    )
    conn.commit()
    conn.close()


def save_trace(data: Dict):
    conn = get_conn()
    step_index = data.get("step_index")
    if step_index is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) FROM traces WHERE task_run_id=?",
            (data["task_run_id"],)
        ).fetchone()
        step_index = (row[0] or -1) + 1

    conn.execute(
        """INSERT INTO traces (
            task_run_id, step_index, event_type, action, detail, timestamp,
            tool_name, tool_args, tool_result, tool_error, exit_code,
            duration_ms, token_usage, estimated_cost, model_name,
            prompt_version, metadata
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["task_run_id"],
            step_index,
            data.get("event_type", "ACTION"),
            data["action"],
            data.get("detail", ""),
            data["timestamp"],
            data.get("tool_name"),
            json.dumps(data.get("tool_args")) if data.get("tool_args") is not None else None,
            json.dumps(data.get("tool_result")) if data.get("tool_result") is not None else None,
            data.get("tool_error"),
            data.get("exit_code"),
            data.get("duration_ms", 0.0),
            json.dumps(data.get("token_usage")) if data.get("token_usage") is not None else None,
            data.get("estimated_cost", 0.0),
            data.get("model_name"),
            data.get("prompt_version"),
            json.dumps(data.get("metadata", {})),
        )
    )
    conn.commit()
    conn.close()


def get_all_evaluations() -> List[Dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT e.*,
               COUNT(tr.id)         AS run_count,
               ROUND(AVG(tr.final_score), 1) AS avg_score,
               SUM(CASE WHEN tr.status='passed' THEN 1 ELSE 0 END) AS passed_count,
               ROUND(AVG(tr.latency_ms), 1) AS avg_latency_ms,
               ROUND(SUM(tr.estimated_cost), 4) AS total_cost_usd,
               SUM(tr.recovery_attempted) AS total_recovery_attempts,
               SUM(tr.recovery_succeeded) AS total_recovery_successes
        FROM evaluations e
        LEFT JOIN task_runs tr ON tr.evaluation_id = e.id
        GROUP BY e.id
        ORDER BY e.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_evaluation_detail(eval_id: str) -> Optional[Dict]:
    conn = get_conn()
    eval_row = conn.execute("SELECT * FROM evaluations WHERE id=?", (eval_id,)).fetchone()
    if not eval_row:
        conn.close()
        return None
    result = dict(eval_row)

    task_runs = conn.execute(
        "SELECT * FROM task_runs WHERE evaluation_id=? ORDER BY created_at", (eval_id,)
    ).fetchall()

    result["task_runs"] = []
    for tr in task_runs:
        tr_dict = dict(tr)
        if tr_dict.get("failure_details"):
            try:
                tr_dict["failure_details"] = json.loads(tr_dict["failure_details"])
            except Exception:
                pass
        if tr_dict.get("judge_result"):
            try:
                tr_dict["judge_result"] = json.loads(tr_dict["judge_result"])
            except Exception:
                pass
        if tr_dict.get("token_usage"):
            try:
                tr_dict["token_usage"] = json.loads(tr_dict["token_usage"])
            except Exception:
                pass
        if tr_dict.get("diagnosis_json"):
            try:
                tr_dict["diagnosis"] = json.loads(tr_dict["diagnosis_json"])
            except Exception:
                pass
        traces = conn.execute(
            "SELECT * FROM traces WHERE task_run_id=? ORDER BY step_index", (tr_dict["id"],)
        ).fetchall()
        tr_dict["traces"] = []
        for t in traces:
            td = dict(t)
            if td.get("tool_args"):
                try: td["tool_args"] = json.loads(td["tool_args"])
                except Exception: pass
            if td.get("tool_result"):
                try: td["tool_result"] = json.loads(td["tool_result"])
                except Exception: pass
            if td.get("token_usage"):
                try: td["token_usage"] = json.loads(td["token_usage"])
                except Exception: pass
            if td.get("metadata"):
                try: td["metadata"] = json.loads(td["metadata"])
                except Exception: pass
            tr_dict["traces"].append(td)
        result["task_runs"].append(tr_dict)

    conn.close()
    return result
