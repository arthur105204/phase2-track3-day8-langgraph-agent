"""CLI for the lab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from .graph import build_graph
from .metrics import MetricsReport, metric_from_state, summarize_metrics, write_metrics
from .persistence import build_checkpointer
from .report import write_report
from .scenarios import load_scenarios
from .state import initial_state

app = typer.Typer(no_args_is_help=True)


def _unlink_sqlite_files(db_path: str) -> None:
    base = Path(db_path)
    for path in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        path.unlink(missing_ok=True)


@app.command("run-scenarios")
def run_scenarios(
    config: Annotated[Path, typer.Option("--config")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Run all grading scenarios and write metrics JSON."""
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    scenarios = load_scenarios(cfg["scenarios_path"])
    db_url = cfg.get("database_url")
    if (
        cfg.get("checkpointer") == "sqlite"
        and cfg.get("reset_sqlite_before_run", True)
        and isinstance(db_url, str)
    ):
        _unlink_sqlite_files(db_url)
    checkpointer = build_checkpointer(cfg.get("checkpointer", "memory"), db_url)
    graph = build_graph(checkpointer=checkpointer)
    metrics = []
    for scenario in scenarios:
        state = initial_state(scenario)
        run_config = {"configurable": {"thread_id": state["thread_id"]}}
        final_state = graph.invoke(state, config=run_config)
        metrics.append(
            metric_from_state(
                final_state,
                scenario.expected_route.value,
                scenario.requires_approval,
            )
        )
    report = summarize_metrics(metrics)
    write_metrics(report, output)
    if cfg.get("report_path"):
        write_report(report, cfg["report_path"])
    typer.echo(f"Wrote metrics to {output}")


@app.command("validate-metrics")
def validate_metrics(metrics: Annotated[Path, typer.Option("--metrics")]) -> None:
    """Validate metrics JSON schema for grading."""
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    report = MetricsReport.model_validate(payload)
    if report.total_scenarios < 6:
        raise typer.BadParameter("Expected at least 6 scenarios")
    typer.echo(f"Metrics valid. success_rate={report.success_rate:.2%}")


@app.command("dump-state-history")
def dump_state_history(
    config: Annotated[Path, typer.Option("--config")],
    thread_id: Annotated[
        str,
        typer.Option("--thread-id", help="e.g. thread-S01_simple (matches initial_state)"),
    ],
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max checkpoints (newest first)"),
    ] = 15,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write JSON evidence to this path"),
    ] = None,
) -> None:
    """Print checkpoint history (time-travel / persistence evidence)."""
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    checkpointer = build_checkpointer(cfg.get("checkpointer", "memory"), cfg.get("database_url"))
    graph = build_graph(checkpointer=checkpointer)
    run_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    rows: list[dict[str, Any]] = []
    for i, snap in enumerate(graph.get_state_history(run_config)):
        if i >= limit:
            break
        values = snap.values
        if not isinstance(values, dict):
            values = {}
        conf = snap.config if isinstance(snap.config, dict) else {}
        configurable = conf.get("configurable") or {}
        chk_id = configurable.get("checkpoint_id")
        rows.append(
            {
                "step": i,
                "checkpoint_id": chk_id,
                "next": list(getattr(snap, "next", ()) or ()),
                "route": values.get("route"),
                "attempt": values.get("attempt"),
                "events_tail": [e.get("node") for e in (values.get("events") or [])][-8:],
            }
        )
    payload = {"thread_id": thread_id, "checkpoints_newest_first": rows}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        typer.echo(f"Wrote history to {output}")
    else:
        typer.echo(text)


if __name__ == "__main__":
    app()
