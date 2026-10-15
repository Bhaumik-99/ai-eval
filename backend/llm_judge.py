"""
AgentEval — LLM-as-a-Judge Module

Provides a complementary behavioral assessment of candidate solutions using
an LLM judge. This module does NOT replace deterministic test results — it
augments them by evaluating aspects that automated tests cannot easily verify:

  - Task compliance and intent matching
  - Code design quality and idiomatic style
  - Security awareness and defensive coding
  - Edge-case reasoning and robustness
  - Documentation quality and clarity

Architecture:
  1. A versioned rubric defines scoring dimensions and criteria.
  2. The judge receives: task spec, expected behavior, candidate code, test evidence.
  3. It returns validated structured JSON with scores, evidence, and failure reasons.
  4. Judge scores are recorded alongside deterministic scores for comparison.
  5. Agreement metrics can be computed against human-labeled ground truth.

Configuration:
  - AGENTEVAL_JUDGE_MODEL: Ollama model for judging (default: same as eval model)
  - AGENTEVAL_JUDGE_ENABLED: Set to "1" to enable (default: "1")
  - AGENTEVAL_JUDGE_BASE_URL: Ollama base URL (default: http://localhost:11434)
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JUDGE_ENABLED = os.environ.get("AGENTEVAL_JUDGE_ENABLED", "1") == "1"
JUDGE_MODEL = os.environ.get("AGENTEVAL_JUDGE_MODEL", "qwen2.5-coder:7b")
JUDGE_BASE_URL = os.environ.get("AGENTEVAL_JUDGE_BASE_URL", "http://localhost:11434")
JUDGE_TEMPERATURE = float(os.environ.get("AGENTEVAL_JUDGE_TEMPERATURE", "0.1"))
JUDGE_TIMEOUT = int(os.environ.get("AGENTEVAL_JUDGE_TIMEOUT", "90"))

# ---------------------------------------------------------------------------
# Rubric — Versioned scoring dimensions
# ---------------------------------------------------------------------------

RUBRIC_VERSION = "1.0.0"

RUBRIC_DIMENSIONS = {
    "task_compliance": {
        "description": "How well the solution addresses all requirements stated in the task specification.",
        "weight": 0.35,
        "scale": "0-10",
        "criteria": {
            "10": "All requirements fully implemented correctly",
            "7-9": "Most requirements met, minor gaps",
            "4-6": "Some requirements met but significant gaps",
            "1-3": "Few requirements addressed",
            "0": "Does not address the task at all",
        },
    },
    "correctness": {
        "description": "Logical correctness beyond what tests cover — edge cases, boundary conditions, error states.",
        "weight": 0.25,
        "scale": "0-10",
        "criteria": {
            "10": "Handles all edge cases, boundary conditions, and error states correctly",
            "7-9": "Handles most edge cases, minor oversights",
            "4-6": "Some edge cases missed, potential runtime errors",
            "1-3": "Major logical errors or missing error handling",
            "0": "Fundamentally broken logic",
        },
    },
    "security_awareness": {
        "description": "Defensive coding practices — input validation, injection prevention, authorization.",
        "weight": 0.20,
        "scale": "0-10",
        "criteria": {
            "10": "Comprehensive input validation, no injection vectors, proper authorization",
            "7-9": "Good security practices with minor gaps",
            "4-6": "Basic validation present but incomplete",
            "1-3": "Minimal security consideration",
            "0": "Introduces security vulnerabilities",
        },
    },
    "code_quality": {
        "description": "Readability, structure, naming, documentation, and idiomatic Python style.",
        "weight": 0.20,
        "scale": "0-10",
        "criteria": {
            "10": "Clean, well-documented, idiomatic Python with clear structure",
            "7-9": "Good code quality with minor style issues",
            "4-6": "Functional but messy or poorly documented",
            "1-3": "Hard to read, poor naming, no documentation",
            "0": "Unreadable or obfuscated",
        },
    },
}

# ---------------------------------------------------------------------------
# Judge Prompt Template
# ---------------------------------------------------------------------------

JUDGE_PROMPT_TEMPLATE = """\
You are an expert code reviewer acting as a judge for an AI agent evaluation system.

## Your Task
Evaluate the candidate solution against the task specification. You must assess
behavioral compliance and code quality — NOT just whether tests pass.

