"""Objective outcome and forward performance persistence."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

import duckdb

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_store._base import SelectionStoreBase, SelectionStoreError
from stratgauge.data_pipeline.selection_store._io import _write_selection_text
from stratgauge.domain.selections import (
    SelectionOutcome,
    SelectionPerformance,
)


class OutcomesMixin(SelectionStoreBase):
    """Outcome and performance operations."""

    def write_outcome(self, outcome: SelectionOutcome) -> SelectionOutcome:
        with local_write_lock(self.root):
            return self._write_outcome_unlocked(outcome)

    def _write_outcome_unlocked(self, outcome: SelectionOutcome) -> SelectionOutcome:
        imported = self.load_import(outcome.batch_id)
        if not any(
            candidate.candidate_id == outcome.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {outcome.candidate_id}"
            )

        if outcome.maturity_task_id is not None:
            existing = self.find_outcome_for_task(
                outcome.maturity_task_id,
                outcome.candidate_id,
            )
            if existing is not None:
                if existing == outcome:
                    return existing
                raise SelectionStoreError(
                    "maturity task candidate already has a different outcome: "
                    f"{outcome.maturity_task_id}/{outcome.candidate_id}"
                )

        outcome_id = (
            f"outcome-{hashlib.sha256(outcome.model_dump_json().encode('utf-8')).hexdigest()[:20]}"
        )
        path = self.outcome_dir / f"{outcome_id}.json"
        if path.exists():
            try:
                existing = SelectionOutcome.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection outcome is invalid: {outcome_id}") from exc
            if existing != outcome:
                raise SelectionStoreError(
                    f"selection outcome identity has different content: {outcome_id}"
                )
            self._update_read_index(lambda index: index.upsert_outcome(existing, path))
            return existing
        _write_selection_text(path, outcome.model_dump_json(indent=2))
        self._update_read_index(lambda index: index.upsert_outcome(outcome, path))
        return outcome

    def write_performance(self, performance: SelectionPerformance) -> SelectionPerformance:
        with local_write_lock(self.root):
            return self._write_performance_unlocked(performance)

    def _write_performance_unlocked(
        self,
        performance: SelectionPerformance,
    ) -> SelectionPerformance:
        imported = self.load_import(performance.batch_id)
        if not any(
            candidate.candidate_id == performance.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {performance.candidate_id}"
            )
        if performance.maturity_task_id is not None:
            existing = self.find_performance_for_task(
                performance.maturity_task_id,
                performance.candidate_id,
            )
            if existing is not None:
                if existing == performance:
                    return existing
                raise SelectionStoreError(
                    "maturity task candidate already has a different performance: "
                    f"{performance.maturity_task_id}/{performance.candidate_id}"
                )
        performance_id = (
            "performance-"
            f"{hashlib.sha256(performance.model_dump_json().encode('utf-8')).hexdigest()[:20]}"
        )
        path = self.performance_dir / f"{performance_id}.json"
        if path.exists():
            try:
                existing = SelectionPerformance.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(
                    f"selection performance is invalid: {performance_id}"
                ) from exc
            if existing != performance:
                raise SelectionStoreError(
                    f"selection performance identity has different content: {performance_id}"
                )
            self._update_read_index(lambda index: index.upsert_performance(existing, path))
            return existing
        _write_selection_text(path, performance.model_dump_json(indent=2))
        self._update_read_index(lambda index: index.upsert_performance(performance, path))
        return performance

    def find_performance_for_task(
        self,
        maturity_task_id: str,
        candidate_id: str,
    ) -> SelectionPerformance | None:
        indexed_paths = self._indexed_paths(
            lambda index: index.find_performance_paths_for_task(
                maturity_task_id,
                candidate_id,
            )
        )
        if indexed_paths is None:
            matches = tuple(
                performance
                for performance in self._list_all_performances()
                if performance.maturity_task_id == maturity_task_id
                and performance.candidate_id == candidate_id
            )
        else:
            matches = tuple(self._load_performance(path) for path in indexed_paths)
        if len(matches) > 1:
            raise SelectionStoreError(
                "maturity task candidate has more than one performance: "
                f"{maturity_task_id}/{candidate_id}"
            )
        return matches[0] if matches else None

    def list_performances(self, candidate_id: str) -> tuple[SelectionPerformance, ...]:
        indexed_paths = self._indexed_paths(
            lambda index: index.list_performance_paths(candidate_id)
        )
        if indexed_paths is None:
            performances = [
                performance
                for performance in self._list_all_performances()
                if performance.candidate_id == candidate_id
            ]
        else:
            performances = [self._load_performance(path) for path in indexed_paths]
        return tuple(performances)

    def list_performances_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionPerformance, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        performances_by_candidate: dict[str, list[SelectionPerformance]] = {
            candidate_id: [] for candidate_id in requested
        }
        indexed_paths = self._indexed_paths_for_candidates(
            table="performances",
            order_by="performance_policy_id, evaluated_at, performance_id",
            candidate_ids=requested,
        )
        if indexed_paths is not None:
            return {
                candidate_id: tuple(
                    self._load_performance(path) for path in indexed_paths[candidate_id]
                )
                for candidate_id in requested
            }
        for performance in self._list_all_performances():
            if performance.candidate_id in performances_by_candidate:
                performances_by_candidate[performance.candidate_id].append(performance)
        return {
            candidate_id: tuple(
                sorted(
                    rows,
                    key=lambda performance: (
                        performance.performance_policy_id,
                        performance.evaluated_at,
                    ),
                )
            )
            for candidate_id, rows in performances_by_candidate.items()
        }

    def _list_all_performances(self) -> tuple[SelectionPerformance, ...]:
        performances: list[SelectionPerformance] = []
        for path in sorted(self.performance_dir.glob("performance-*.json")):
            performances.append(self._load_performance(path))
        return tuple(performances)

    def _load_performance(self, source_path: str | Path) -> SelectionPerformance:
        path = self._source_path(source_path)
        try:
            return SelectionPerformance.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection performance is invalid: {path.name}") from exc

    def find_outcome_for_task(
        self,
        maturity_task_id: str,
        candidate_id: str,
    ) -> SelectionOutcome | None:
        indexed_paths = self._indexed_paths(
            lambda index: index.find_outcome_paths_for_task(
                maturity_task_id,
                candidate_id,
            )
        )
        if indexed_paths is None:
            matches = tuple(
                outcome
                for outcome in self._list_all_outcomes()
                if outcome.maturity_task_id == maturity_task_id
                and outcome.candidate_id == candidate_id
            )
        else:
            matches = tuple(self._load_outcome(path) for path in indexed_paths)
        if len(matches) > 1:
            raise SelectionStoreError(
                "maturity task candidate has more than one outcome: "
                f"{maturity_task_id}/{candidate_id}"
            )
        return matches[0] if matches else None

    def list_maturity_task_outcomes(self, maturity_task_id: str) -> tuple[SelectionOutcome, ...]:
        indexed_paths = self._indexed_paths(
            lambda index: index.list_maturity_task_outcome_paths(maturity_task_id)
        )
        if indexed_paths is None:
            outcomes = tuple(
                outcome
                for outcome in self._list_all_outcomes()
                if outcome.maturity_task_id == maturity_task_id
            )
        else:
            outcomes = tuple(self._load_outcome(path) for path in indexed_paths)
        return outcomes

    def list_outcomes(self, candidate_id: str) -> tuple[SelectionOutcome, ...]:
        indexed_paths = self._indexed_paths(lambda index: index.list_outcome_paths(candidate_id))
        if indexed_paths is None:
            outcomes = [
                outcome
                for outcome in self._list_all_outcomes()
                if outcome.candidate_id == candidate_id
            ]
        else:
            outcomes = [self._load_outcome(path) for path in indexed_paths]
        return tuple(sorted(outcomes, key=lambda outcome: outcome.evaluated_at))

    def list_outcomes_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionOutcome, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        outcomes_by_candidate: dict[str, list[SelectionOutcome]] = {
            candidate_id: [] for candidate_id in requested
        }
        indexed_paths = self._indexed_paths_for_candidates(
            table="outcomes",
            order_by="evaluated_at, outcome_id",
            candidate_ids=requested,
        )
        if indexed_paths is not None:
            return {
                candidate_id: tuple(
                    self._load_outcome(path) for path in indexed_paths[candidate_id]
                )
                for candidate_id in requested
            }
        for outcome in self._list_all_outcomes():
            if outcome.candidate_id in outcomes_by_candidate:
                outcomes_by_candidate[outcome.candidate_id].append(outcome)
        return {
            candidate_id: tuple(sorted(rows, key=lambda outcome: outcome.evaluated_at))
            for candidate_id, rows in outcomes_by_candidate.items()
        }

    def _indexed_paths_for_candidates(
        self,
        *,
        table: str,
        order_by: str,
        candidate_ids: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]] | None:
        index = self._usable_read_index()
        if index is None:
            return None
        paths_by_candidate: dict[str, list[str]] = {
            candidate_id: [] for candidate_id in candidate_ids
        }
        placeholders = ", ".join("?" for _ in candidate_ids)
        try:
            with duckdb.connect(str(index.index_path), read_only=True) as connection:
                rows = connection.execute(
                    f"""
                    SELECT candidate_id, source_path
                    FROM {table}
                    WHERE candidate_id IN ({placeholders})
                    ORDER BY candidate_id, {order_by}
                    """,
                    list(candidate_ids),
                ).fetchall()
        except Exception:
            self._read_index = None
            return None
        for candidate_id, source_path in rows:
            paths_by_candidate[str(candidate_id)].append(str(source_path))
        return {candidate_id: tuple(paths) for candidate_id, paths in paths_by_candidate.items()}

    def _list_all_outcomes(self) -> tuple[SelectionOutcome, ...]:
        outcomes: list[SelectionOutcome] = []
        for path in sorted(self.outcome_dir.glob("outcome-*.json")):
            outcomes.append(self._load_outcome(path))
        return tuple(outcomes)

    def _load_outcome(self, source_path: str | Path) -> SelectionOutcome:
        path = self._source_path(source_path)
        try:
            return SelectionOutcome.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection outcome is invalid: {path.name}") from exc
