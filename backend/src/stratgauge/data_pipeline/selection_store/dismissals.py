"""Candidate dismissal event persistence."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_store._base import SelectionStoreBase, SelectionStoreError
from stratgauge.data_pipeline.selection_store._io import _write_selection_text
from stratgauge.domain.selections import SelectionCandidateDismissal


class DismissalsMixin(SelectionStoreBase):
    """Persist append-only candidate dismissal events."""

    def write_dismissal(
        self,
        batch_id: str,
        dismissal: SelectionCandidateDismissal,
    ) -> SelectionCandidateDismissal:
        with local_write_lock(self.root):
            return self._write_dismissal_unlocked(batch_id, dismissal)

    def _write_dismissal_unlocked(
        self,
        batch_id: str,
        dismissal: SelectionCandidateDismissal,
    ) -> SelectionCandidateDismissal:
        imported = self.load_import(batch_id)
        if not any(
            candidate.candidate_id == dismissal.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {dismissal.candidate_id}"
            )
        path = self.dismissal_dir / f"{dismissal.dismissal_id}.json"
        if path.exists():
            try:
                existing = SelectionCandidateDismissal.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(
                    f"selection dismissal is invalid: {dismissal.dismissal_id}"
                ) from exc
            if existing.candidate_id != dismissal.candidate_id:
                raise SelectionStoreError(
                    "selection dismissal identity already has different content"
                )
            return existing
        _write_selection_text(path, dismissal.model_dump_json(indent=2))
        return dismissal

    def list_dismissals(self, candidate_id: str) -> tuple[SelectionCandidateDismissal, ...]:
        dismissals: list[SelectionCandidateDismissal] = []
        try:
            for path in sorted(self.dismissal_dir.glob("dismissal-*.json")):
                dismissal = SelectionCandidateDismissal.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if dismissal.candidate_id == candidate_id:
                    dismissals.append(dismissal)
        except Exception as exc:
            raise SelectionStoreError("selection dismissal is invalid") from exc
        return tuple(sorted(dismissals, key=lambda item: (item.recorded_at, item.dismissal_id)))

    def list_dismissals_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionCandidateDismissal, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        dismissals_by_candidate: dict[str, list[SelectionCandidateDismissal]] = {
            candidate_id: [] for candidate_id in requested
        }
        requested_set = set(requested)
        try:
            for path in sorted(self.dismissal_dir.glob("dismissal-*.json")):
                dismissal = SelectionCandidateDismissal.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if dismissal.candidate_id in requested_set:
                    dismissals_by_candidate[dismissal.candidate_id].append(dismissal)
        except Exception as exc:
            raise SelectionStoreError("selection dismissal is invalid") from exc
        return {
            candidate_id: tuple(
                sorted(dismissals, key=lambda item: (item.recorded_at, item.dismissal_id))
            )
            for candidate_id, dismissals in dismissals_by_candidate.items()
        }

    def latest_dismissal(
        self,
        candidate_id: str,
        *,
        recorded_before: datetime | None = None,
    ) -> SelectionCandidateDismissal | None:
        dismissals = self.list_dismissals(candidate_id)
        if recorded_before is not None:
            dismissals = tuple(item for item in dismissals if item.recorded_at <= recorded_before)
        return dismissals[-1] if dismissals else None