## Rubric (v{rubric_version})
Score each dimension from 0 to 10:

{rubric_text}

## Task Specification
**Task ID**: {task_id}
**Task Name**: {task_name}
**Category**: {category}
**Description**: {description}

## Expected Behavior
{expected_behavior}

## Candidate Solution
```python
{candidate_code}
```

## Deterministic Test Evidence
{test_evidence}

## Instructions
1. Analyze the candidate solution against EACH rubric dimension.
2. For each dimension, provide:
   - A numeric score (0-10)
   - Specific evidence from the code supporting your score
   - Any failure reasons if score < 7
3. Provide an overall assessment summarizing key strengths and weaknesses.
4. Be fair but rigorous. Tests passing does NOT mean the code is good.
5. Tests failing does NOT mean the code is entirely bad — assess partial credit.

## Required Output Format
You MUST respond with ONLY valid JSON matching this exact structure:
```json
{{
  "dimensions": {{
    "task_compliance": {{
      "score": <0-10>,
      "evidence": "<specific evidence from code>",
      "failure_reasons": ["<reason1>", "<reason2>"] or []
    }},
    "correctness": {{
      "score": <0-10>,
      "evidence": "<specific evidence>",
      "failure_reasons": []
    }},
    "security_awareness": {{
      "score": <0-10>,
      "evidence": "<specific evidence>",
      "failure_reasons": []
    }},
    "code_quality": {{
      "score": <0-10>,
      "evidence": "<specific evidence>",
      "failure_reasons": []
    }}
  }},
  "overall_assessment": "<2-3 sentence summary>",
  "weighted_score": <0-100 float>
}}
```

Respond with ONLY the JSON object, no other text.
"""


def _build_rubric_text() -> str:
    """Format the rubric dimensions into readable text for the prompt."""
    lines = []
    for dim_id, dim in RUBRIC_DIMENSIONS.items():
        lines.append(f"### {dim_id} (weight: {dim['weight']:.0%})")
        lines.append(f"{dim['description']}")
        for level, desc in dim["criteria"].items():
            lines.append(f"  - **{level}**: {desc}")
        lines.append("")
    return "\n".join(lines)


def _build_test_evidence(test_results: List[Dict]) -> str:
    """Format deterministic test results as evidence for the judge."""
    if not test_results:
        return "No deterministic test results available."

    passed = [t for t in test_results if t.get("passed")]
    failed = [t for t in test_results if not t.get("passed")]

    lines = [f"**{len(passed)}/{len(test_results)} tests passed**\n"]

    if passed:
        lines.append("Passed tests:")
        for t in passed:
            lines.append(f"  ✓ {t['name']}")

    if failed:
        lines.append("\nFailed tests:")
        for t in failed:
            error_snippet = (t.get("error") or "No details")[:200]
            lines.append(f"  ✗ {t['name']}: {error_snippet}")

    return "\n".join(lines)


def _build_expected_behavior(task_meta: Dict) -> str:
    """Build expected behavior description from task metadata."""
    parts = [task_meta.get("description", "No description provided.")]

    if task_meta.get("visible_hint"):
        parts.append(f"\n**Hint**: {task_meta['visible_hint']}")

    if task_meta.get("tags"):
        parts.append(f"\n**Relevant areas**: {', '.join(task_meta['tags'])}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core Judge Function
# ---------------------------------------------------------------------------

_OLLAMA_ALIVE: Optional[bool] = None
_LAST_OLLAMA_CHECK: float = 0.0

def is_ollama_alive() -> bool:
    """Fast check whether Ollama daemon is reachable on JUDGE_BASE_URL (cached 15s)."""
    global _OLLAMA_ALIVE, _LAST_OLLAMA_CHECK
    now = time.monotonic()
    if _OLLAMA_ALIVE is not None and (now - _LAST_OLLAMA_CHECK) < 15.0:
        return _OLLAMA_ALIVE
    import requests
    try:
        resp = requests.get(f"{JUDGE_BASE_URL}/api/tags", timeout=0.6)
        _OLLAMA_ALIVE = (resp.status_code == 200)
    except Exception:
        _OLLAMA_ALIVE = False
    _LAST_OLLAMA_CHECK = now
    return _OLLAMA_ALIVE


def judge_solution(
    task_id: str,
    candidate_code: str,
    task_meta: Dict,
    test_results: List[Dict],
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    if not JUDGE_ENABLED:
        return _disabled_result()

    model = model_override or JUDGE_MODEL

    if not is_ollama_alive():
        return _error_result(model, f"Ollama service is not reachable on {JUDGE_BASE_URL}")

    # Build the prompt
    rubric_text = _build_rubric_text()
    test_evidence = _build_test_evidence(test_results)
    expected_behavior = _build_expected_behavior(task_meta)

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        rubric_version=RUBRIC_VERSION,
        rubric_text=rubric_text,
        task_id=task_id,
        task_name=task_meta.get("name", task_id),
        category=task_meta.get("category", "unknown"),
        description=task_meta.get("description", ""),
        expected_behavior=expected_behavior,
        candidate_code=candidate_code[:8000],  # Truncate very long solutions
        test_evidence=test_evidence,
    )

    start_time = time.monotonic()

    try:
        raw_response = _call_ollama(model, prompt)
    except Exception as exc:
        logger.warning("LLM judge call failed for task %s: %s", task_id, exc)
        return _error_result(model, str(exc))

    latency_ms = int((time.monotonic() - start_time) * 1000)

    # Parse and validate the response
    parsed = _parse_judge_response(raw_response)
    if parsed is None:
        logger.warning(
            "LLM judge returned unparseable response for task %s (len=%d)",
            task_id, len(raw_response),
        )
        return _error_result(
            model,
            "Failed to parse judge response as valid JSON",
            raw_response=raw_response[:2000],
            latency_ms=latency_ms,
        )

    # Validate and clamp scores
    dimensions = _validate_dimensions(parsed.get("dimensions", {}))
    weighted_score = _compute_weighted_score(dimensions)

    return {
        "enabled": True,
        "rubric_version": RUBRIC_VERSION,
        "model": model,
        "prompt_version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions,
        "weighted_score": round(weighted_score, 1),
        "overall_assessment": str(parsed.get("overall_assessment", ""))[:1000],
        "raw_response": raw_response[:3000],
        "latency_ms": latency_ms,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Ollama Integration
# ---------------------------------------------------------------------------

def _call_ollama(model: str, prompt: str) -> str:
    """Call Ollama's generate API and return the response text."""
    import requests

    response = requests.post(
        f"{JUDGE_BASE_URL}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": JUDGE_TEMPERATURE,
                "num_predict": 4096,
            },
            "format": "json",
        },
        timeout=JUDGE_TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("response", "")


