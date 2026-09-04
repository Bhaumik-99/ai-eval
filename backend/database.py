"""
AgentEval — SQLite Database Layer
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
            final_score         REAL DEFAULT 0,
            failure_category    TEXT,
            failure_details     TEXT,
            agent_solution      TEXT,
            created_at          TEXT,
            completed_at        TEXT,
            FOREIGN KEY (evaluation_id) REFERENCES evaluations(id)
        );

        CREATE TABLE IF NOT EXISTS traces (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_run_id  TEXT NOT NULL,
            step_index   INTEGER DEFAULT 0,
            action       TEXT NOT NULL,
            detail       TEXT,
            timestamp    TEXT NOT NULL,
            FOREIGN KEY (task_run_id) REFERENCES task_runs(id)
        );
    """)
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


def update_task_run_result(run_id: str, grade: Dict, solution: str, status: str):
    conn = get_conn()
    conn.execute(
        """UPDATE task_runs SET
               status=?, visible_score=?, hidden_score=?, regression_score=?,
               requirements_score=?, quality_score=?, final_score=?,
               failure_category=?, failure_details=?, agent_solution=?, completed_at=?
           WHERE id=?""",
        (
            status,
            grade.get("visible_score", 0),
            grade.get("hidden_score", 0),
            grade.get("regression_score", 0),
            grade.get("requirements_score", 0),
            grade.get("quality_score", 0),
            grade.get("final_score", 0),
            grade.get("failure_category"),
            json.dumps(grade.get("failure_details", [])),
            solution,
            datetime.utcnow().isoformat(),
            run_id,
        )
    )
    conn.commit()
    conn.close()


def save_trace(data: Dict):
    conn = get_conn()
    # Get current step index
    row = conn.execute(
        "SELECT COALESCE(MAX(step_index), -1) FROM traces WHERE task_run_id=?",
        (data["task_run_id"],)
    ).fetchone()
    step_index = (row[0] or -1) + 1
    conn.execute(
        "INSERT INTO traces (task_run_id, step_index, action, detail, timestamp) VALUES (?,?,?,?,?)",
        (data["task_run_id"], step_index, data["action"], data.get("detail", ""), data["timestamp"])
    )
    conn.commit()
    conn.close()


def get_all_evaluations() -> List[Dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT e.*,
               COUNT(tr.id)         AS run_count,
               ROUND(AVG(tr.final_score), 1) AS avg_score,
               SUM(CASE WHEN tr.status='passed' THEN 1 ELSE 0 END) AS passed_count
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
        traces = conn.execute(
            "SELECT * FROM traces WHERE task_run_id=? ORDER BY step_index", (tr_dict["id"],)
        ).fetchall()
        tr_dict["traces"] = [dict(t) for t in traces]
        result["task_runs"].append(tr_dict)

    conn.close()
    return result
