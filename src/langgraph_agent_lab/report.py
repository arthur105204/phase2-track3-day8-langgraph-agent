"""Report generation helper."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .metrics import MetricsReport


def _mermaid_diagram() -> str:
    """Export a Mermaid diagram (bonus: graph visualization in the report)."""
    try:
        from langgraph_agent_lab.graph import build_graph
        from langgraph_agent_lab.persistence import build_checkpointer

        compiled = build_graph(checkpointer=build_checkpointer("memory"))
        return compiled.get_graph().draw_mermaid()
    except Exception as exc:  # pragma: no cover - optional path
        return f"```text\nDiagram unavailable: {exc}\n```"


def render_report(metrics: MetricsReport) -> str:
    """Fill the lab template with metrics, narrative sections, and a Mermaid diagram."""
    template_path = Path(__file__).resolve().parents[2] / "reports" / "lab_report_template.md"
    template = template_path.read_text(encoding="utf-8")

    rows = "\n".join(
        f"| {m.scenario_id} | {m.expected_route} | {m.actual_route} | "
        f"{'yes' if m.success else 'no'} | {m.retry_count} | {m.interrupt_count} |"
        for m in metrics.scenario_metrics
    )
    table = (
        "| Scenario | Expected route | Actual route | Success | Retries | Interrupts |\n"
        "|---|---|---|---:|---:|---:|\n"
        f"{rows}\n"
    )

    summary = (
        f"- Total scenarios: {metrics.total_scenarios}\n"
        f"- Success rate: {metrics.success_rate:.2%}\n"
        f"- Average nodes visited: {metrics.avg_nodes_visited:.2f}\n"
        f"- Total retries (retry node): {metrics.total_retries}\n"
        f"- Total approval nodes visited: {metrics.total_interrupts}\n"
    )

    arch = (
        "Nodes: ``intake`` (normalize + PII flags), ``classify`` (token/keyword routing), "
        "``answer``, ``tool`` (structured JSON + idempotency key), ``evaluate`` (JSON validation), "
        "``clarify``, ``risky_action``, ``approval`` (mock / interrupt HITL), "
        "``retry`` (bounded attempts + backoff metadata), ``dead_letter`` (JSONL escalation), "
        "``finalize``. Append-only reducers: ``messages``, ``tool_results``, ``errors``, "
        "``events``."
    )

    persistence = (
        "This run used the LangGraph checkpointer from ``configs/lab.yaml`` with a stable "
        "``thread_id`` per scenario (e.g. ``thread-S01_simple``). For checkpoint-chain evidence, "
        "run ``python -m langgraph_agent_lab.cli dump-state-history --config configs/lab.yaml "
        "--thread-id thread-S01_simple --output outputs/state_history_S01.json`` after "
        "``run-scenarios``, then attach or excerpt that JSON in this section."
    )

    failures = (
        "1. **Transient tool errors**: ``evaluate`` returns ``needs_retry`` when the structured "
        "payload has ``status=error`` / ``code=TRANSIENT``; ``retry`` increments ``attempt`` until "
        "``max_attempts`` routes to ``dead_letter``.\n"
        "2. **Risky actions without approval**: ``risky_action`` always precedes ``approval``; "
        "rejections route to ``clarify`` instead of executing ``tool``."
    )

    improvements = (
        "Wire a real LLM-as-judge in ``evaluate_node``, add OTLP tracing, and replace the JSONL "
        "dead-letter sink with a queue + paging integration."
    )

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    filled = template.replace("- Name:", "- Name: (see submission metadata)")
    filled = filled.replace("- Repo/commit:", "- Repo/commit: (local lab workspace)")
    filled = filled.replace("- Date:", f"- Date: {now}")
    filled = filled.replace("Describe your graph nodes, edges, state fields, and reducers.", arch)
    metrics_placeholder = (
        "Paste the key metrics from `outputs/metrics.json`.\n\n"
        "| Scenario | Expected route | Actual route | Success | Retries | Interrupts |\n"
        "|---|---|---|---:|---:|---:|\n"
    )
    filled = filled.replace(
        metrics_placeholder,
        "Key metrics (also in ``outputs/metrics.json``):\n\n" + summary + "\n" + table + "\n",
    )
    failure_placeholder = (
        "Describe at least two failure modes you considered:\n\n"
        "1. Retry or tool failure:\n"
        "2. Risky action without approval:\n"
    )
    filled = filled.replace(
        failure_placeholder,
        "Documented failure modes:\n\n" + failures + "\n\n",
    )
    filled = filled.replace(
        "Explain how you used checkpointer, thread id, state history, or crash-resume.",
        persistence,
    )
    ext_question = (
        "Describe any extension you completed: SQLite/Postgres, time travel, "
        "fan-out/fan-in, graph diagram, tracing."
    )
    ext_answer = (
        "Extensions in this submission: **SQLite WAL checkpointer** (durable checkpoints), "
        "**dead-letter JSONL** sink, **Mermaid graph export** below, and structured tool payloads."
    )
    filled = filled.replace(ext_question, ext_answer)
    filled = filled.replace(
        "If you had one more day, what would you productionize first?",
        improvements,
    )

    mermaid = _mermaid_diagram()
    if mermaid.lstrip().startswith("```"):
        diagram_block = "\n## 9. Graph diagram (Mermaid)\n\n" + mermaid + "\n"
    else:
        diagram_block = (
            "\n## 9. Graph diagram (Mermaid)\n\n"
            "Generated via ``get_graph().draw_mermaid()``:\n\n"
            f"```mermaid\n{mermaid}\n```\n"
        )
    return filled + diagram_block


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")
