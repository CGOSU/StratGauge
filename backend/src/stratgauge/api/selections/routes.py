"""HTTP routes for selection batches, annotations, maturity, and data management."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response, StreamingResponse

from stratgauge.api.selections.requests import (
    ManualSelectionRequest,
    SelectionAnnotationRequest,
    SelectionExclusionRequest,
    SelectionFormulaCreateRequest,
    SelectionImportRequest,
    SelectionLabelRequest,
    SelectionLifecycleChangeRequest,
    SelectionMatureRequest,
    SelectionReusableTagCreateRequest,
    SelectionReusableTagRenameRequest,
)
from stratgauge.api.selections.service import SelectionService
from stratgauge.api.selections.views import (
    SelectionBatchPage,
    SelectionBatchView,
    SelectionFormulaView,
    SelectionManagedBatchDetailView,
    SelectionManagedBatchPage,
    SelectionManagedBatchView,
    SelectionManagedFormulaView,
    SelectionManagementStatsView,
    SelectionMatureView,
    SelectionMaturityStatus,
    SelectionMaturityStatusPage,
    SelectionObservationPage,
    SelectionObservationView,
    SelectionStatsView,
)
from stratgauge.backtest.selection_analysis import SelectionCandidateAnalysis
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionBatch,
    SelectionCandidateReviewState,
    SelectionFormula,
    SelectionLifecycleStatus,
    SelectionMaturityTask,
    SelectionReusableTagView,
)


def create_selections_router(
    selections: SelectionService,
    *,
    adapter_factory: Callable[[str], MarketDataAdapter],
    market_root: str | Path,
) -> APIRouter:
    """FastAPI router for selection research and data-management endpoints."""
    router = APIRouter(tags=["selections"])

    @router.get("/v1/formulas", response_model=list[SelectionFormula])
    def list_formulas() -> list[SelectionFormula]:
        return list(selections.list_formulas())

    @router.get("/v1/annotation-tags", response_model=list[SelectionReusableTagView])
    def list_annotation_tags() -> list[SelectionReusableTagView]:
        return list(selections.list_reusable_tags())

    @router.post("/v1/annotation-tags", response_model=SelectionReusableTagView, status_code=201)
    def create_annotation_tag(
        request: SelectionReusableTagCreateRequest,
    ) -> SelectionReusableTagView:
        return selections.create_reusable_tag(request)

    @router.get(
        "/v1/data-management/annotation-tags",
        response_model=list[SelectionReusableTagView],
    )
    def list_managed_annotation_tags() -> list[SelectionReusableTagView]:
        return list(selections.list_managed_reusable_tags())

    @router.post(
        "/v1/data-management/annotation-tags/{tag_id}/rename",
        response_model=SelectionReusableTagView,
    )
    def rename_annotation_tag(
        tag_id: str,
        request: SelectionReusableTagRenameRequest,
    ) -> SelectionReusableTagView:
        return selections.rename_reusable_tag(tag_id, request)

    @router.post(
        "/v1/data-management/annotation-tags/{tag_id}/archive",
        response_model=SelectionReusableTagView,
    )
    def archive_annotation_tag(
        tag_id: str,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionReusableTagView:
        return selections.set_reusable_tag_status(
            tag_id,
            SelectionLifecycleStatus.ARCHIVED,
            request,
        )

    @router.post(
        "/v1/data-management/annotation-tags/{tag_id}/restore",
        response_model=SelectionReusableTagView,
    )
    def restore_annotation_tag(
        tag_id: str,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionReusableTagView:
        return selections.set_reusable_tag_status(
            tag_id,
            SelectionLifecycleStatus.ACTIVE,
            request,
        )

    @router.post("/v1/formulas", response_model=SelectionFormulaView, status_code=201)
    def create_formula(request: SelectionFormulaCreateRequest) -> SelectionFormulaView:
        return selections.create_formula(request)

    @router.get(
        "/v1/data-management/formulas",
        response_model=list[SelectionManagedFormulaView],
    )
    def list_managed_formulas() -> list[SelectionManagedFormulaView]:
        return list(selections.list_managed_formulas())

    @router.post(
        "/v1/data-management/formulas/{formula_id}/archive",
        response_model=SelectionManagedFormulaView,
    )
    def archive_formula(
        formula_id: str,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedFormulaView:
        return selections.set_formula_status(
            formula_id,
            SelectionLifecycleStatus.ARCHIVED,
            request,
        )

    @router.post(
        "/v1/data-management/formulas/{formula_id}/restore",
        response_model=SelectionManagedFormulaView,
    )
    def restore_formula(
        formula_id: str,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedFormulaView:
        return selections.set_formula_status(
            formula_id,
            SelectionLifecycleStatus.ACTIVE,
            request,
        )

    @router.get("/v1/selection-batches", response_model=list[SelectionBatch])
    def list_selection_batches() -> list[SelectionBatch]:
        return list(selections.list_batches())

    @router.get("/v1/selection-batches/page", response_model=SelectionBatchPage)
    def list_selection_batches_page(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> SelectionBatchPage:
        return selections.list_batches_page(page=page, page_size=page_size)

    @router.get(
        "/v1/selection-observation-pool",
        response_model=list[SelectionObservationView],
    )
    def list_selection_observation_pool(
        review_state: SelectionCandidateReviewState | None = None,
        batch_id: str | None = None,
        include_excluded: bool = False,
    ) -> list[SelectionObservationView]:
        return list(
            selections.list_observation_pool(
                review_state=review_state,
                batch_id=batch_id,
                include_excluded=include_excluded,
            )
        )

    @router.get("/v1/selection-observation-pool/page", response_model=SelectionObservationPage)
    def list_selection_observation_pool_page(
        review_state: SelectionCandidateReviewState | None = None,
        batch_id: str | None = None,
        include_excluded: bool = False,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> SelectionObservationPage:
        return selections.list_observation_pool_page(
            review_state=review_state,
            batch_id=batch_id,
            include_excluded=include_excluded,
            page=page,
            page_size=page_size,
        )

    @router.get(
        "/v1/selection-maturity/page",
        response_model=SelectionMaturityStatusPage,
    )
    def list_selection_maturity_status_page(
        status: SelectionMaturityStatus | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> SelectionMaturityStatusPage:
        return selections.list_maturity_status_page(
            status=status,
            page=page,
            page_size=page_size,
        )

    @router.post("/v1/selection-batches", response_model=SelectionBatchView, status_code=201)
    def import_selection_batch(request: SelectionImportRequest) -> SelectionBatchView:
        return selections.import_batch(request)

    @router.post(
        "/v1/selection-batches/manual",
        response_model=SelectionBatchView,
        status_code=201,
    )
    def create_manual_selection_batch(request: ManualSelectionRequest) -> SelectionBatchView:
        return selections.create_manual_batch(request)

    @router.post(
        "/v1/selection-batches/{batch_id}/maturity-tasks",
        response_model=SelectionMaturityTask,
        status_code=201,
    )
    def create_maturity_task(
        batch_id: str,
        request: SelectionMatureRequest,
    ) -> SelectionMaturityTask:
        return selections.create_maturity_task(batch_id, request)

    @router.get(
        "/v1/selection-batches/{batch_id}/maturity-tasks",
        response_model=list[SelectionMaturityTask],
    )
    def list_maturity_tasks(batch_id: str) -> list[SelectionMaturityTask]:
        return list(selections.list_maturity_tasks(batch_id))

    @router.post(
        "/v1/maturity-tasks/{task_id}/run",
        response_model=SelectionMaturityTask,
    )
    def run_maturity_task(task_id: str) -> SelectionMaturityTask:
        task = selections.get_maturity_task(task_id)
        return selections.run_maturity_task(
            task_id,
            adapter=adapter_factory(task.provider),
            market_store=SnapshotStore(market_root),
        )

    @router.get(
        "/v1/data-management/stats",
        response_model=SelectionManagementStatsView,
    )
    def summarize_managed_batches(
        formula_version: str | None = None,
        source: Literal["tongdaxin", "manual"] | None = None,
        selection_date_from: date | None = None,
        selection_date_to: date | None = None,
        manual_decision: ManualSelectionDecision | None = None,
        tracking_policy_id: str | None = None,
        lifecycle: Literal["all", "active", "archived"] = "all",
    ) -> SelectionManagementStatsView:
        return selections.summarize_managed_batches(
            formula_version=formula_version,
            source=source,
            selection_date_from=selection_date_from,
            selection_date_to=selection_date_to,
            manual_decision=manual_decision,
            tracking_policy_id=tracking_policy_id,
            lifecycle=lifecycle,
        )

    @router.get(
        "/v1/data-management/batches",
        response_model=list[SelectionManagedBatchView],
    )
    def list_managed_batches() -> list[SelectionManagedBatchView]:
        return list(selections.list_managed_batches())

    @router.get(
        "/v1/data-management/batches/page",
        response_model=SelectionManagedBatchPage,
    )
    def list_managed_batches_page(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> SelectionManagedBatchPage:
        return selections.list_managed_batches_page(page=page, page_size=page_size)

    @router.get(
        "/v1/data-management/batches/{batch_id}/raw",
        response_class=Response,
    )
    def download_managed_batch_raw(batch_id: str) -> Response:
        content, filename = selections.load_raw_batch(batch_id)
        encoded_filename = quote(filename)
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
        )

    @router.get(
        "/v1/data-management/batches/{batch_id}/dataset",
        response_class=StreamingResponse,
    )
    def download_managed_batch_dataset(batch_id: str) -> StreamingResponse:
        return StreamingResponse(
            selections.stream_dataset(batch_id),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": (f"attachment; filename=selection-{quote(batch_id)}.jsonl")
            },
        )

    @router.get(
        "/v1/data-management/batches/{batch_id}/annotation-dataset",
        response_class=StreamingResponse,
    )
    def download_managed_batch_annotation_dataset(batch_id: str) -> StreamingResponse:
        return StreamingResponse(
            selections.stream_annotation_dataset(batch_id),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=selection-annotations-{quote(batch_id)}.jsonl"
                )
            },
        )

    @router.get(
        "/v1/data-management/batches/{batch_id}",
        response_model=SelectionManagedBatchDetailView,
    )
    def get_managed_batch(batch_id: str) -> SelectionManagedBatchDetailView:
        return selections.get_managed_batch(batch_id)

    @router.post(
        "/v1/data-management/batches/{batch_id}/archive",
        response_model=SelectionManagedBatchDetailView,
    )
    def archive_batch(
        batch_id: str,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedBatchDetailView:
        return selections.set_batch_status(
            batch_id,
            SelectionLifecycleStatus.ARCHIVED,
            request,
        )

    @router.post(
        "/v1/data-management/batches/{batch_id}/restore",
        response_model=SelectionManagedBatchDetailView,
    )
    def restore_batch(
        batch_id: str,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedBatchDetailView:
        return selections.set_batch_status(
            batch_id,
            SelectionLifecycleStatus.ACTIVE,
            request,
        )

    @router.get("/v1/selection-batches/{batch_id}", response_model=SelectionBatchView)
    def get_selection_batch(batch_id: str) -> SelectionBatchView:
        return selections.get_batch(batch_id)

    @router.get("/v1/selection-batches/{batch_id}/stats", response_model=SelectionStatsView)
    def summarize_selection_batch(batch_id: str) -> SelectionStatsView:
        return selections.summarize_batch(batch_id)

    @router.get(
        "/v1/selection-batches/{batch_id}/candidates/{candidate_id}/analysis",
        response_model=SelectionCandidateAnalysis,
    )
    def analyze_selection_candidate(
        batch_id: str,
        candidate_id: str,
        tracking_policy_id: str | None = None,
    ) -> SelectionCandidateAnalysis:
        return selections.analyze_candidate(
            batch_id,
            candidate_id,
            tracking_policy_id=tracking_policy_id,
        )

    @router.post(
        "/v1/selection-batches/{batch_id}/mature",
        response_model=SelectionMatureView,
    )
    def mature_selection_batch(
        batch_id: str,
        request: SelectionMatureRequest,
    ) -> SelectionMatureView:
        return selections.mature_batch(
            batch_id,
            request,
            adapter=adapter_factory(request.provider),
            market_store=SnapshotStore(market_root),
        )

    @router.post("/v1/selection-batches/{batch_id}/labels", response_model=SelectionBatchView)
    def label_selection_candidate(
        batch_id: str,
        request: SelectionLabelRequest,
    ) -> SelectionBatchView:
        return selections.label_candidate(batch_id, request)

    @router.post(
        "/v1/selection-batches/{batch_id}/annotations",
        response_model=SelectionBatchView,
    )
    def annotate_selection_candidate(
        batch_id: str,
        request: SelectionAnnotationRequest,
    ) -> SelectionBatchView:
        return selections.annotate_candidate(batch_id, request)

    @router.post(
        "/v1/selection-batches/{batch_id}/exclusions",
        response_model=SelectionBatchView,
    )
    def exclude_selection_candidate(
        batch_id: str,
        request: SelectionExclusionRequest,
    ) -> SelectionBatchView:
        return selections.exclude_candidate(batch_id, request)

    return router
