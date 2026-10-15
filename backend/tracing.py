"""
AgentEval — Genuine Agent Tracing System

Captures real execution events during agent evaluation runs. Every trace event
corresponds to a real model request, tool call, visible/hidden test execution,
recovery attempt, or failure diagnosis with concrete latency, exit code, token usage,
and step sequence.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).parent.parent
TRACES_DIR = ROOT_DIR / "traces"
TRACES_DIR.mkdir(exist_ok=True)

PROMPT_VERSION = "2.1.0"
MODEL_COST_PER_1K_TOKENS = {
    "qwen2.5-coder:7b": {"prompt": 0.0002, "completion": 0.0004},
    "llama3:8b": {"prompt": 0.0002, "completion": 0.0004},
    "deepseek-coder:6.7b": {"prompt": 0.00015, "completion": 0.0003},
    "mock": {"prompt": 0.00005, "completion": 0.0001},
}


class TraceEvent(BaseModel):
    run_id: str
    task_id: str
    step_index: int
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: str  # MODEL_REQUEST, MODEL_RESPONSE, TOOL_CALL, TOOL_RESULT, TOOL_ERROR, TEST_EXECUTION, etc.
    action: str
    detail: str
    model_name: Optional[str] = None
    prompt_version: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None
    tool_error: Optional[str] = None
    exit_code: Optional[int] = None
    duration_ms: float = 0.0
    token_usage: Optional[Dict[str, int]] = None  # {prompt_tokens, completion_tokens, total_tokens}
    estimated_cost_usd: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """Rough character-to-token heuristic (4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def calculate_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_COST_PER_1K_TOKENS.get(model_name, MODEL_COST_PER_1K_TOKENS["mock"])
    prompt_cost = (prompt_tokens / 1000.0) * rates["prompt"]
    completion_cost = (completion_tokens / 1000.0) * rates["completion"]
    return round(prompt_cost + completion_cost, 6)


class TraceRecorder:
    """
    Records genuine agent traces into DB, filesystem, and dispatches to live listeners.
    """

    def __init__(self, run_id: str, task_id: str, on_event: Optional[Callable[[Dict], Any]] = None):
        self.run_id = run_id
        self.task_id = task_id
        self.step_index = 0
        self.events: List[TraceEvent] = []
        self.on_event = on_event
        self.total_tokens = {"prompt": 0, "completion": 0, "total": 0}
        self.total_cost_usd = 0.0
        self.total_duration_ms = 0.0

    def record_event(
        self,
        event_type: str,
        action: str,
        detail: str,
        model_name: Optional[str] = None,
        prompt_version: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Any] = None,
        tool_error: Optional[str] = None,
        exit_code: Optional[int] = None,
        duration_ms: float = 0.0,
        token_usage: Optional[Dict[str, int]] = None,
        cost_usd: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        self.step_index += 1
        self.total_duration_ms += duration_ms

        if token_usage:
            p_tok = token_usage.get("prompt_tokens", 0)
            c_tok = token_usage.get("completion_tokens", 0)
            self.total_tokens["prompt"] += p_tok
            self.total_tokens["completion"] += c_tok
            self.total_tokens["total"] += p_tok + c_tok
            if not cost_usd and model_name:
                cost_usd = calculate_cost(model_name, p_tok, c_tok)

        self.total_cost_usd += cost_usd

        evt = TraceEvent(
            run_id=self.run_id,
            task_id=self.task_id,
            step_index=self.step_index,
            event_type=event_type,
            action=action,
            detail=detail,
            model_name=model_name,
            prompt_version=prompt_version,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            tool_error=tool_error,
            exit_code=exit_code,
            duration_ms=round(duration_ms, 2),
            token_usage=token_usage,
            estimated_cost_usd=cost_usd,
            metadata=metadata or {},
        )
        self.events.append(evt)

        # Notify callback (e.g. WebSocket broadcast)
        if self.on_event:
            try:
                self.on_event(evt.model_dump())
            except Exception as e:
                print(f"[TraceRecorder] Callback failed: {e}")

        return evt

    def save_to_file(self, candidate_solution: Optional[str] = None) -> Path:
        """Persist full trace to reproducible json file on disk."""
        filepath = TRACES_DIR / f"{self.run_id}.json"
        data = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "prompt_version": PROMPT_VERSION,
            "step_count": len(self.events),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "candidate_solution": candidate_solution,
            "events": [e.model_dump() for e in self.events],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return filepath
