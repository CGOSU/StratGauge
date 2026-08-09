"""API contracts and service for the semi-manual selection queue."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.backtest.selection_stats import SelectionOutcomeStats, summarize_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.tdx_import import TdxImportError, import_tdx_export
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
    SelectionBatch,
    SelectionCandidate,
    SelectionOutcome,
    TdxSelectionImport,
)


class SelectionApiError(Exception):
    """Expected API-facing selection workflow failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SelectionImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_base64: str = Field(min_length=1)
    selection_date: date
    exported_at: datetime
    formula_name: str = Field(min_length=1)
    formula_version: str | None = None
    formula_sha256: str | None = None

    @field_validator("exported_at")
    @classmethod
    def require_aware_export_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exported_at must include a timezone")
        return value


class SelectionLabelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    decision: ManualSelectionDecision
    labeled_at: datetime
    note: str | None = None

    @field_validator("labeled_at")
    @classmethod
    def require_aware_label_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("labeled_at must include a timezone")
        return value


class SelectionCandidateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: SelectionCandidate
    labels: tuple[ManualSelectionLabel, ...] = ()
    outcomes: tuple[SelectionOutcome, ...] = ()


class SelectionBatchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    candidates: tuple[SelectionCandidateView, ...]


class SelectionStatsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str
    candidate_count: int = Field(ge=0)
    pending: int = Field(ge=0)
    latest_outcome_count: int = Field(ge=0)
    summary: SelectionOutcomeStats


class SelectionService:
    """Application service backed by immutable local selection storage."""

    def __init__(self, store: SelectionImportStore) -> None:
        self._store = store

    def list_batches(self) -> tuple[SelectionBatch, ...]:
        batches: list[SelectionBatch] = []
        try:
            for manifest in self._store.list_manifests():
                batches.append(self._store.load_import(manifest.batch_id).batch)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        return tuple(
            sorted(batches, key=lambda batch: (batch.selection_date, batch.batch_id), reverse=True)
        )

    def get_batch(self, batch_id: str) -> SelectionBatchView:
        try:
            imported = self._store.load_import(batch_id)
            return self._view(imported)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def import_batch(self, request: SelectionImportRequest) -> SelectionBatchView:
        content = _decode_base64(request.content_base64)
        try:
            imported = import_tdx_export(
                content,
                selection_date=request.selection_date,
                exported_at=request.exported_at,
                imported_at=datetime.now(UTC),
                formula_name=request.formula_name,
                formula_version=request.formula_version,
                formula_sha256=request.formula_sha256,
            )
            self._store.write(content, imported)
            return self._view(imported)
        except TdxImportError as exc:
            raise SelectionApiError("INVALID_TDX_EXPORT", str(exc), 422) from exc
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("INVALID_SELECTION_METADATA", str(exc), 422) from exc

    def summarize_batch(self, batch_id: str) -> SelectionStatsView:
        try:
            imported = self._store.load_import(batch_id)
            outcomes: list[SelectionOutcome] = []
            pending = 0
            for candidate in imported.candidates:
                candidate_outcomes = self._store.list_outcomes(candidate.candidate_id)
                if not candidate_outcomes:
                    pending += 1
                outcomes.extend(candidate_outcomes)
            latest = latest_selection_outcomes(outcomes)
            return SelectionStatsView(
                batch_id=batch_id,
                candidate_count=len(imported.candidates),
                pending=pending,
                latest_outcome_count=len(latest),
                summary=summarize_selection_outcomes(latest),
            )
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def label_candidate(self, batch_id: str, request: SelectionLabelRequest) -> SelectionBatchView:
        label = ManualSelectionLabel(
            candidate_id=request.candidate_id,
            decision=request.decision,
            labeled_at=request.labeled_at,
            note=request.note,
        )
        try:
            self._store.write_label(batch_id, label)
            return self.get_batch(batch_id)
        except SelectionStoreError as exc:
            message = str(exc)
            if "label timestamp" in message:
                raise SelectionApiError(
                    "SELECTION_LABEL_NOT_ALLOWED",
                    "人工标注必须在导出后、下一自然日开始前完成。",
                    422,
                ) from exc
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc

    def _view(self, imported: TdxSelectionImport) -> SelectionBatchView:
        try:
            candidates = tuple(
                SelectionCandidateView(
                    candidate=candidate,
                    labels=self._store.list_labels(candidate.candidate_id),
                    outcomes=self._store.list_outcomes(candidate.candidate_id),
                )
                for candidate in imported.candidates
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        return SelectionBatchView(batch=imported.batch, candidates=candidates)


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SelectionApiError(
            "INVALID_SELECTION_FILE", "导出文件不是有效的 Base64 内容。", 422
        ) from exc
