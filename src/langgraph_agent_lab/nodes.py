"""Node implementations for the LangGraph workflow.

Each function returns a partial state update and does not mutate the input state.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from .state import AgentState, ApprovalDecision, Route, make_event

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def intake_node(state: AgentState) -> dict:
    """Normalize query, flag common PII patterns, and attach lightweight metadata."""
    raw = state.get("query", "")
    query = " ".join(raw.split()).strip()
    pii_flags: dict[str, bool] = {
        "email_like": bool(_EMAIL.search(query)),
        "phone_like": bool(_PHONE.search(query)),
    }
    preview = query if len(query) <= 80 else query[:77] + "..."
    return {
        "query": query,
        "messages": [f"intake:{preview}"],
        "events": [
            make_event(
                "intake",
                "completed",
                "query normalized",
                pii_flags=pii_flags,
            )
        ],
    }


def classify_node(state: AgentState) -> dict:
    """Keyword and token policy: risky > tool > missing_info > error > simple."""
    raw = state.get("query", "")
    tokens = _tokens(raw)
    query_lower = raw.lower()

    risky_kw = {"refund", "delete", "send", "cancel", "remove", "revoke"}
    tool_kw = {"status", "order", "lookup", "check", "track", "find", "search"}
    error_kw = {"timeout", "fail", "failure", "error", "crash", "unavailable"}

    route = Route.SIMPLE
    risk_level = "low"

    if tokens & risky_kw:
        route = Route.RISKY
        risk_level = "high"
    elif tokens & tool_kw:
        route = Route.TOOL
        risk_level = "medium"
    elif len(tokens) < 5 and (tokens & {"it", "this", "that", "them"}):
        route = Route.MISSING_INFO
        risk_level = "low"
    elif tokens & error_kw or any(w in query_lower for w in ("time out", "timed out")):
        route = Route.ERROR
        risk_level = "medium"

    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"route={route.value}")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for concrete identifiers instead of guessing."""
    q = state.get("query", "").strip()
    route = state.get("route", "")
    approval = state.get("approval") or {}
    if approval.get("action") == "reject":
        question = (
            "The proposed action was rejected. Please describe the allowed resolution "
            "or the ticket id you want us to follow."
        )
    elif route == Route.MISSING_INFO.value:
        question = (
            f"Your message ({q!r}) is too vague. Which product, order id, account email, "
            "or error text should we use?"
        )
    else:
        question = "Please add the missing order id, account identifier, or expected outcome."
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "clarification requested")],
    }


def tool_node(state: AgentState) -> dict:
    """Idempotent mock tool: JSON lines keyed by scenario and attempt."""
    attempt = int(state.get("attempt", 0))
    scenario_id = state.get("scenario_id", "unknown")
    route = state.get("route", "")
    max_attempts = int(state.get("max_attempts", 3))

    transient = route == Route.ERROR.value and attempt <= 1 and max_attempts > 1
    if transient:
        payload = {
            "tool": "mock_support_api",
            "idempotency_key": f"{scenario_id}:{attempt}",
            "status": "error",
            "code": "TRANSIENT",
            "message": "upstream timeout",
            "attempt": attempt,
        }
    else:
        payload = {
            "tool": "mock_support_api",
            "idempotency_key": f"{scenario_id}:{attempt}",
            "status": "ok",
            "data": {"scenario_id": scenario_id, "resolved": True},
            "attempt": attempt,
        }
    line = json.dumps(payload, sort_keys=True)
    ev = make_event(
        "tool",
        "completed",
        "tool executed",
        idempotency_key=payload["idempotency_key"],
    )
    return {"tool_results": [line], "events": [ev]}


def risky_action_node(state: AgentState) -> dict:
    """Summarize the risky request with evidence for HITL."""
    q = state.get("query", "").strip()
    proposed = (
        f"Review destructive or customer-visible action derived from query={q!r}. "
        "Verify policy, account id, and audit trail before execution."
    )
    return {
        "proposed_action": proposed,
        "events": [
            make_event(
                "risky_action",
                "pending_approval",
                "approval required",
                evidence_snippet=q[:120],
            )
        ],
    }


