"""Batch listing and management statistics methods."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Literal

from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.backtest.selection_stats import summarize_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionBatch,
    SelectionOutcome,
)

if TYPE_CHECKING:
    from stratgauge.api.selections import (
        SelectionBatchPage,
        SelectionManagementStatsView,
    )


from stratgauge.application.selection_management._base import (
    SelectionManagementBase,
)
from stratgauge.application.selection_management.errors import SelectionApiError  # noqa: F401
from stratgauge.application.selection_management.export_rows import (  # noqa: F401
    annotation_dataset_row,
    latest_outcome_after_annotation,
    selection_dataset_row,
)
from stratgauge.application.selection_management.pagination import (  # noqa: F401
    is_entity_active,
    page_slice,
    validate_page,
)


class QueriesMixin(SelectionManagementBase):
    """Batch listing and statistics operations."""

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
