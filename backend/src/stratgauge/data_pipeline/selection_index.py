"""Rebuildable DuckDB read index for immutable selection evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import duckdb

from stratgauge.domain.selections import (
    SelectionLifecycleEvent,
    SelectionOutcome,
    SelectionPerformance,
    SelectionReusableTagEvent,
)

_SCHEMA_VERSION = "selection-read-index-v1"
_INDEX_FILENAME = "selection-index.duckdb"


class SelectionIndexError(RuntimeError):
    """The selection read projection cannot be opened or updated safely."""


@dataclass(frozen=True, slots=True)
class SelectionIndexStats:
    """Counts materialized by one projection rebuild."""

    outcomes: int
    performances: int
    lifecycle_events: int
    reusable_tag_events: int


class SelectionReadIndex:
    """Index immutable selection files without copying their JSON payloads.

    The JSON files remain the source of truth.  Tables contain only lookup keys
    and relative source paths, so a query still validates and reads the selected
    evidence file rather than trusting a duplicated payload in DuckDB.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        auto_rebuild: bool = True,
        require_ready: bool = True,
        initialize: bool = True,
    ) -> None:
        self.root = Path(root)
        self.index_path = self.root / "tdx" / _INDEX_FILENAME
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if not initialize:
            return
        if not self.index_path.exists() and auto_rebuild:
            self.rebuild()
        else:
            self._ensure_schema()
        if require_ready and not self.is_ready():
            raise SelectionIndexError("selection read index is not ready; run a rebuild")

    def rebuild(self) -> SelectionIndexStats:
        """Build a complete replacement and atomically install it.

        The caller must hold the selection root's single-writer lock.  A failed
        scan leaves the previous projection untouched.
        """

        temporary = self.index_path.with_name(f"{self.index_path.name}.{uuid4().hex}.tmp")
        connection: duckdb.DuckDBPyConnection | None = None
        try:
            connection = duckdb.connect(str(temporary))
            _create_schema(connection)
            connection.execute("BEGIN")
            stats = self._populate(connection)
            _set_meta(connection, "state", "ready")
            _set_meta(connection, "schema_version", _SCHEMA_VERSION)
            connection.execute("COMMIT")
            connection.close()
            connection = None
            temporary.replace(self.index_path)
            return stats
        except Exception as exc:
            if connection is not None:
                try:
                    connection.execute("ROLLBACK")
                except Exception:
                    pass
                connection.close()
            _remove_database_files(temporary)
            if isinstance(exc, SelectionIndexError):
                raise
            raise SelectionIndexError("failed to rebuild selection read index") from exc

    def is_ready(self) -> bool:
        try:
            with duckdb.connect(str(self.index_path), read_only=True) as connection:
                rows = connection.execute(
                    "SELECT key, value FROM projection_meta WHERE key IN (?, ?)",
                    ["state", "schema_version"],
                ).fetchall()
        except Exception:
            return False
        values = {str(key): str(value) for key, value in rows}
        return values.get("state") == "ready" and values.get("schema_version") == _SCHEMA_VERSION

    def mark_stale(self) -> None:
        try:
            with duckdb.connect(str(self.index_path)) as connection:
                _set_meta(connection, "state", "stale")
        except Exception as exc:
            raise SelectionIndexError("failed to mark selection read index stale") from exc

    def upsert_outcome(self, outcome: SelectionOutcome, source_path: str | Path) -> None:
        relative_path = _relative_source_path(self.root, source_path)
        self._replace_row(
            "outcomes",
            ("outcome_id", "maturity_task_id", "candidate_id", "evaluated_at", "source_path"),
            (
                Path(relative_path).stem,
                outcome.maturity_task_id,
                outcome.candidate_id,
                outcome.evaluated_at,
                relative_path,
            ),
        )

    def upsert_performance(
        self,
        performance: SelectionPerformance,
        source_path: str | Path,
    ) -> None:
        relative_path = _relative_source_path(self.root, source_path)
        self._replace_row(
            "performances",
            (
                "performance_id",
                "maturity_task_id",
                "candidate_id",
                "performance_policy_id",
                "evaluated_at",
                "source_path",
            ),
            (
                Path(relative_path).stem,
                performance.maturity_task_id,
                performance.candidate_id,
                performance.performance_policy_id,
                performance.evaluated_at,
                relative_path,
            ),
        )

    def upsert_lifecycle_event(
        self,
        event: SelectionLifecycleEvent,
        source_path: str | Path,
    ) -> None:
        relative_path = _relative_source_path(self.root, source_path)
        self._replace_row(
            "lifecycle_events",
            ("event_id", "entity_type", "entity_id", "status", "changed_at", "source_path"),
            (
                event.event_id,
                event.entity_type,
                event.entity_id,
                event.status.value,
                event.changed_at,
                relative_path,
            ),
        )

    def upsert_reusable_tag_event(
        self,
        event: SelectionReusableTagEvent,
        source_path: str | Path,
    ) -> None:
        relative_path = _relative_source_path(self.root, source_path)
        self._replace_row(
            "reusable_tag_events",
            ("event_id", "tag_id", "category", "event_type", "occurred_at", "source_path"),
            (
                event.event_id,
                event.tag_id,
                event.category.value,
                event.event_type.value,
                event.occurred_at,
                relative_path,
            ),
        )

    def find_outcome_paths_for_task(
        self,
        maturity_task_id: str,
        candidate_id: str,
    ) -> tuple[str, ...]:
        return self._query_paths(
            """
            SELECT source_path
            FROM outcomes
            WHERE maturity_task_id = ? AND candidate_id = ?
            ORDER BY evaluated_at, outcome_id
            """,
            (maturity_task_id, candidate_id),
        )

    def list_maturity_task_outcome_paths(self, maturity_task_id: str) -> tuple[str, ...]:
        return self._query_paths(
            """
            SELECT source_path
            FROM outcomes
            WHERE maturity_task_id = ?
            ORDER BY candidate_id, evaluated_at, outcome_id
            """,
            (maturity_task_id,),
        )

    def list_outcome_paths(self, candidate_id: str) -> tuple[str, ...]:
        return self._query_paths(
            """
            SELECT source_path
            FROM outcomes
            WHERE candidate_id = ?
            ORDER BY evaluated_at, outcome_id
            """,
            (candidate_id,),
        )

    def find_performance_paths_for_task(
        self,
        maturity_task_id: str,
        candidate_id: str,
    ) -> tuple[str, ...]:
        return self._query_paths(
            """
            SELECT source_path
            FROM performances
            WHERE maturity_task_id = ? AND candidate_id = ?
            ORDER BY evaluated_at, performance_id
            """,
            (maturity_task_id, candidate_id),
        )

    def list_performance_paths(self, candidate_id: str) -> tuple[str, ...]:
        return self._query_paths(
            """
            SELECT source_path
            FROM performances
            WHERE candidate_id = ?
            ORDER BY performance_policy_id, evaluated_at, performance_id
            """,
            (candidate_id,),
        )

    def list_lifecycle_event_paths(
        self,
        entity_type: str,
        entity_id: str,
    ) -> tuple[str, ...]:
        return self._query_paths(
            """
            SELECT source_path
            FROM lifecycle_events
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY changed_at, event_id
            """,
            (entity_type, entity_id),
        )

    def list_reusable_tag_event_paths(self, tag_id: str) -> tuple[str, ...]:
        return self._query_paths(
            """
            SELECT source_path
            FROM reusable_tag_events
            WHERE tag_id = ?
            ORDER BY occurred_at, event_id
            """,
            (tag_id,),
        )

    def _query_paths(self, query: str, parameters: tuple[object, ...]) -> tuple[str, ...]:
        try:
            with duckdb.connect(str(self.index_path), read_only=True) as connection:
                rows = connection.execute(query, list(parameters)).fetchall()
        except Exception as exc:
            raise SelectionIndexError("failed to query selection read index") from exc
        return tuple(str(row[0]) for row in rows)

    def _replace_row(
        self,
        table: str,
        columns: tuple[str, ...],
        values: tuple[object, ...],
    ) -> None:
        source_path = values[-1]
        try:
            with duckdb.connect(str(self.index_path)) as connection:
                connection.execute("BEGIN")
                connection.execute(
                    f"DELETE FROM {table} WHERE source_path = ?",
                    [source_path],
                )
                placeholders = ", ".join("?" for _ in columns)
                connection.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    list(values),
                )
                _set_meta(connection, "state", "ready")
                connection.execute("COMMIT")
        except Exception as exc:
            try:
                with duckdb.connect(str(self.index_path)) as connection:
                    _set_meta(connection, "state", "stale")
            except Exception:
                pass
            raise SelectionIndexError("failed to update selection read index") from exc

    def _ensure_schema(self) -> None:
        try:
            with duckdb.connect(str(self.index_path)) as connection:
                _create_schema(connection)
        except Exception as exc:
            raise SelectionIndexError("failed to open selection read index") from exc

    def _populate(self, connection: duckdb.DuckDBPyConnection) -> SelectionIndexStats:
        outcome_count = 0
        for path in sorted((self.root / "tdx" / "outcomes").glob("outcome-*.json")):
            try:
                outcome = SelectionOutcome.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionIndexError(f"selection outcome is invalid: {path.name}") from exc
            _insert_outcome(connection, outcome, _relative_source_path(self.root, path))
            outcome_count += 1

        performance_count = 0
        for path in sorted((self.root / "tdx" / "performances").glob("performance-*.json")):
            try:
                performance = SelectionPerformance.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionIndexError(f"selection performance is invalid: {path.name}") from exc
            _insert_performance(connection, performance, _relative_source_path(self.root, path))
            performance_count += 1

        lifecycle_count = 0
        for path in sorted((self.root / "tdx" / "lifecycle").glob("lifecycle-*.json")):
            try:
                event = SelectionLifecycleEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionIndexError(
                    f"selection lifecycle event is invalid: {path.name}"
                ) from exc
            _insert_lifecycle_event(connection, event, _relative_source_path(self.root, path))
            lifecycle_count += 1

        tag_event_count = 0
        for path in sorted(
            (self.root / "tdx" / "annotation_tag_events").glob("annotation-tag-event-*.json")
        ):
            try:
                tag_event = SelectionReusableTagEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionIndexError(f"reusable tag event is invalid: {path.name}") from exc
            _insert_reusable_tag_event(
                connection,
                tag_event,
                _relative_source_path(self.root, path),
            )
            tag_event_count += 1

        return SelectionIndexStats(
            outcomes=outcome_count,
            performances=performance_count,
            lifecycle_events=lifecycle_count,
            reusable_tag_events=tag_event_count,
        )