def approval_node(state: AgentState) -> dict:
    """HITL: optional LangGraph interrupt, otherwise mock approve.

    Supports approve / reject / edit via interrupt payload or env ``MOCK_APPROVAL_ACTION``.
    """
    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt(
            {
                "proposed_action": state.get("proposed_action"),
                "risk_level": state.get("risk_level"),
            }
        )
        decision = _parse_approval_value(value)
    else:
        action = os.getenv("MOCK_APPROVAL_ACTION", "approve").lower()
        if action == "reject":
            decision = ApprovalDecision(
                approved=False,
                action="reject",
                comment="mock rejection for tests",
            )
        elif action == "edit":
            decision = ApprovalDecision(
                approved=True,
                action="edit",
                comment="mock edit",
                edited_payload=str(state.get("proposed_action", "")) + " | edited-by-mock",
            )
        else:
            decision = ApprovalDecision(
                approved=True,
                action="approve",
                comment="mock approval for lab",
            )

    ev_msg = f"action={decision.action} approved={decision.approved}"
    updates: dict = {
        "approval": decision.model_dump(),
        "events": [make_event("approval", "completed", ev_msg)],
    }
    if decision.action == "edit" and decision.edited_payload:
        updates["proposed_action"] = decision.edited_payload
    return updates


def _parse_approval_value(value: object) -> ApprovalDecision:
    if isinstance(value, dict):
        action = str(value.get("action", "approve")).lower()
        if action not in ("approve", "reject", "edit"):
            action = "approve"
        approved = bool(value.get("approved", action != "reject"))
        action_t = cast(Literal["approve", "reject", "edit"], action)
        return ApprovalDecision(
            approved=approved,
            reviewer=str(value.get("reviewer", "interrupt-user")),
            comment=str(value.get("comment", "")),
            action=action_t,
            edited_payload=value.get("edited_payload") if value.get("edited_payload") else None,
        )
    return ApprovalDecision(
        approved=bool(value),
        action="approve",
        comment="interrupt truthy value",
    )


def retry_or_fallback_node(state: AgentState) -> dict:
    """Bounded retry with exponential backoff metadata (milliseconds, capped)."""
    prev = int(state.get("attempt", 0))
    attempt = prev + 1
    backoff_ms = min(30_000, int(100 * (2 ** max(0, attempt - 1))))
    err = f"transient failure attempt={attempt} backoff_ms={backoff_ms}"
    return {
        "attempt": attempt,
        "errors": [err],
        "events": [
            make_event(
                "retry",
                "completed",
                "retry attempt recorded",
                attempt=attempt,
                backoff_ms=backoff_ms,
            )
        ],
    }


def answer_node(state: AgentState) -> dict:
    """Ground answers in the latest structured tool result and approval context."""
    tool_results = state.get("tool_results", [])
    approval = state.get("approval") or {}
    route = state.get("route", "")
    latest = tool_results[-1] if tool_results else ""
    parsed: dict | None = None
    if latest:
        try:
            parsed = json.loads(latest)
        except json.JSONDecodeError:
            parsed = None

    if route == Route.SIMPLE.value:
        answer = (
            "Here are the usual steps: use the self-service password reset link, "
            "check spam for the email, and contact support if the link expires."
        )
    elif parsed and parsed.get("status") == "ok":
        parts = [f"Tool status=ok for {parsed.get('idempotency_key', 'run')}."]
        if approval.get("approved"):
            parts.append("Recorded approval before executing the checked action.")
        answer = " ".join(parts)
    elif latest:
        answer = f"Based on the latest tool payload: {latest}"
    else:
        answer = "No tool data was required; response is based on the classified route only."

    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Structured validation of tool JSON (no LLM required for the lab)."""
    tool_results = state.get("tool_results", [])
    latest = tool_results[-1] if tool_results else ""
    try:
        payload = json.loads(latest) if latest else {}
    except json.JSONDecodeError:
        return {
            "evaluation_result": "needs_retry",
            "events": [make_event("evaluate", "failed", "tool payload is not valid JSON")],
        }
    status = str(payload.get("status", "")).lower()
    if status == "error" or payload.get("code") == "TRANSIENT":
        return {
            "evaluation_result": "needs_retry",
            "events": [make_event("evaluate", "completed", "structured result indicates retry")],
        }
    return {
        "evaluation_result": "success",
        "events": [make_event("evaluate", "completed", "structured result ok")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Escalate exhausted retries to a lightweight dead-letter log (local JSONL)."""
    scenario_id = state.get("scenario_id", "unknown")
    dl_path = Path(os.getenv("DEAD_LETTER_PATH", "outputs/dead_letter.jsonl"))
    dl_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "scenario_id": scenario_id,
        "thread_id": state.get("thread_id"),
        "attempt": state.get("attempt"),
        "errors": list(state.get("errors", []) or []),
    }
    with dl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    msg = (
        "Request could not be completed after maximum retry attempts. "
        f"Escalated to dead-letter log at {dl_path.as_posix()} for manual review."
    )
    return {
        "final_answer": msg,
        "events": [make_event("dead_letter", "completed", f"recorded scenario={scenario_id}")],
    }


def finalize_node(state: AgentState) -> dict:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
