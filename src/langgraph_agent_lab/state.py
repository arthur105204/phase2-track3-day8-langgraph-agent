"""State schema for the Day 08 LangGraph lab.

Students should extend the schema only when needed. Keep state lean and serializable.
"""

from __future__ import annotations

from enum import StrEnum
from operator import add
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator


class Route(StrEnum):
    SIMPLE = "simple"
    TOOL = "tool"
    MISSING_INFO = "missing_info"
    RISKY = "risky"
    ERROR = "error"
    DEAD_LETTER = "dead_letter"
    DONE = "done"


class LabEvent(BaseModel):
    """Append-only audit event for grading and debugging."""

    node: str
    event_type: str
    message: str
    latency_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    """Structured HITL outcome (mock or returned from interrupt())."""

    approved: bool = False
    reviewer: str = "mock-reviewer"
    comment: str = ""
    action: Literal["approve", "reject", "edit"] = "approve"
    edited_payload: str | None = None


class AgentState(TypedDict, total=False):
    """LangGraph state.

    Reducers (append-only via ``Annotated[..., add]``):
    - ``messages``: short trace strings for debugging.
    - ``tool_results``: structured JSON lines from tool_node (never mutate in place).
    - ``errors``: human-readable error strings across retries.
    - ``events``: serialized ``LabEvent`` dicts for metrics and grading.

    Overwritten each time a node returns an update for that key:
    - ``thread_id``, ``scenario_id``, ``query`` (intake may normalize ``query``).
    - ``route``, ``risk_level``, ``attempt``, ``max_attempts`` (routing / retry).
    - ``final_answer``, ``pending_question``, ``proposed_action``, ``approval``.
    - ``evaluation_result``: ``needs_retry`` | ``success`` | ``rejected`` — gate for the tool loop.
    """

    thread_id: str
    scenario_id: str
    query: str
    route: str
    risk_level: str
    attempt: int
    max_attempts: int
    final_answer: str | None
    pending_question: str | None
    proposed_action: str | None
    approval: dict[str, Any] | None
    evaluation_result: str | None
    messages: Annotated[list[str], add]
    tool_results: Annotated[list[str], add]
    errors: Annotated[list[str], add]
    events: Annotated[list[dict[str, Any]], add]


class Scenario(BaseModel):
    id: str
    query: str
    expected_route: Route
    requires_approval: bool = False
    should_retry: bool = False
    max_attempts: int = 3
    tags: list[str] = Field(default_factory=list)

    @field_validator("query")
    @classmethod
    def query_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        return value


def initial_state(scenario: Scenario) -> AgentState:
    """Create a serializable initial state for one scenario."""
    return {
        "thread_id": f"thread-{scenario.id}",
        "scenario_id": scenario.id,
        "query": scenario.query,
        "route": "",
        "risk_level": "unknown",
        "attempt": 0,
        "max_attempts": scenario.max_attempts,
        "final_answer": None,
        "pending_question": None,
        "proposed_action": None,
        "approval": None,
        "evaluation_result": None,
        "messages": [],
        "tool_results": [],
        "errors": [],
        "events": [],
    }


def make_event(node: str, event_type: str, message: str, **metadata: Any) -> dict[str, Any]:  # noqa: ANN401
    """Create a normalized event payload."""
    event = LabEvent(node=node, event_type=event_type, message=message, metadata=metadata)
    return event.model_dump()
