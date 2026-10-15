"""
AgentEval — Agent Module
Connects to Ollama for live evaluation, or uses the MockAgent for demo mode.

The MockAgent returns pre-written partial solutions that produce realistic
score distributions — some tasks pass, some fail, with varied failure categories.
"""
import re
import textwrap
import time
from typing import Dict

import requests


# ---------------------------------------------------------------------------
# Ollama Agent
# ---------------------------------------------------------------------------

class OllamaAgent:
    """Calls a locally-running Ollama model to generate a solution."""

    def __init__(self, model: str = "qwen2.5-coder:7b"):
        self.model = model
        self.base_url = "http://localhost:11434"

    def solve(self, task: Dict) -> str:
        """Generate a solution for the given task. Returns the full app.py content."""
        prompt = self._build_prompt(task)

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 2048},
                },
                timeout=120,
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Cannot connect to Ollama at localhost:11434. "
                "Is Ollama running? Try: ollama serve"
            )

        return self._extract_code(raw, task["starter_code"])

    def _build_prompt(self, task: Dict) -> str:
        return textwrap.dedent(f"""
            You are an expert Python developer. Fix the code below according to the task description.

            TASK: {task['description']}

            CURRENT CODE (app.py):
            ```python
            {task['starter_code']}
            ```

            VISIBLE TEST (for reference):
            ```python
            {task.get('visible_test', '# No visible test provided')}
            ```

            Instructions:
            - Output ONLY the complete fixed Python file (app.py)
            - Do NOT add any explanation, markdown headers, or commentary
            - Wrap your code in ```python ... ``` tags
            - Make sure all existing functions still work (no regressions)
        """).strip()

    def _extract_code(self, raw: str, fallback: str) -> str:
        """Extract Python code block from LLM response."""
        match = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()
        # If no code block, return the raw text (might still work)
        stripped = raw.strip()
        if stripped.startswith("def ") or stripped.startswith("from ") or stripped.startswith("import "):
            return stripped
        return fallback


# ---------------------------------------------------------------------------
# Mock Agent — deterministic demo solutions
# ---------------------------------------------------------------------------

# Pre-written solutions for each task that produce realistic score distributions.
# These are deliberately imperfect to show the grader catching failures.

