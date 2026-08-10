"""API contracts and service for the semi-manual selection queue."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.backtest.selection_dataset import (
    build_selection_dataset,
    serialize_selection_dataset,
)
from stratgauge.backtest.selection_job import run_selection_outcome_job
from stratgauge.backtest.selection_outcomes import (
    SelectionTrackingPolicy,
    latest_selection_outcomes,
)
from stratgauge.backtest.selection_stats import SelectionOutcomeStats, summarize_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.tdx_import import TdxImportError, import_tdx_export
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
    SelectionBatch,
    SelectionCandidate,
    SelectionFormula,
    SelectionLifecycleEvent,
    SelectionLifecycleStatus,
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


class SelectionFormulaCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str | None = None
    description: str | None = None


class SelectionImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_base64: str = Field(min_length=1)
    selection_date: date
    exported_at: datetime
    source_filename: str | None = None
    formula_id: str | None = None
    formula_name: str | None = None
    formula_version: str | None = None
    formula_sha256: str | None = None

    @field_validator("exported_at")
    @classmethod
    def require_aware_export_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exported_at must include a timezone")
        return value


class SelectionLifecycleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


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


class SelectionMatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["akshare", "baostock", "waizao"]
    evaluated_at: datetime
    horizon_sessions: int = Field(gt=0)
    position_notional: Decimal = Field(gt=0)

    @field_validator("evaluated_at")
    @classmethod
    def require_aware_evaluation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        return value

    @field_validator("position_notional", mode="before")
    @classmethod
    def require_finite_notional(cls, value: Decimal | str | int | float) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("position_notional must be finite")
        return result


class SelectionFormulaView(BaseModel):
    model_config = ConfigDict(frozen=True)

    formula: SelectionFormula


class SelectionManagedFormulaView(BaseModel):
    model_config = ConfigDict(frozen=True)

    formula: SelectionFormula
    active: bool
    lifecycle: tuple[SelectionLifecycleEvent, ...]


class SelectionManagedBatchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    active: bool
    label_count: int = Field(ge=0)
    outcome_count: int = Field(ge=0)
    lifecycle: tuple[SelectionLifecycleEvent, ...]


class SelectionMatureView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str
    provider: str
    outcomes_written: int = Field(ge=0)
    rejected_candidates: tuple[tuple[str, str], ...]
    market_snapshot_ids: tuple[str, ...]


class SelectionCandidateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: SelectionCandidate
    labels: tuple[ManualSelectionLabel, ...] = ()
    outcomes: tuple[SelectionOutcome, ...] = ()


class SelectionBatchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    candidates: tuple[SelectionCandidateView, ...]


class SelectionManagedBatchDetailView(BaseModel):
    model_config = ConfigDict(frozen=True)

    view: SelectionBatchView
    active: bool
    lifecycle: tuple[SelectionLifecycleEvent, ...]


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

    def list_formulas(self) -> tuple[SelectionFormula, ...]:
        try:
            return self._store.list_formulas()
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def create_formula(self, request: SelectionFormulaCreateRequest) -> SelectionFormulaView:
        try:
            formula = self._store.create_formula(
                name=request.name,
                version=request.version,
                description=request.description,
            )
            return SelectionFormulaView(formula=formula)
        except (SelectionStoreError, ValueError) as exc:
            raise SelectionApiError("SELECTION_FORMULA_INVALID", str(exc), 422) from exc

    def list_managed_formulas(self) -> tuple[SelectionManagedFormulaView, ...]:
        try:
            formulas = self._store.list_formulas(include_archived=True)
            return tuple(
                SelectionManagedFormulaView(
                    formula=formula,
                    active=self._store.is_entity_active("formula", formula.formula_id),
                    lifecycle=self._store.list_lifecycle_events("formula", formula.formula_id),
                )
                for formula in formulas
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def list_managed_batches(self) -> tuple[SelectionManagedBatchView, ...]:
        try:
            managed: list[SelectionManagedBatchView] = []
            for manifest in self._store.list_manifests(include_archived=True):
                imported = self._store.load_import(manifest.batch_id)
                label_count = sum(
                    len(self._store.list_labels(candidate.candidate_id))
                    for candidate in imported.candidates
                )
                outcome_count = sum(
                    len(self._store.list_outcomes(candidate.candidate_id))
                    for candidate in imported.candidates
                )
                managed.append(
                    SelectionManagedBatchView(
                        batch=imported.batch,
                        active=self._store.is_entity_active("batch", imported.batch.batch_id),
                        label_count=label_count,
                        outcome_count=outcome_count,
                        lifecycle=self._store.list_lifecycle_events(
                            "batch", imported.batch.batch_id
                        ),
                    )
                )
            return tuple(
                sorted(
                    managed,
                    key=lambda item: (item.batch.selection_date, item.batch.batch_id),
                    reverse=True,
                )
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def get_managed_batch(self, batch_id: str) -> SelectionManagedBatchDetailView:
        try:
            imported = self._store.load_import(batch_id)
            return SelectionManagedBatchDetailView(
                view=self._view(imported),
                active=self._store.is_entity_active("batch", batch_id),
                lifecycle=self._store.list_lifecycle_events("batch", batch_id),
            )
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def set_formula_status(
        self,
        formula_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedFormulaView:
        try:
            self._store.set_formula_status(
                formula_id,
                status,
                changed_at=datetime.now(UTC),
                reason=request.reason,
            )
            return next(
                item
                for item in self.list_managed_formulas()
                if item.formula.formula_id == formula_id
            )
        except StopIteration as exc:
            raise SelectionApiError("SELECTION_FORMULA_NOT_FOUND", "选股公式不存在。", 404) from exc
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_FORMULA_NOT_FOUND", "选股公式不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def set_batch_status(
        self,
        batch_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedBatchDetailView:
        try:
            self._store.set_batch_status(
                batch_id,
                status,
                changed_at=datetime.now(UTC),
                reason=request.reason,
            )
            return self.get_managed_batch(batch_id)
        except SelectionApiError:
            raise
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def load_raw_batch(self, batch_id: str) -> tuple[bytes, str]:
        try:
            imported = self._store.load_import(batch_id)
            filename = imported.batch.source_filename or f"selection-{batch_id}.txt"
            return self._store.load_raw(batch_id), filename
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def export_dataset(self, batch_id: str) -> bytes:
        try:
            rows = build_selection_dataset(self._store, batch_id)
            return serialize_selection_dataset(rows).encode("utf-8")
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

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
            formula_name, formula_version, formula_id = self._formula_metadata(request)
            imported = import_tdx_export(
                content,
                selection_date=request.selection_date,
                exported_at=request.exported_at,
                imported_at=datetime.now(UTC),
                source_filename=request.source_filename,
                formula_name=formula_name,
                formula_version=formula_version,
                formula_id=formula_id,
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

    def _formula_metadata(
        self,
        request: SelectionImportRequest,
    ) -> tuple[str, str | None, str | None]:
        if request.formula_id is not None:
            try:
                formula = self._store.load_formula(request.formula_id)
            except SelectionStoreError as exc:
                raise SelectionApiError(
                    "SELECTION_FORMULA_NOT_FOUND", "选股公式不存在。", 404
                ) from exc
            if not self._store.is_entity_active("formula", formula.formula_id):
                raise SelectionApiError("SELECTION_FORMULA_ARCHIVED", "选股公式已停用。", 422)
            return formula.name, formula.version, formula.formula_id
        if request.formula_name is None:
            raise SelectionApiError("SELECTION_FORMULA_REQUIRED", "必须选择一个选股公式。", 422)
        return request.formula_name, request.formula_version, None

    def mature_batch(
        self,
        batch_id: str,
        request: SelectionMatureRequest,
        *,
        adapter: MarketDataAdapter,
        market_store: SnapshotStore,
    ) -> SelectionMatureView:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            result = run_selection_outcome_job(
                imported,
                adapter=adapter,
                selection_store=self._store,
                market_store=market_store,
                evaluated_at=request.evaluated_at,
                policy=SelectionTrackingPolicy(
                    horizon_sessions=request.horizon_sessions,
                    position_notional=request.position_notional,
                ),
            )
            return SelectionMatureView(
                batch_id=batch_id,
                provider=request.provider,
                outcomes_written=len(result.outcomes),
                rejected_candidates=result.rejected_candidates,
                market_snapshot_ids=result.market_snapshot_ids,
            )
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except (OSError, ValueError) as exc:
            raise SelectionApiError("SELECTION_MATURE_FAILED", str(exc), 422) from exc

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
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
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