def _create_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS projection_meta (
            key VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS outcomes (
            outcome_id VARCHAR PRIMARY KEY,
            maturity_task_id VARCHAR,
            candidate_id VARCHAR NOT NULL,
            evaluated_at TIMESTAMPTZ NOT NULL,
            source_path VARCHAR NOT NULL UNIQUE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS performances (
            performance_id VARCHAR PRIMARY KEY,
            maturity_task_id VARCHAR,
            candidate_id VARCHAR NOT NULL,
            performance_policy_id VARCHAR NOT NULL,
            evaluated_at TIMESTAMPTZ NOT NULL,
            source_path VARCHAR NOT NULL UNIQUE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lifecycle_events (
            event_id VARCHAR PRIMARY KEY,
            entity_type VARCHAR NOT NULL,
            entity_id VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL,
            source_path VARCHAR NOT NULL UNIQUE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reusable_tag_events (
            event_id VARCHAR PRIMARY KEY,
            tag_id VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            event_type VARCHAR NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            source_path VARCHAR NOT NULL UNIQUE
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS outcomes_task_candidate_idx "
        "ON outcomes (maturity_task_id, candidate_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS outcomes_candidate_idx ON outcomes (candidate_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS performances_task_candidate_idx "
        "ON performances (maturity_task_id, candidate_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS performances_candidate_idx ON performances (candidate_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lifecycle_entity_idx "
        "ON lifecycle_events (entity_type, entity_id, changed_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS reusable_tag_idx ON reusable_tag_events (tag_id, occurred_at)"
    )


def _set_meta(connection: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    connection.execute("DELETE FROM projection_meta WHERE key = ?", [key])
    connection.execute(
        "INSERT INTO projection_meta (key, value) VALUES (?, ?)",
        [key, value],
    )


def _insert_outcome(
    connection: duckdb.DuckDBPyConnection,
    outcome: SelectionOutcome,
    source_path: str,
) -> None:
    connection.execute(
        """
        INSERT INTO outcomes
            (outcome_id, maturity_task_id, candidate_id, evaluated_at, source_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            Path(source_path).stem,
            outcome.maturity_task_id,
            outcome.candidate_id,
            outcome.evaluated_at,
            source_path,
        ],
    )


def _insert_performance(
    connection: duckdb.DuckDBPyConnection,
    performance: SelectionPerformance,
    source_path: str,
) -> None:
    connection.execute(
        """
        INSERT INTO performances
            (
                performance_id,
                maturity_task_id,
                candidate_id,
                performance_policy_id,
                evaluated_at,
                source_path
            )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            Path(source_path).stem,
            performance.maturity_task_id,
            performance.candidate_id,
            performance.performance_policy_id,
            performance.evaluated_at,
            source_path,
        ],
    )


def _insert_lifecycle_event(
    connection: duckdb.DuckDBPyConnection,
    event: SelectionLifecycleEvent,
    source_path: str,
) -> None:
    connection.execute(
        """
        INSERT INTO lifecycle_events
            (event_id, entity_type, entity_id, status, changed_at, source_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            event.event_id,
            event.entity_type,
            event.entity_id,
            event.status.value,
            event.changed_at,
            source_path,
        ],
    )


def _insert_reusable_tag_event(
    connection: duckdb.DuckDBPyConnection,
    event: SelectionReusableTagEvent,
    source_path: str,
) -> None:
    connection.execute(
        """
        INSERT INTO reusable_tag_events
            (event_id, tag_id, category, event_type, occurred_at, source_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            event.event_id,
            event.tag_id,
            event.category.value,
            event.event_type.value,
            event.occurred_at,
            source_path,
        ],
    )


def _relative_source_path(root: Path, source_path: str | Path) -> str:
    path = Path(source_path)
    if path.is_absolute():
        path = path.relative_to(root)
    return path.as_posix()


def _remove_database_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}.wal")):
        candidate.unlink(missing_ok=True)
