import importlib.util

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("langgraph") is None, reason="langgraph not installed in local environment")

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import Route, Scenario, initial_state


@pytest.mark.parametrize(
    ("query", "expected_route"),
    [
        ("How do I reset my password?", Route.SIMPLE.value),
        ("Please lookup order status for order 123", Route.TOOL.value),
        ("Refund this customer", Route.RISKY.value),
    ],
)
def test_graph_runs_basic_routes(query, expected_route):
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    scenario = Scenario(id="smoke", query=query, expected_route=Route(expected_route))
    state = initial_state(scenario)
    result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
    assert result["route"] == expected_route
    assert result.get("final_answer") or result.get("pending_question")


def test_sample_scenarios_jsonl_all_match_expected_route():
    from langgraph_agent_lab.metrics import metric_from_state
    from langgraph_agent_lab.scenarios import load_scenarios

    scenarios = load_scenarios("data/sample/scenarios.jsonl")
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    for scenario in scenarios:
        state = initial_state(scenario)
        final_state = graph.invoke(
            state,
            config={"configurable": {"thread_id": state["thread_id"]}},
        )
        m = metric_from_state(
            final_state,
            scenario.expected_route.value,
            scenario.requires_approval,
        )
        assert m.success, (scenario.id, m.actual_route, m.errors)
