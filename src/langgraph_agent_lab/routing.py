"""Routing functions for conditional edges."""

from __future__ import annotations

from .state import AgentState, Route


def route_after_classify(state: AgentState) -> str:
    """Map classified route to the next graph node.

    Unknown routes fall through to ``answer`` so the graph always progresses.
    """
    route = state.get("route") or Route.SIMPLE.value
    mapping = {
        Route.SIMPLE.value: "answer",
        Route.TOOL.value: "tool",
        Route.MISSING_INFO.value: "clarify",
        Route.RISKY.value: "risky_action",
        Route.ERROR.value: "retry",
    }
    return mapping.get(route, "answer")


def route_after_retry(state: AgentState) -> str:
    """Decide whether to retry the tool, or escalate to dead-letter."""
    if int(state.get("attempt", 0)) >= int(state.get("max_attempts", 3)):
        return "dead_letter"
    return "tool"


def route_after_evaluate(state: AgentState) -> str:
    """Branch after tool evaluation (structured ``evaluation_result``)."""
    if state.get("evaluation_result") == "needs_retry":
        return "retry"
    return "answer"


def route_after_approval(state: AgentState) -> str:
    """Continue to tool on approval or edit; send rejections back to clarify."""
    approval = state.get("approval") or {}
    action = str(approval.get("action", "approve"))
    if action == "reject" or not approval.get("approved", False):
        return "clarify"
    return "tool"