_MOCK_SOLUTIONS: Dict[str, str] = {

    "discount": textwrap.dedent("""\
        def calculate_discount(price, percentage):
            \"\"\"Calculate the discounted price with input validation.

            Args:
                price: The original price (must be non-negative)
                percentage: The discount percentage 0-100

            Returns:
                The discounted price as a float
            \"\"\"
            if not isinstance(percentage, (int, float)):
                raise TypeError("Percentage must be a number")
            if percentage < 0 or percentage > 100:
                raise ValueError(
                    f"Percentage must be between 0 and 100, got {percentage}"
                )
            # NOTE: agent forgot to validate negative price
            discount_amount = price * percentage / 100
            return round(price - discount_amount, 2)
    """),

    "tax": textwrap.dedent("""\
        def calculate_tax(income, tax_rate):
            \"\"\"Calculate the tax amount from income and tax rate.\"\"\"
            if income < 0:
                return 0.0
            if tax_rate < 0 or tax_rate > 100:
                raise ValueError("tax_rate must be between 0 and 100")
            # BUG still present: missing / 100 — agent only fixed the guard clauses
            return round(income * tax_rate, 2)
    """),

    "api_endpoint": textwrap.dedent("""\
        from fastapi import FastAPI, HTTPException
        from typing import Optional

        app = FastAPI(title="UserService API")

        USERS_DB = {
            1: {"id": 1, "name": "Alice Johnson", "email": "alice@example.com", "role": "admin", "active": True},
            2: {"id": 2, "name": "Bob Smith", "email": "bob@example.com", "role": "user", "active": True},
            3: {"id": 3, "name": "Charlie Brown", "email": "charlie@example.com", "role": "user", "active": False},
        }

        @app.get("/users")
        def get_all_users():
            \"\"\"Return all users.\"\"\"
            return list(USERS_DB.values())

        @app.get("/users/{user_id}")
        def get_user(user_id: int):
            \"\"\"Return a single user by ID.\"\"\"
            if user_id not in USERS_DB:
                raise HTTPException(status_code=404, detail=f"User {user_id} not found")
            return USERS_DB[user_id]
    """),

    "sql_injection": textwrap.dedent("""\
        import sqlite3


        def get_db():
            \"\"\"Get an in-memory SQLite database with pre-populated test data.\"\"\"
            conn = sqlite3.connect(":memory:")
            conn.execute(\"\"\"
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user'
                )
            \"\"\")
            conn.execute("INSERT INTO users VALUES (1, 'admin', 'hashed_admin_pass_xyz', 'admin')")
            conn.execute("INSERT INTO users VALUES (2, 'alice', 'hashed_alice_pass_abc', 'user')")
            conn.execute("INSERT INTO users VALUES (3, 'bob', 'hashed_bob_pass_def', 'user')")
            conn.commit()
            return conn


        def authenticate_user(username: str, password: str):
            \"\"\"Authenticate using parameterized queries — SQL injection fixed.\"\"\"
            if not username or not password:
                return None
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE username=? AND password=?",
                (username, password),
            )
            user = cursor.fetchone()
            conn.close()
            return user


        def get_user_by_id(user_id: int, requesting_user_role: str):
            \"\"\"Get user data. Authorization check still missing — agent forgot to fix this.\"\"\"
            conn = get_db()
            cursor = conn.cursor()
            # BUG: authorization check not implemented
            cursor.execute("SELECT id, username, role FROM users WHERE id=?", (user_id,))
            result = cursor.fetchone()
            conn.close()
            return result
    """),

    "db_constraint": textwrap.dedent("""\
        import sqlite3
        from typing import Optional, Tuple


        def init_db(conn: sqlite3.Connection) -> None:
            \"\"\"Initialize the database with a UNIQUE constraint on email.\"\"\"
            conn.execute(\"\"\"
                CREATE TABLE IF NOT EXISTS users (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    name  TEXT    NOT NULL,
                    email TEXT    NOT NULL UNIQUE
                )
            \"\"\")
            conn.commit()


        def create_user(name: str, email: str, conn: Optional[sqlite3.Connection] = None) -> int:
            \"\"\"Create a new user. Raises sqlite3.IntegrityError for duplicate email.\"\"\"
            if not email:
                raise ValueError("Email cannot be empty")
            if conn is None:
                conn = sqlite3.connect(":memory:")
                init_db(conn)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (name, email) VALUES (?, ?)", (name, email))
            conn.commit()
            return cursor.lastrowid


        def get_user_by_email(email: str, conn: sqlite3.Connection) -> Optional[Tuple]:
            \"\"\"Fetch a user by their email address.\"\"\"
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email=?", (email,))
            return cursor.fetchone()


        def get_all_users(conn: sqlite3.Connection):
            \"\"\"Return all users.\"\"\"
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users")
            return cursor.fetchall()
    """),

    "orders": textwrap.dedent("""\
        from typing import Dict, List, Optional


        DISCOUNT_CODES: Dict[str, float] = {
            "SAVE10": 10.0,
            "SAVE20": 20.0,
            "HALFOFF": 50.0,
        }


        def calculate_order_total(
            items: List[Dict],
            tax_rate: float = 8.0,
            discount_code: Optional[str] = None,
        ) -> Dict:
            \"\"\"Calculate the total for an order including tax and optional discount.\"\"\"
            subtotal = sum(item["price"] * item["quantity"] for item in items)

            discount_pct = DISCOUNT_CODES.get(discount_code, 0.0) if discount_code else 0.0
            # Fixed: now divides by 100 correctly
            discount_amount = subtotal * discount_pct / 100

            taxable_amount = subtotal - discount_amount
            # BUG (regression): agent accidentally multiplied tax_rate instead of dividing
            tax_amount = taxable_amount * tax_rate * 0.01  # same as /100, this is fine

            total = taxable_amount + tax_amount

            return {
                "subtotal": round(subtotal, 2),
                "tax": round(tax_amount, 2),
                "discount": round(discount_amount, 2),
                "total": round(total, 2),
            }
    """),

    "rate_limiter": textwrap.dedent("""\
        import threading
        import time


        class TokenBucket:
            \"\"\"Thread-safe Token Bucket Rate Limiter with input validation.\"\"\"

            def __init__(self, capacity: float, refill_rate: float):
                if capacity <= 0:
                    raise ValueError("Capacity must be positive")
                if refill_rate <= 0:
                    raise ValueError("Refill rate must be positive")
                self.capacity = float(capacity)
                self.refill_rate = float(refill_rate)
                self.tokens = float(capacity)
                self.last_refill = time.time()
                self.lock = threading.Lock()

            def _refill(self):
                now = time.time()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
                self.last_refill = now

            def consume(self, tokens: int = 1) -> bool:
                with self.lock:
                    self._refill()
                    if self.tokens >= tokens:
                        self.tokens -= tokens
                        return True
                    return False
    """),

    "jwt_auth": textwrap.dedent("""\
        import base64
        import hashlib
        import hmac
        import json
        import time
        from typing import Dict


        def _b64encode(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


        def _b64decode(s: str) -> bytes:
            padding = 4 - (len(s) % 4)
            if padding != 4:
                s += "=" * padding
            return base64.urlsafe_b64decode(s)


        def create_token(payload: Dict, secret: str, expires_in_sec: int = 3600) -> str:
            header = {"alg": "HS256", "typ": "JWT"}
            payload_copy = dict(payload)
            payload_copy["exp"] = int(time.time()) + expires_in_sec

            header_b64 = _b64encode(json.dumps(header).encode("utf-8"))
            payload_b64 = _b64encode(json.dumps(payload_copy).encode("utf-8"))
            msg = f"{header_b64}.{payload_b64}".encode("utf-8")

            sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
            sig_b64 = _b64encode(sig)
            return f"{header_b64}.{payload_b64}.{sig_b64}"


        def decode_token(token: str, secret: str) -> Dict:
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError("Invalid token structure")

            header_b64, payload_b64, sig_b64 = parts
            msg = f"{header_b64}.{payload_b64}".encode("utf-8")
            expected_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
            actual_sig = _b64decode(sig_b64)

            if not hmac.compare_digest(expected_sig, actual_sig):
                raise ValueError("Invalid signature")

            payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
            if "exp" in payload and time.time() > payload["exp"]:
                raise ValueError("Token has expired")
            return payload
    """),

    "cache_lru": textwrap.dedent("""\
        from collections import OrderedDict
        from typing import Any, Optional


        class LRUCache:
            \"\"\"Least Recently Used Cache using OrderedDict.\"\"\"

            def __init__(self, capacity: int):
                if capacity <= 0:
                    raise ValueError("Capacity must be positive")
                self.capacity = capacity
                self.cache = OrderedDict()

            def get(self, key: Any) -> Optional[Any]:
                if key not in self.cache:
                    return None
                self.cache.move_to_end(key)
                return self.cache[key]

            def put(self, key: Any, value: Any) -> None:
                if key in self.cache:
                    self.cache.move_to_end(key)
                self.cache[key] = value
                if len(self.cache) > self.capacity:
                    self.cache.popitem(last=False)
    """),

    "data_pipeline": textwrap.dedent("""\
        import re
        from typing import Dict, List


        def clean_records(records: List[Dict]) -> List[Dict]:
            \"\"\"Deduplicate, clean dirty strings, and validate records.\"\"\"
            by_id = {}
            for r in records:
                rec = dict(r)
                email = str(rec.get("email", "")).strip()
                if not ("@" in email and "." in email):
                    continue

                amount_raw = str(rec.get("amount", "0")).replace("$", "").replace(",", "").strip()
                try:
                    rec["amount"] = float(amount_raw)
                except ValueError:
                    continue

                if "status" not in rec or not rec["status"]:
                    rec["status"] = "active"

                by_id[int(rec["id"])] = rec

            return sorted(by_id.values(), key=lambda x: x["id"])
    """),
}


class MockAgent:
    """
    Returns pre-written partial solutions for demo mode.
    Produces realistic score distributions without needing Ollama.
    """

    def __init__(self, model: str = "mock", task_id: str = ""):
        self.model = model
        self.task_id = task_id

    def solve(self, task: Dict) -> str:
        task_id = task.get("id", self.task_id)
        if task_id not in _MOCK_SOLUTIONS:
            # Return the starter code unchanged (score ≈ 0)
            return task.get("starter_code", "# No solution generated")

        # Simulate thinking time
        time.sleep(1.5)
        return _MOCK_SOLUTIONS[task_id]
