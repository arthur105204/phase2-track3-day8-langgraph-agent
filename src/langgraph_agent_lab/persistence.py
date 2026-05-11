"""Checkpointer adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> object | None:
    """Return a LangGraph checkpointer.

    - ``memory``: in-process ``MemorySaver`` for fast tests.
    - ``sqlite``: durable checkpoints with WAL (extension / crash-resume evidence).
    - ``postgres``: optional enterprise backend when the extra package is installed.
    """
    if kind == "none":
        return None
    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            msg = "SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite"
            raise RuntimeError(msg) from exc
        path = database_url or "checkpoints.db"
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.commit()
        return SqliteSaver(conn)
    if kind == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver  # noqa: I001
        except ImportError as exc:
            msg = "Postgres checkpointer requires: pip install langgraph-checkpoint-postgres"
            raise RuntimeError(msg) from exc
        return PostgresSaver.from_conn_string(database_url or "")
    raise ValueError(f"Unknown checkpointer kind: {kind}")
