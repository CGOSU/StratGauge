"""API contracts and service for the semi-manual selection queue."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.application.selection_management import SelectionApiError as SelectionApiError
from stratgauge.application.selection_management import (
    SelectionManagementService,
    page_slice,
    validate_page,
)
from stratgauge.backtest.selection_analysis import (
    SelectionAnalysisError,
    SelectionCandidateAnalysis,
    analyze_selection_candidate,
)
from stratgauge.backtest.selection_job import run_selection_outcome_job
from stratgauge.backtest.selection_outcomes import (
    SelectionPerformancePolicy,
    SelectionTrackingPolicy,
    latest_selection_outcomes,
)
from stratgauge.backtest.selection_stats import SelectionOutcomeStats, summarize_selection_outcomes
from stratgauge.backtest.selection_tasks import (
    SelectionMaturityTaskError,
    build_maturity_task,
    create_maturity_task,
    run_maturity_task,
)
from stratgauge.data_pipeline.manual_selection import (
    ManualSelectionCandidateInput,
    ManualSelectionError,
    build_manual_selection,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.tdx_import import TdxImportError, import_tdx_export
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.domain.models import Exchange
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionBatch,
    SelectionCandidate,
    SelectionCandidateExclusion,
    SelectionCandidateReviewState,
    SelectionFormula,
    SelectionLifecycleEvent,
    SelectionLifecycleStatus,
    SelectionMaturityTask,
    SelectionOutcome,
    SelectionPatternTag,
    SelectionPerformance,
    SelectionPositionTag,
    SelectionPriceVolumeTag,
    SelectionReusableTagCategory,
    SelectionReusableTagSnapshot,
    SelectionSectorTag,
    TdxSelectionImport,
)
from stratgauge.domain.selections import (
    SelectionReusableTagView as DomainSelectionReusableTagView,
)


class SelectionFormulaCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str | None = None
    description: str | None = None


class SelectionReusableTagCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: SelectionReusableTagCategory
    label: str = Field(min_length=1)


class SelectionReusableTagRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)


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


class ManualSelectionCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_code: str
    exchange: Exchange
    name: str | None = None
    note: str | None = None

    @field_validator("local_code")
    @classmethod
    def normalize_local_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
            raise ValueError("local_code must contain exactly six digits")
        return normalized

    @field_validator("name", "note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class ManualSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_date: date
    recorded_at: datetime
    formula_id: str = Field(min_length=1)
    candidates: list[ManualSelectionCandidateRequest] = Field(min_length=1)

    @field_validator("recorded_at")
    @classmethod
    def require_aware_recorded_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
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


class SelectionAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    reviewed_at: datetime
    overall_score: int = Field(ge=1, le=5)
    outlook: SelectionAnnotationOutlook
    decision: SelectionAnnotationDecision
    position_tags: tuple[SelectionPositionTag, ...] = ()
    pattern_tags: tuple[SelectionPatternTag, ...] = ()
    price_volume_tags: tuple[SelectionPriceVolumeTag, ...] = ()
    sector_tags: tuple[SelectionSectorTag, ...] = ()
    reusable_tag_ids: tuple[str, ...] = ()
    note: str | None = None

    @field_validator("reusable_tag_ids")
    @classmethod
    def validate_reusable_tag_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized):
            raise ValueError("reusable tag IDs must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("reusable tag IDs must not contain duplicates")
        return normalized

    @field_validator("reviewed_at")
    @classmethod
    def require_aware_review_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value

    @field_validator("note")
    @classmethod
    def normalize_annotation_note(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class SelectionExclusionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    excluded_at: datetime
    reason: str = Field(min_length=1)

    @field_validator("excluded_at")
    @classmethod
    def require_aware_exclusion_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("excluded_at must include a timezone")
        return value

    @field_validator("reason")
    @classmethod
    def normalize_exclusion_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("exclusion reason must not be empty")
        return normalized


class SelectionMatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["tushare", "akshare", "baostock", "waizao"]
    evaluated_at: datetime
    horizon_sessions: int = Field(default=10, gt=0)
    position_notional: Decimal = Field(gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: int = Field(default=900, gt=0, le=86400)
    target_return: Decimal | None = Field(default=None, ge=0)

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
    annotation_count: int = Field(default=0, ge=0)
    exclusion_count: int = Field(default=0, ge=0)
    outcome_count: int = Field(ge=0)
    lifecycle: tuple[SelectionLifecycleEvent, ...]


class SelectionMatureView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str
    provider: str
    outcomes_written: int = Field(ge=0)
    performances_written: int = Field(default=0, ge=0)
    rejected_candidates: tuple[tuple[str, str], ...]
    market_snapshot_ids: tuple[str, ...]


class SelectionCandidateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: SelectionCandidate
    labels: tuple[ManualSelectionLabel, ...] = ()
    annotations: tuple[SelectionAnnotation, ...] = ()
    exclusions: tuple[SelectionCandidateExclusion, ...] = ()
    latest_annotation: SelectionAnnotation | None = None
    latest_exclusion: SelectionCandidateExclusion | None = None
    review_state: SelectionCandidateReviewState = SelectionCandidateReviewState.UNREVIEWED
    excluded: bool = False
    outcomes: tuple[SelectionOutcome, ...] = ()
    performances: tuple[SelectionPerformance, ...] = ()


class SelectionObservationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    candidate: SelectionCandidateView


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


class SelectionManagementStatsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    pending: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    tracking_policy_ids: tuple[str, ...] = ()
    summary: SelectionOutcomeStats


class SelectionBatchPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[SelectionBatch, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


class SelectionObservationPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[SelectionObservationView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


class SelectionManagedBatchPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[SelectionManagedBatchView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


class SelectionService:
    """Application service backed by immutable local selection storage."""

    def __init__(self, store: SelectionImportStore) -> None:
        self._store = store
        self._management = SelectionManagementService(store, view_factory=self._view)

    def list_formulas(self) -> tuple[SelectionFormula, ...]:
        return self._management.list_formulas()

    def create_formula(self, request: SelectionFormulaCreateRequest) -> SelectionFormulaView:
        formula = self._management.create_formula(
            name=request.name,
            version=request.version,
            description=request.description,
        )
        return SelectionFormulaView(formula=formula)

    def list_reusable_tags(self) -> tuple[DomainSelectionReusableTagView, ...]:
        return self._management.list_reusable_tags()

    def list_managed_reusable_tags(self) -> tuple[DomainSelectionReusableTagView, ...]:
        return self._management.list_reusable_tags(include_archived=True)

    def create_reusable_tag(
        self,
        request: SelectionReusableTagCreateRequest,
    ) -> DomainSelectionReusableTagView:
        return self._management.create_reusable_tag(
            category=request.category,
            label=request.label,
        )

    def rename_reusable_tag(
        self,
        tag_id: str,
        request: SelectionReusableTagRenameRequest,
    ) -> DomainSelectionReusableTagView:
        return self._management.rename_reusable_tag(tag_id=tag_id, label=request.label)

    def set_reusable_tag_status(
        self,
        tag_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> DomainSelectionReusableTagView:
        return self._management.set_reusable_tag_status(
            tag_id=tag_id,
            status=status,
            reason=request.reason,
        )

    def list_managed_formulas(self) -> tuple[SelectionManagedFormulaView, ...]:
        return self._management.list_managed_formulas()

    def list_managed_batches(self) -> tuple[SelectionManagedBatchView, ...]:
        return self._management.list_managed_batches()

    def list_managed_batches_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionManagedBatchPage:
        return self._management.list_managed_batches_page(page=page, page_size=page_size)

    def get_managed_batch(self, batch_id: str) -> SelectionManagedBatchDetailView:
        return self._management.get_managed_batch(batch_id)

    def set_formula_status(
        self,
        formula_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedFormulaView:
        return self._management.set_formula_status(
            formula_id=formula_id,
            status=status,
            reason=request.reason,
        )

    def set_batch_status(
        self,
        batch_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedBatchDetailView:
        return self._management.set_batch_status(
            batch_id=batch_id,
            status=status,
            reason=request.reason,
        )

    def load_raw_batch(self, batch_id: str) -> tuple[bytes, str]:
        return self._management.load_raw_batch(batch_id)

    def export_dataset(self, batch_id: str) -> bytes:
        return self._management.export_dataset(batch_id)

    def export_annotation_dataset(self, batch_id: str) -> bytes:
        return self._management.export_annotation_dataset(batch_id)

    def stream_dataset(self, batch_id: str) -> Iterator[bytes]:
        return self._management.stream_dataset(batch_id)

    def stream_annotation_dataset(self, batch_id: str) -> Iterator[bytes]:
        return self._management.stream_annotation_dataset(batch_id)

    def list_observation_pool(
        self,
        *,
        review_state: SelectionCandidateReviewState | None = None,
        batch_id: str | None = None,
        include_excluded: bool = False,
    ) -> tuple[SelectionObservationView, ...]:
        try:
            views: list[SelectionObservationView] = []
            imports = tuple(
                self._store.load_import(manifest.batch_id)
                for manifest in self._store.list_manifests()
                if batch_id is None or manifest.batch_id == batch_id
            )
            candidates = tuple(
                candidate for imported in imports for candidate in imported.candidates
            )
            candidate_views = {
                view.candidate.candidate_id: view for view in self._candidate_views(candidates)
            }
            for imported in imports:
                for candidate in imported.candidates:
                    candidate_view = candidate_views[candidate.candidate_id]
                    if candidate_view.excluded and not include_excluded:
                        continue
                    if review_state is not None and candidate_view.review_state != review_state:
                        continue
                    views.append(
                        SelectionObservationView(
                            batch=imported.batch,
                            candidate=candidate_view,
                        )
                    )
            return tuple(
                sorted(
                    views,
                    key=lambda item: (
                        item.batch.selection_date,
                        item.batch.batch_id,
                        item.candidate.candidate.symbol,
                    ),
                    reverse=True,
                )
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def list_batches(self) -> tuple[SelectionBatch, ...]:
        return self._management.list_batches()

    def list_observation_pool_page(
        self,
        *,
        review_state: SelectionCandidateReviewState | None = None,
        batch_id: str | None = None,
        include_excluded: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionObservationPage:
        validate_page(page, page_size)
        items = self.list_observation_pool(
            review_state=review_state,
            batch_id=batch_id,
            include_excluded=include_excluded,
        )
        page_items, total, has_next = page_slice(items, page, page_size)
        return SelectionObservationPage(
            items=page_items,
            page=page,
            page_size=page_size,
            total=total,
            has_next=has_next,
        )

    def list_batches_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionBatchPage:
        return self._management.list_batches_page(page=page, page_size=page_size)

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

    def get_candidate(self, candidate_id: str) -> SelectionCandidate:
        try:
            for manifest in self._store.list_manifests(include_archived=True):
                imported = self._store.load_import(manifest.batch_id)
                for candidate in imported.candidates:
                    if candidate.candidate_id == candidate_id:
                        return candidate
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        raise SelectionApiError("SELECTION_CANDIDATE_NOT_FOUND", "候选股票不存在。", 404)

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

    def create_manual_batch(self, request: ManualSelectionRequest) -> SelectionBatchView:
        formula = self._load_active_formula(request.formula_id)
        try:
            result = build_manual_selection(
                selection_date=request.selection_date,
                recorded_at=request.recorded_at,
                imported_at=datetime.now(UTC),
                formula_id=formula.formula_id,
                formula_name=formula.name,
                formula_version=formula.version,
                candidates=tuple(
                    ManualSelectionCandidateInput(
                        local_code=candidate.local_code,
                        exchange=candidate.exchange,
                        name=candidate.name,
                        note=candidate.note,
                    )
                    for candidate in request.candidates
                ),
            )
            self._store.write(result.raw_content, result.imported)
            return self._view(self._store.load_import(result.imported.batch.batch_id))
        except ManualSelectionError as exc:
            raise SelectionApiError("INVALID_MANUAL_SELECTION", str(exc), 422) from exc
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("INVALID_MANUAL_SELECTION", str(exc), 422) from exc

    def _formula_metadata(
        self,
        request: SelectionImportRequest,
    ) -> tuple[str, str | None, str | None]:
        if request.formula_id is not None:
            formula = self._load_active_formula(request.formula_id)
            return formula.name, formula.version, formula.formula_id
        if request.formula_name is None:
            raise SelectionApiError("SELECTION_FORMULA_REQUIRED", "必须选择一个选股公式。", 422)
        return request.formula_name, request.formula_version, None

    def _load_active_formula(self, formula_id: str) -> SelectionFormula:
        try:
            formula = self._store.load_formula(formula_id)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_FORMULA_NOT_FOUND", "选股公式不存在。", 404) from exc
        if not self._store.is_entity_active("formula", formula.formula_id):
            raise SelectionApiError("SELECTION_FORMULA_ARCHIVED", "选股公式已停用。", 422)
        return formula

    def create_maturity_task(
        self,
        batch_id: str,
        request: SelectionMatureRequest,
    ) -> SelectionMaturityTask:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            policy = SelectionTrackingPolicy(
                horizon_sessions=request.horizon_sessions,
                position_notional=request.position_notional,
            )
            task = build_maturity_task(
                imported,
                provider=request.provider,
                evaluated_at=request.evaluated_at,
                policy=policy,
                performance_policy=SelectionPerformancePolicy(
                    target_return=request.target_return,
                ),
                max_retries=request.max_retries,
                timeout_seconds=request.timeout_seconds,
                created_at=datetime.now(UTC),
            )
            return create_maturity_task(self._store, task, now=datetime.now(UTC))
        except SelectionApiError:
            raise
        except SelectionMaturityTaskError as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_INVALID", str(exc), 422) from exc
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_INVALID", str(exc), 422) from exc

    def list_maturity_tasks(self, batch_id: str) -> tuple[SelectionMaturityTask, ...]:
        try:
            self._store.load_import(batch_id)
            return self._store.list_maturity_tasks(batch_id=batch_id)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def get_maturity_task(self, task_id: str) -> SelectionMaturityTask:
        try:
            return self._store.load_maturity_task(task_id)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_MATURITY_TASK_NOT_FOUND", "成熟任务不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def run_maturity_task(
        self,
        task_id: str,
        *,
        adapter: MarketDataAdapter,
        market_store: SnapshotStore,
    ) -> SelectionMaturityTask:
        try:
            task = self._store.load_maturity_task(task_id)
            imported = self._store.load_import(task.batch_id)
            if not self._store.is_entity_active("batch", task.batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            return run_maturity_task(
                task,
                imported=imported,
                adapter=adapter,
                selection_store=self._store,
                market_store=market_store,
                now=datetime.now(UTC),
            )
        except SelectionApiError:
            raise
        except SelectionMaturityTaskError as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_FAILED", str(exc), 409) from exc
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_MATURITY_TASK_NOT_FOUND", "成熟任务不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except (OSError, ValueError) as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_FAILED", str(exc), 422) from exc

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
                performance_policy=SelectionPerformancePolicy(
                    target_return=request.target_return,
                ),
            )
            return SelectionMatureView(
                batch_id=batch_id,
                provider=request.provider,
                outcomes_written=len(result.outcomes),
                performances_written=len(result.performances),
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

    def analyze_candidate(
        self,
        batch_id: str,
        candidate_id: str,
        *,
        tracking_policy_id: str | None = None,
    ) -> SelectionCandidateAnalysis:
        try:
            return analyze_selection_candidate(
                self._store,
                batch_id=batch_id,
                candidate_id=candidate_id,
                tracking_policy_id=tracking_policy_id,
            )
        except SelectionAnalysisError as exc:
            message = str(exc)
            if "candidate does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            if "does not exist" in message:
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            if "multiple tracking policies" in message:
                raise SelectionApiError(
                    "TRACKING_POLICY_REQUIRED", "历史样本包含多个结果政策，请明确指定政策。", 422
                ) from exc
            raise SelectionApiError("SELECTION_ANALYSIS_FAILED", message, 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_ANALYSIS_INVALID", str(exc), 422) from exc

    def summarize_batch(self, batch_id: str) -> SelectionStatsView:
        try:
            imported = self._store.load_import(batch_id)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(
                candidate.candidate_id for candidate in imported.candidates
            )
            outcomes: list[SelectionOutcome] = []
            pending = 0
            for candidate in imported.candidates:
                candidate_outcomes = outcomes_by_candidate[candidate.candidate_id]
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

    def summarize_managed_batches(
        self,
        *,
        formula_version: str | None = None,
        source: Literal["tongdaxin", "manual"] | None = None,
        selection_date_from: date | None = None,
        selection_date_to: date | None = None,
        manual_decision: ManualSelectionDecision | None = None,
        tracking_policy_id: str | None = None,
        lifecycle: Literal["all", "active", "archived"] = "all",
    ) -> SelectionManagementStatsView:
        return self._management.summarize_managed_batches(
            formula_version=formula_version,
            source=source,
            selection_date_from=selection_date_from,
            selection_date_to=selection_date_to,
            manual_decision=manual_decision,
            tracking_policy_id=tracking_policy_id,
            lifecycle=lifecycle,
        )

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
                    "旧版人工标签必须在导出后、下一自然日开始前完成。",
                    422,
                ) from exc
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc

    def annotate_candidate(
        self,
        batch_id: str,
        request: SelectionAnnotationRequest,
    ) -> SelectionBatchView:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            if request.reviewed_at < imported.batch.exported_at:
                raise SelectionApiError(
                    "SELECTION_ANNOTATION_NOT_ALLOWED",
                    "人工评审时间不能早于导出时间。",
                    422,
                )
            recorded_at = datetime.now(UTC)
            reusable_tag_snapshots = self._resolve_reusable_tag_snapshots(request.reusable_tag_ids)
            annotation = SelectionAnnotation(
                annotation_id=_content_id(
                    "annotation",
                    request.candidate_id,
                    request.reviewed_at.isoformat(),
                    str(request.overall_score),
                    request.outlook.value,
                    request.decision.value,
                    ",".join(item.value for item in request.position_tags),
                    ",".join(item.value for item in request.pattern_tags),
                    ",".join(item.value for item in request.price_volume_tags),
                    ",".join(item.value for item in request.sector_tags),
                    ",".join(item.tag_id for item in reusable_tag_snapshots),
                    request.note or "",
                    recorded_at.isoformat(),
                ),
                candidate_id=request.candidate_id,
                reviewed_at=request.reviewed_at,
                recorded_at=recorded_at,
                overall_score=request.overall_score,
                outlook=request.outlook,
                decision=request.decision,
                position_tags=request.position_tags,
                pattern_tags=request.pattern_tags,
                price_volume_tags=request.price_volume_tags,
                sector_tags=request.sector_tags,
                reusable_tag_snapshots=reusable_tag_snapshots,
                note=request.note,
            )
            self._store.write_annotation(batch_id, annotation)
            return self.get_batch(batch_id)
        except SelectionApiError:
            raise
        except SelectionStoreError as exc:
            message = str(exc)
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_ANNOTATION_INVALID", str(exc), 422) from exc

    def _resolve_reusable_tag_snapshots(
        self,
        tag_ids: tuple[str, ...],
    ) -> tuple[SelectionReusableTagSnapshot, ...]:
        if not tag_ids:
            return ()
        try:
            tags = {
                tag.tag_id: tag for tag in self._store.list_reusable_tags(include_archived=True)
            }
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        snapshots: list[SelectionReusableTagSnapshot] = []
        for tag_id in tag_ids:
            tag = tags.get(tag_id)
            if tag is None:
                raise SelectionApiError("ANNOTATION_TAG_NOT_FOUND", "评审标签不存在。", 404)
            if not tag.active:
                raise SelectionApiError("ANNOTATION_TAG_ARCHIVED", "评审标签已归档。", 422)
            snapshots.append(
                SelectionReusableTagSnapshot(
                    tag_id=tag.tag_id,
                    category=tag.category,
                    label=tag.label,
                )
            )
        return tuple(snapshots)

    def exclude_candidate(
        self,
        batch_id: str,
        request: SelectionExclusionRequest,
    ) -> SelectionBatchView:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            if request.excluded_at < imported.batch.exported_at:
                raise SelectionApiError(
                    "SELECTION_EXCLUSION_NOT_ALLOWED",
                    "排除时间不能早于导出时间。",
                    422,
                )
            recorded_at = datetime.now(UTC)
            exclusion = SelectionCandidateExclusion(
                exclusion_id=_content_id(
                    "exclusion",
                    request.candidate_id,
                    request.excluded_at.isoformat(),
                    request.reason,
                    recorded_at.isoformat(),
                ),
                candidate_id=request.candidate_id,
                excluded_at=request.excluded_at,
                recorded_at=recorded_at,
                reason=request.reason,
            )
            self._store.write_exclusion(batch_id, exclusion)
            return self.get_batch(batch_id)
        except SelectionApiError:
            raise
        except SelectionStoreError as exc:
            message = str(exc)
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_EXCLUSION_INVALID", str(exc), 422) from exc

    def _candidate_views(
        self,
        candidates: tuple[SelectionCandidate, ...],
    ) -> tuple[SelectionCandidateView, ...]:
        candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
        labels_by_candidate = self._store.list_labels_for_candidates(candidate_ids)
        annotations_by_candidate = self._store.list_annotations_for_candidates(candidate_ids)
        exclusions_by_candidate = self._store.list_exclusions_for_candidates(candidate_ids)
        outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
        performances_by_candidate = self._store.list_performances_for_candidates(candidate_ids)
        return tuple(
            self._candidate_view(
                candidate,
                labels=labels_by_candidate[candidate.candidate_id],
                annotations=annotations_by_candidate[candidate.candidate_id],
                exclusions=exclusions_by_candidate[candidate.candidate_id],
                outcomes=outcomes_by_candidate[candidate.candidate_id],
                performances=performances_by_candidate[candidate.candidate_id],
            )
            for candidate in candidates
        )

    def _candidate_view(
        self,
        candidate: SelectionCandidate,
        *,
        labels: tuple[ManualSelectionLabel, ...],
        annotations: tuple[SelectionAnnotation, ...],
        exclusions: tuple[SelectionCandidateExclusion, ...],
        outcomes: tuple[SelectionOutcome, ...],
        performances: tuple[SelectionPerformance, ...],
    ) -> SelectionCandidateView:
        latest_annotation = annotations[-1] if annotations else None
        latest_exclusion = exclusions[-1] if exclusions else None
        return SelectionCandidateView(
            candidate=candidate,
            labels=labels,
            annotations=annotations,
            exclusions=exclusions,
            latest_annotation=latest_annotation,
            latest_exclusion=latest_exclusion,
            review_state=_review_state(latest_annotation),
            excluded=latest_exclusion is not None,
            outcomes=outcomes,
            performances=performances,
        )

    def _view(self, imported: TdxSelectionImport) -> SelectionBatchView:
        try:
            candidates = self._candidate_views(imported.candidates)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        return SelectionBatchView(batch=imported.batch, candidates=candidates)


def _content_id(prefix: str, *parts: str) -> str:
    identity = "\0".join(parts)
    return f"{prefix}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _review_state(
    annotation: SelectionAnnotation | None,
) -> SelectionCandidateReviewState:
    if annotation is None:
        return SelectionCandidateReviewState.UNREVIEWED
    states = {
        SelectionAnnotationDecision.DISCARD: SelectionCandidateReviewState.DISCARDED,
        SelectionAnnotationDecision.WATCH: SelectionCandidateReviewState.WATCHING,
        SelectionAnnotationDecision.PREPARE_BUY: SelectionCandidateReviewState.PREPARING,
    }
    return states[annotation.decision]


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SelectionApiError(
            "INVALID_SELECTION_FILE", "导出文件不是有效的 Base64 内容。", 422
        ) from exc