# ---------------------------------------------------------------------------
# Response Parsing & Validation
# ---------------------------------------------------------------------------

def _parse_judge_response(raw: str) -> Optional[Dict]:
    """
    Parse the LLM's JSON response. Handles common issues like
    markdown code fences around the JSON.
    """
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fence
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } block
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(raw[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _validate_dimensions(raw_dims: Dict) -> Dict[str, Dict]:
    """
    Validate and sanitize dimension scores from the judge response.
    Ensures all expected dimensions are present with valid scores.
    """
    validated = {}
    for dim_id in RUBRIC_DIMENSIONS:
        dim_data = raw_dims.get(dim_id, {})
        if not isinstance(dim_data, dict):
            dim_data = {}

        score = dim_data.get("score", 0)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        # Clamp to valid range
        score = max(0.0, min(10.0, score))

        evidence = str(dim_data.get("evidence", "No evidence provided"))[:500]

        failure_reasons = dim_data.get("failure_reasons", [])
        if not isinstance(failure_reasons, list):
            failure_reasons = []
        failure_reasons = [str(r)[:200] for r in failure_reasons[:5]]

        validated[dim_id] = {
            "score": round(score, 1),
            "evidence": evidence,
            "failure_reasons": failure_reasons,
        }

    return validated


def _compute_weighted_score(dimensions: Dict[str, Dict]) -> float:
    """Compute the weighted aggregate score (0-100 scale)."""
    total = 0.0
    for dim_id, dim_meta in RUBRIC_DIMENSIONS.items():
        dim_score = dimensions.get(dim_id, {}).get("score", 0.0)
        total += dim_score * dim_meta["weight"]
    # Scale from 0-10 to 0-100
    return total * 10


# ---------------------------------------------------------------------------
# Agreement & Calibration Tracking
# ---------------------------------------------------------------------------

def compute_agreement(
    judge_scores: List[Dict],
    deterministic_scores: List[float],
    human_labels: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Compute agreement metrics between judge scores, deterministic test scores,
    and optionally human-labeled ground truth.

    This is used for calibration and monitoring judge quality over time.

    Args:
        judge_scores: List of judge result dicts (with "weighted_score").
        deterministic_scores: List of deterministic final_scores (same order).
        human_labels: Optional list of human-assigned scores (same order).

    Returns:
        Agreement metrics dict with correlation, MAE, and pass/fail agreement.
    """
    if not judge_scores or not deterministic_scores:
        return {"error": "Insufficient data for agreement computation"}

    n = min(len(judge_scores), len(deterministic_scores))
    judge_vals = [s.get("weighted_score", 0.0) for s in judge_scores[:n]]
    det_vals = list(deterministic_scores[:n])

    metrics = {
        "n_samples": n,
        "judge_mean": round(sum(judge_vals) / n, 1) if n else 0,
        "deterministic_mean": round(sum(det_vals) / n, 1) if n else 0,
    }

    # Mean Absolute Error between judge and deterministic
    mae = sum(abs(j - d) for j, d in zip(judge_vals, det_vals)) / n
    metrics["judge_vs_deterministic_mae"] = round(mae, 1)

    # Pass/fail agreement (threshold = 70)
    judge_pass = [1 if s >= 70 else 0 for s in judge_vals]
    det_pass = [1 if s >= 70 else 0 for s in det_vals]
    agreement = sum(1 for j, d in zip(judge_pass, det_pass) if j == d) / n
    metrics["pass_fail_agreement"] = round(agreement, 3)

    # False positive rate (judge says pass, deterministic says fail)
    det_fails = sum(1 for d in det_pass if d == 0)
    if det_fails > 0:
        false_positives = sum(1 for j, d in zip(judge_pass, det_pass) if j == 1 and d == 0)
        metrics["false_positive_rate"] = round(false_positives / det_fails, 3)
    else:
        metrics["false_positive_rate"] = 0.0

    # False negative rate (judge says fail, deterministic says pass)
    det_passes = sum(1 for d in det_pass if d == 1)
    if det_passes > 0:
        false_negatives = sum(1 for j, d in zip(judge_pass, det_pass) if j == 0 and d == 1)
        metrics["false_negative_rate"] = round(false_negatives / det_passes, 3)
    else:
        metrics["false_negative_rate"] = 0.0

    # Human label agreement (if available)
    if human_labels and len(human_labels) >= n:
        human_vals = list(human_labels[:n])
        human_mae = sum(abs(j - h) for j, h in zip(judge_vals, human_vals)) / n
        metrics["judge_vs_human_mae"] = round(human_mae, 1)

        human_pass = [1 if s >= 70 else 0 for s in human_vals]
        human_agreement = sum(1 for j, h in zip(judge_pass, human_pass) if j == h) / n
        metrics["judge_vs_human_agreement"] = round(human_agreement, 3)

    return metrics


# ---------------------------------------------------------------------------
# Result Helpers
# ---------------------------------------------------------------------------

def _disabled_result() -> Dict[str, Any]:
    """Return a placeholder result when the judge is disabled."""
    return {
        "enabled": False,
        "rubric_version": RUBRIC_VERSION,
        "model": None,
        "prompt_version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dimensions": {},
        "weighted_score": 0.0,
        "overall_assessment": "LLM judge is disabled.",
        "raw_response": "",
        "latency_ms": 0,
        "error": None,
    }


def _error_result(
    model: str,
    error_msg: str,
    raw_response: str = "",
    latency_ms: int = 0,
) -> Dict[str, Any]:
    """Return an error result when the judge call fails."""
    return {
        "enabled": True,
        "rubric_version": RUBRIC_VERSION,
        "model": model,
        "prompt_version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dimensions": {
            dim_id: {"score": 0, "evidence": "Judge call failed", "failure_reasons": [error_msg]}
            for dim_id in RUBRIC_DIMENSIONS
        },
        "weighted_score": 0.0,
        "overall_assessment": f"Judge evaluation failed: {error_msg}",
        "raw_response": raw_response,
        "latency_ms": latency_ms,
        "error": error_msg,
    }
