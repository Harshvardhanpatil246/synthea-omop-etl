"""
Database layer.

Works with two databases:

  SQLite     -- built into Python. Nothing to install. Use this to learn.
  PostgreSQL -- what you would actually use at work.

Same SQL, same tables, same results. You switch with one command-line flag.
The only difference is the placeholder character in queries: SQLite uses "?"
and PostgreSQL uses "%s", so we swap it automatically.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

log = logging.getLogger(__name__)


class Database:
    """A thin wrapper so the rest of the code does not care which DB is used."""

    def __init__(self, kind: str, target: str) -> None:
        self.kind = kind          # "sqlite" or "postgres"
        self.target = target      # file path, or a connection string
        self.conn: Any = None

    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        if self.kind == "sqlite":
            Path(self.target).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.target)
            # Enforce foreign keys so bad references fail loudly, not silently.
            self.conn.execute("PRAGMA foreign_keys = ON")
            log.info("Connected to SQLite database: %s", self.target)
        elif self.kind == "postgres":
            import psycopg2  # only needed if you actually use Postgres
            self.conn = psycopg2.connect(self.target)
            self.conn.autocommit = False
            log.info("Connected to PostgreSQL")
        else:
            raise ValueError(f"Unknown database kind: {self.kind}")

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    # ------------------------------------------------------------------ #

    def placeholder(self) -> str:
        return "?" if self.kind == "sqlite" else "%s"

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        return cur

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        """Insert many rows at once. Far faster than one at a time."""
        if not rows:
            return 0
        cur = self.conn.cursor()
        cur.executemany(sql, rows)
        return len(rows)

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        cur = self.execute(sql, params)
        return cur.fetchall()

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # ------------------------------------------------------------------ #

    def run_script(self, path: Path) -> None:
        """
        Run a .sql file.

        Statements are split on semicolons. That is fine for our DDL, which
        has no functions or triggers containing semicolons.
        """
        sql_text = path.read_text(encoding="utf-8")

        # Strip full-line comments so the splitter does not choke on them.
        lines = [
            line for line in sql_text.splitlines()
            if not line.strip().startswith("--")
        ]
        cleaned = "\n".join(lines)

        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        cur = self.conn.cursor()
        for statement in statements:
            cur.execute(statement)
        self.commit()
        log.info("Ran %s (%d statements)", path.name, len(statements))

    def insert_many(self, table: str, columns: list[str],
                    rows: list[tuple]) -> int:
        """Build and run a bulk INSERT for the given table."""
        if not rows:
            return 0
        marks = ", ".join([self.placeholder()] * len(columns))
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})"
        return self.executemany(sql, rows)

    def truncate(self, tables: list[str]) -> None:
        """
        Empty tables before a fresh load.

        Order matters: children before parents, or foreign keys complain.
        """
        cur = self.conn.cursor()
        for table in tables:
            cur.execute(f"DELETE FROM {table}")
        self.commit()
        log.info("Cleared %d table(s)", len(tables))


@contextmanager
def open_database(kind: str, target: str) -> Iterator[Database]:
    """
    Open a connection, commit on success, roll back on any error.

        with open_database("sqlite", "data/omop.db") as db:
            db.query("SELECT count(*) FROM person")
    """
    db = Database(kind, target)
    db.connect()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
