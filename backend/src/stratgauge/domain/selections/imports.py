"""Parsed TDX selection import aggregate."""

from __future__ import annotations

from typing import Any

from pydantic import model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections.core import SelectionBatch, SelectionCandidate


class TdxSelectionImport(DomainModel):
    """Parsed export with immutable batch metadata and candidates."""

    batch: SelectionBatch
    candidates: tuple[SelectionCandidate, ...]

    @model_validator(mode="after")
    def validate_batch_membership(self) -> TdxSelectionImport:
        if self.batch.candidate_count != len(self.candidates):
            raise ValueError("batch candidate_count must match candidates")
        if any(candidate.batch_id != self.batch.batch_id for candidate in self.candidates):
            raise ValueError("all candidates must belong to the batch")
        return self

    def model_dump_for_storage(self) -> dict[str, Any]:
        """Return a JSON-compatible payload for a future immutable import store."""

        return self.model_dump(mode="json")
