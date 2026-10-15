"""
AgentEval — Task Registry
Loads task metadata and files from the tasks/ directory.
"""
import json
import os
from typing import Dict, List, Optional

TASKS_DIR = os.path.dirname(os.path.abspath(__file__))

TASK_IDS = [
    "discount",
    "tax",
    "api_endpoint",
    "sql_injection",
    "db_constraint",
    "orders",
    "rate_limiter",
    "jwt_auth",
    "cache_lru",
    "data_pipeline",
]

_CACHE: Dict[str, Dict] = {}


def load_task(task_id: str) -> Dict:
    """Load a single task by ID. Results are cached."""
    if task_id in _CACHE:
        return _CACHE[task_id]

    task_dir = os.path.join(TASKS_DIR, task_id)
    meta_path = os.path.join(task_dir, "task.json")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    app_path = os.path.join(task_dir, "app.py")
    with open(app_path, encoding="utf-8") as f:
        meta["starter_code"] = f.read()

    visible_test_path = os.path.join(task_dir, "tests", "visible_test.py")
    if os.path.exists(visible_test_path):
        with open(visible_test_path, encoding="utf-8") as f:
            meta["visible_test"] = f.read()
    else:
        meta["visible_test"] = ""

    meta["task_dir"] = task_dir
    _CACHE[task_id] = meta
    return meta


def load_all_tasks() -> List[Dict]:
    """Load all tasks in order."""
    tasks = []
    for task_id in TASK_IDS:
        try:
            task = load_task(task_id)
            # Strip starter_code and visible_test from list view (too large)
            summary = {k: v for k, v in task.items() if k not in ("starter_code", "visible_test", "task_dir")}
            tasks.append(summary)
        except Exception as exc:
            print(f"[registry] Warning: could not load task '{task_id}': {exc}")
    return tasks
