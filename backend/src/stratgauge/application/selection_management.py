"""Management-facing application operations for immutable selection evidence."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal

from stratgauge.backtest.selection_dataset import (
    SelectionAnnotationDatasetRow,
    SelectionDatasetRow,
    build_annotation_dataset,
    build_selection_dataset,
    serialize_annotation_dataset,
    serialize_selection_dataset,
)
from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.backtest.selection_stats import summarize_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionBatch,
    SelectionCandidate,
    SelectionFormula,
    SelectionLifecycleEvent,
    SelectionLifecycleStatus,
    SelectionOutcome,
    SelectionReusableTagCategory,
    TdxSelectionImport,
)
from stratgauge.domain.selections import (
    SelectionReusableTagView as DomainSelectionReusableTagView,
)

if TYPE_CHECKING:
    from stratgauge.api.selections import (
        SelectionBatchPage,
        SelectionBatchView,
        SelectionManagedBatchDetailView,
        SelectionManagedBatchPage,
        SelectionManagedBatchView,
        SelectionManagedFormulaView,
        SelectionManagementStatsView,
    )


class SelectionApiError(Exception):
    """Expected API-facing selection workflow failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


BatchViewFactory = Callable[[TdxSelectionImport], "SelectionBatchView"]


class SelectionManagementService:
    """Handle formula, tag, batch lifecycle, and export projections."""

    def __init__(self, store: SelectionImportStore, *, view_factory: BatchViewFactory) -> None:
        self._store = store
        self._view_factory = view_factory

    def list_formulas(self) -> tuple[SelectionFormula, ...]:
        try:
            return self._store.list_formulas()
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def create_formula(
        self,
        *,
        name: str,
        version: str | None,
        description: str | None,
    ) -> SelectionFormula:
        try:
            return self._store.create_formula(
                name=name,
                version=version,
                description=description,
            )
        except (SelectionStoreError, ValueError) as exc:
            raise SelectionApiError("SELECTION_FORMULA_INVALID", str(exc), 422) from exc

    def list_reusable_tags(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[DomainSelectionReusableTagView, ...]:
        try:
            return self._store.list_reusable_tags(include_archived=include_archived)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def create_reusable_tag(
        self,
        *,
        category: SelectionReusableTagCategory,
        label: str,
    ) -> DomainSelectionReusableTagView:
        try:
            tag = self._store.create_reusable_tag(
                category=category,
                label=label,
                created_at=datetime.now(UTC),
            )
            return self._get_reusable_tag(tag.tag_id)
        except (SelectionStoreError, ValueError) as exc:
            raise SelectionApiError("ANNOTATION_TAG_INVALID", str(exc), 422) from exc

    def rename_reusable_tag(
        self,
        *,
        tag_id: str,
        label: str,
    ) -> DomainSelectionReusableTagView:
        try:
            self._store.set_reusable_tag_label(
                tag_id,
                label=label,
                occurred_at=datetime.now(UTC),
            )
            return self._get_reusable_tag(tag_id)
        except SelectionStoreError as exc:
            raise self._tag_error(exc, tag_id) from exc
        except ValueError as exc:
            raise SelectionApiError("ANNOTATION_TAG_INVALID", str(exc), 422) from exc

    def set_reusable_tag_status(
        self,
        *,
        tag_id: str,
        status: SelectionLifecycleStatus,
        reason: str | None,
    ) -> DomainSelectionReusableTagView:
        try:
            self._store.set_reusable_tag_status(
                tag_id,
                status,
                occurred_at=datetime.now(UTC),
                reason=reason,
            )
            return self._get_reusable_tag(tag_id)
        except SelectionStoreError as exc:
            raise self._tag_error(exc, tag_id) from exc
        except ValueError as exc:
            raise SelectionApiError("ANNOTATION_TAG_INVALID", str(exc), 422) from exc

    def _get_reusable_tag(self, tag_id: str) -> DomainSelectionReusableTagView:
        for tag in self.list_reusable_tags(include_archived=True):
            if tag.tag_id == tag_id:
                return tag
        raise SelectionApiError("ANNOTATION_TAG_NOT_FOUND", "评审标签不存在。", 404)

    @staticmethod
    def _tag_error(exc: SelectionStoreError, tag_id: str) -> SelectionApiError:
        message = str(exc)
        if "does not exist" in message or "invalid reusable tag id" in message:
            return SelectionApiError("ANNOTATION_TAG_NOT_FOUND", "评审标签不存在。", 404)
        return SelectionApiError("ANNOTATION_TAG_INVALID", message, 422)

    def list_managed_formulas(self) -> tuple[SelectionManagedFormulaView, ...]:
        from stratgauge.api.selections import SelectionManagedFormulaView

        try:
            formulas = self._store.list_formulas(include_archived=True)
            lifecycle_by_formula = self._store.list_lifecycle_events_for_entities(
                "formula",
                (formula.formula_id for formula in formulas),
            )
            return tuple(
                SelectionManagedFormulaView(
                    formula=formula,
                    active=is_entity_active(lifecycle_by_formula[formula.formula_id]),
                    lifecycle=lifecycle_by_formula[formula.formula_id],
                )
                for formula in formulas
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def list_managed_batches(self) -> tuple[SelectionManagedBatchView, ...]:
        from stratgauge.api.selections import SelectionManagedBatchView

        try:
            manifests = self._store.list_manifests(include_archived=True)
            imports = tuple(self._store.load_import(manifest.batch_id) for manifest in manifests)
            candidate_ids = tuple(
                candidate.candidate_id for imported in imports for candidate in imported.candidates
            )
            labels_by_candidate = self._store.list_labels_for_candidates(candidate_ids)
            annotations_by_candidate = self._store.list_annotations_for_candidates(candidate_ids)
            exclusions_by_candidate = self._store.list_exclusions_for_candidates(candidate_ids)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
            lifecycle_by_batch = self._store.list_lifecycle_events_for_entities(
                "batch",
                (imported.batch.batch_id for imported in imports),
            )
            managed = tuple(
                SelectionManagedBatchView(
                    batch=imported.batch,
                    active=is_entity_active(lifecycle_by_batch[imported.batch.batch_id]),
                    label_count=sum(
                        len(labels_by_candidate[candidate.candidate_id])
                        for candidate in imported.candidates
                    ),
                    annotation_count=sum(
                        len(annotations_by_candidate[candidate.candidate_id])
                        for candidate in imported.candidates
                    ),
                    exclusion_count=sum(
                        len(exclusions_by_candidate[candidate.candidate_id])
                        for candidate in imported.candidates
                    ),
                    outcome_count=sum(
                        len(outcomes_by_candidate[candidate.candidate_id])
                        for candidate in imported.candidates
                    ),
                    lifecycle=lifecycle_by_batch[imported.batch.batch_id],
                )
                for imported in imports
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

    def list_managed_batches_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionManagedBatchPage:
        from stratgauge.api.selections import SelectionManagedBatchPage

        validate_page(page, page_size)
        items = self.list_managed_batches()
        page_items, total, has_next = page_slice(items, page, page_size)
        return SelectionManagedBatchPage(
            items=page_items,
            page=page,
            page_size=page_size,
            total=total,
            has_next=has_next,
        )

    def get_managed_batch(self, batch_id: str) -> SelectionManagedBatchDetailView:
        from stratgauge.api.selections import SelectionManagedBatchDetailView

        try:
            imported = self._store.load_import(batch_id)
            return SelectionManagedBatchDetailView(
                view=self._view_factory(imported),
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
        *,
        formula_id: str,
        status: SelectionLifecycleStatus,
        reason: str | None,
    ) -> SelectionManagedFormulaView:
        try:
            self._store.set_formula_status(
                formula_id,
                status,
                changed_at=datetime.now(UTC),
                reason=reason,
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
        *,
        batch_id: str,
        status: SelectionLifecycleStatus,
        reason: str | None,
    ) -> SelectionManagedBatchDetailView:
        try:
            self._store.set_batch_status(
                batch_id,
                status,
                changed_at=datetime.now(UTC),
                reason=reason,
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

    def export_annotation_dataset(self, batch_id: str) -> bytes:
        try:
            rows = build_annotation_dataset(self._store, batch_id)
            return serialize_annotation_dataset(rows).encode("utf-8")
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_ANNOTATION_DATASET_INVALID", str(exc), 422) from exc

    def stream_dataset(self, batch_id: str) -> Iterator[bytes]:
        try:
            imported = self._store.load_import(batch_id)
            candidate_ids = tuple(candidate.candidate_id for candidate in imported.candidates)
            labels_by_candidate = self._store.list_labels_for_candidates(candidate_ids)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

        def generate() -> Iterator[bytes]:
            for candidate in imported.candidates:
                labels = labels_by_candidate[candidate.candidate_id]
                latest_label = labels[-1] if labels else None
                for outcome in latest_selection_outcomes(
                    outcomes_by_candidate[candidate.candidate_id]
                ) or (None,):
                    row = selection_dataset_row(
                        imported.batch,
                        candidate,
                        latest_label,
                        outcome,
                    )
                    yield f"{row.model_dump_json()}\n".encode()

        return generate()

    def stream_annotation_dataset(self, batch_id: str) -> Iterator[bytes]:
        try:
            imported = self._store.load_import(batch_id)
            candidate_ids = tuple(candidate.candidate_id for candidate in imported.candidates)
            annotations_by_candidate = self._store.list_annotations_for_candidates(candidate_ids)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

        annotated_rows = [
            (
                annotation.recorded_at,
                annotation.annotation_id,
                imported.batch,
                candidate,
                annotation,
                latest_outcome_after_annotation(
                    latest_selection_outcomes(outcomes_by_candidate[candidate.candidate_id]),
                    annotation,
                ),
            )
            for candidate in imported.candidates
            for annotation in annotations_by_candidate[candidate.candidate_id]
        ]

        def generate() -> Iterator[bytes]:
            for _, _, batch, candidate, annotation, outcome in sorted(
                annotated_rows,
                key=lambda item: (item[0], item[1]),
            ):
                row = annotation_dataset_row(batch, candidate, annotation, outcome)
                yield f"{row.model_dump_json()}\n".encode()

        return generate()

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

    def list_batches_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionBatchPage:
        from stratgauge.api.selections import SelectionBatchPage

        validate_page(page, page_size)
        items = self.list_batches()
        page_items, total, has_next = page_slice(items, page, page_size)
        return SelectionBatchPage(
            items=page_items,
            page=page,
            page_size=page_size,
            total=total,
            has_next=has_next,
        )

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
        from stratgauge.api.selections import SelectionManagementStatsView

        if selection_date_from and selection_date_to and selection_date_from > selection_date_to:
            raise SelectionApiError(
                "INVALID_SELECTION_STATS_FILTER",
                "选股日期起始值不能晚于结束值。",
                422,
            )
        formula_version = formula_version.strip() if formula_version else None
        tracking_policy_id = tracking_policy_id.strip() if tracking_policy_id else None
        try:
            manifests = self._store.list_manifests(include_archived=True)
            imports = tuple(self._store.load_import(manifest.batch_id) for manifest in manifests)
            candidate_ids = tuple(
                candidate.candidate_id for imported in imports for candidate in imported.candidates
            )
            labels_by_candidate = self._store.list_labels_for_candidates(candidate_ids)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
            lifecycle_by_batch = self._store.list_lifecycle_events_for_entities(
                "batch",
                (imported.batch.batch_id for imported in imports),
            )
            outcomes: list[SelectionOutcome] = []
            tracking_policy_ids: set[str] = set()
            batch_count = 0
            candidate_count = 0
            pending = 0
            for imported in imports:
                batch = imported.batch
                active = is_entity_active(lifecycle_by_batch[batch.batch_id])
                if lifecycle == "active" and not active:
                    continue
                if lifecycle == "archived" and active:
                    continue
                if formula_version is not None and batch.formula_version != formula_version:
                    continue
                if source is not None and batch.source != source:
                    continue
                if selection_date_from and batch.selection_date < selection_date_from:
                    continue
                if selection_date_to and batch.selection_date > selection_date_to:
                    continue
                batch_count += 1
                for candidate in imported.candidates:
                    labels = labels_by_candidate[candidate.candidate_id]
                    label = labels[-1] if labels else None
                    if manual_decision is not None and (
                        label is None or label.decision != manual_decision
                    ):
                        continue
                    candidate_count += 1
                    candidate_outcomes = latest_selection_outcomes(
                        outcomes_by_candidate[candidate.candidate_id]
                    )
                    tracking_policy_ids.update(
                        outcome.tracking_policy_id for outcome in candidate_outcomes
                    )
                    if tracking_policy_id is not None:
                        candidate_outcomes = tuple(
                            outcome
                            for outcome in candidate_outcomes
                            if outcome.tracking_policy_id == tracking_policy_id
                        )
                    if not candidate_outcomes:
                        pending += 1
                    outcomes.extend(candidate_outcomes)
            summary = summarize_selection_outcomes(outcomes)
            return SelectionManagementStatsView(
                batch_count=batch_count,
                candidate_count=candidate_count,
                pending=pending,
                sample_count=summary.total,
                tracking_policy_ids=tuple(sorted(tracking_policy_ids)),
                summary=summary,
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc


def selection_dataset_row(
    batch: SelectionBatch,
    candidate: SelectionCandidate,
    label: ManualSelectionLabel | None,
    outcome: SelectionOutcome | None,
) -> SelectionDatasetRow:
    return SelectionDatasetRow(
        candidate_id=candidate.candidate_id,
        batch_id=batch.batch_id,
        symbol=candidate.symbol,
        selection_date=batch.selection_date.isoformat(),
        source_filename=batch.source_filename,
        formula_id=batch.formula_id,
        formula_name=batch.formula_name,
        formula_version=batch.formula_version,
        manual_decision=label.decision if label else None,
        manual_labeled_at=label.labeled_at.isoformat() if label else None,
        tracking_policy_id=outcome.tracking_policy_id if outcome else None,
        outcome_policy_id=outcome.outcome_policy_id if outcome else None,
        cost_schedule_id=outcome.cost_schedule_id if outcome else None,
        horizon_sessions=outcome.horizon_sessions if outcome else None,
        position_notional=(
            str(outcome.position_notional)
            if outcome and outcome.position_notional is not None
            else None
        ),
        outcome_status=outcome.status if outcome else None,
        outcome_evaluated_at=outcome.evaluated_at.isoformat() if outcome else None,
        net_return=(
            str(outcome.net_return) if outcome and outcome.net_return is not None else None
        ),
        source_snapshot_ids=outcome.source_snapshot_ids if outcome else (),
    )


def annotation_dataset_row(
    batch: SelectionBatch,
    candidate: SelectionCandidate,
    annotation: SelectionAnnotation,
    outcome: SelectionOutcome | None,
) -> SelectionAnnotationDatasetRow:
    return SelectionAnnotationDatasetRow(
        annotation_id=annotation.annotation_id,
        candidate_id=candidate.candidate_id,
        batch_id=batch.batch_id,
        symbol=candidate.symbol,
        selection_date=batch.selection_date.isoformat(),
        source_filename=batch.source_filename,
        formula_id=batch.formula_id,
        formula_name=batch.formula_name,
        formula_version=batch.formula_version,
        annotation_reviewed_at=annotation.reviewed_at.isoformat(),
        annotation_recorded_at=annotation.recorded_at.isoformat(),
        overall_score=annotation.overall_score,
        outlook=annotation.outlook,
        decision=annotation.decision,
        position_tags=annotation.position_tags,
        pattern_tags=annotation.pattern_tags,
        price_volume_tags=annotation.price_volume_tags,
        sector_tags=annotation.sector_tags,
        taxonomy_version=annotation.taxonomy_version,
        annotation_note=annotation.note,
        tracking_policy_id=outcome.tracking_policy_id if outcome else None,
        outcome_policy_id=outcome.outcome_policy_id if outcome else None,
        cost_schedule_id=outcome.cost_schedule_id if outcome else None,
        horizon_sessions=outcome.horizon_sessions if outcome else None,
        position_notional=(
            str(outcome.position_notional)
            if outcome and outcome.position_notional is not None
            else None
        ),
        outcome_status=outcome.status if outcome else None,
        outcome_evaluated_at=outcome.evaluated_at.isoformat() if outcome else None,
        net_return=(
            str(outcome.net_return) if outcome and outcome.net_return is not None else None
        ),
        source_snapshot_ids=outcome.source_snapshot_ids if outcome else (),
    )


def latest_outcome_after_annotation(
    outcomes: tuple[SelectionOutcome, ...],
    annotation: SelectionAnnotation,
) -> SelectionOutcome | None:
    future = tuple(outcome for outcome in outcomes if outcome.evaluated_at > annotation.recorded_at)
    return future[-1] if future else None


def validate_page(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError("page must be positive")
    if page_size < 1 or page_size > 200:
        raise ValueError("page_size must be between 1 and 200")


def page_slice[ItemT](
    items: tuple[ItemT, ...],
    page: int,
    page_size: int,
) -> tuple[tuple[ItemT, ...], int, bool]:
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], total, end < total


def is_entity_active(events: tuple[SelectionLifecycleEvent, ...]) -> bool:
    return not events or events[-1].status is SelectionLifecycleStatus.ACTIVE
