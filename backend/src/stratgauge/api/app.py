"""FastAPI application factory for the first analysis endpoint."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse, Response

from stratgauge.api.analysis import (
    AnalysisError,
    AnalysisRecord,
    AnalysisService,
    InMemoryAnalysisRepository,
)
from stratgauge.api.market_sync import (
    MarketSyncApiError,
    MarketSyncRequest,
    MarketSyncResponse,
    sync_main_board_close,
    to_market_sync_api_error,
)
from stratgauge.api.model_scores import (
    CandidateModelScoresView,
    ModelScoreApiError,
    ModelScoreService,
)
from stratgauge.api.portfolio import (
    CandidateTradeRequest,
    ConfirmedManualTradeRequest,
    ManualFillRequest,
    ManualOrderRequest,
    PortfolioApiError,
    PortfolioReviewRequest,
    PortfolioService,
)
from stratgauge.api.recommendations import (
    RecommendationApiError,
    RecommendationWorkbenchService,
)
from stratgauge.api.selections import (
    ManualSelectionRequest,
    SelectionAnnotationRequest,
    SelectionApiError,
    SelectionBatchView,
    SelectionExclusionRequest,
    SelectionFormulaCreateRequest,
    SelectionFormulaView,
    SelectionImportRequest,
    SelectionLabelRequest,
    SelectionLifecycleChangeRequest,
    SelectionManagedBatchDetailView,
    SelectionManagedBatchView,
    SelectionManagedFormulaView,
    SelectionManagementStatsView,
    SelectionMatureRequest,
    SelectionMatureView,
    SelectionObservationView,
    SelectionReusableTagCreateRequest,
    SelectionReusableTagRenameRequest,
    SelectionService,
    SelectionStatsView,
)
from stratgauge.api.universe import (
    SecurityMasterSearchResponse,
    SecurityOverrideRequest,
    UniverseSearchError,
    UniverseSearchResponse,
    UniverseSearchService,
)
from stratgauge.api.workbench import register_workbench_routes
from stratgauge.application.workbench import WorkbenchReadError, WorkbenchService
from stratgauge.backtest.recommendations import RecommendationWorkbench
from stratgauge.backtest.selection_analysis import SelectionCandidateAnalysis
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.universe import (
    SecurityMasterStore,
    UniverseOverrideStore,
    UniverseSnapshotStore,
)
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.waizao import WaizaoAdapter
from stratgauge.domain.portfolio import ManualFill, ManualOrder, PortfolioPosition, PortfolioReview
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionBatch,
    SelectionCandidateReviewState,
    SelectionFormula,
    SelectionLifecycleStatus,
    SelectionMaturityTask,
    SelectionReusableTagView,
)
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.shadow import ShadowScoreStore
from stratgauge.runtime import RuntimeConfig


def create_app(
    records: Mapping[tuple[str, date], AnalysisRecord] | None = None,
    *,
    selection_store: SelectionImportStore | None = None,
    data_root: str | Path = Path("data"),
    selection_root: str | Path | None = None,
    market_root: str | Path | None = None,
    market_adapter_factory: Callable[[str], MarketDataAdapter] | None = None,
    portfolio_store: ManualPortfolioStore | None = None,
    portfolio_root: str | Path | None = None,
    universe_store: UniverseSnapshotStore | None = None,
    universe_root: str | Path | None = None,
    model_root: str | Path | None = None,
) -> FastAPI:
    runtime = RuntimeConfig.from_environment(
        data_root,
        selection_root=selection_root,
        market_root=market_root,
        portfolio_root=portfolio_root,
        universe_root=universe_root,
        model_root=model_root,
    )
    service = AnalysisService(InMemoryAnalysisRepository(records or {}))
    selection_store_instance = selection_store or SelectionImportStore(runtime.selection_root)
    selections = SelectionService(selection_store_instance)
    portfolio_store_instance = portfolio_store or ManualPortfolioStore(runtime.portfolio_root)
    portfolio = PortfolioService(portfolio_store_instance)
    market_store_instance = SnapshotStore(runtime.market_root)
    workbench = WorkbenchService(
        selection_store_instance,
        portfolio_store_instance,
        market_store_instance,
    )
    universe = UniverseSearchService(
        universe_store or UniverseSnapshotStore(runtime.universe_root),
        UniverseOverrideStore(runtime.universe_root),
        SecurityMasterStore(runtime.universe_root),
    )
    score_store = ShadowScoreStore(runtime.model_root)
    model_scores = ModelScoreService(
        selection_store_instance,
        model_registry=ModelRegistryStore(runtime.model_root),
        score_store=score_store,
    )
    recommendations = RecommendationWorkbenchService(
        selection_store_instance,
        score_store=score_store,
        market_store=market_store_instance,
    )
    create_market_adapter = market_adapter_factory or _create_market_adapter
    app = FastAPI(title="StratGauge API", version="0.1.0")
    app.state.runtime_config = runtime

    @app.exception_handler(AnalysisError)
    async def handle_analysis_error(_: Any, error: AnalysisError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
        )

    @app.exception_handler(MarketSyncApiError)
    async def handle_market_sync_error(_: Any, error: MarketSyncApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            },
        )

    @app.exception_handler(SelectionApiError)
    async def handle_selection_error(_: Any, error: SelectionApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
        )

    @app.exception_handler(ModelScoreApiError)
    async def handle_model_score_error(_: Any, error: ModelScoreApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
        )

    @app.exception_handler(RecommendationApiError)
    async def handle_recommendation_error(_: Any, error: RecommendationApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
        )

    @app.exception_handler(PortfolioApiError)
    async def handle_portfolio_error(_: Any, error: PortfolioApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
        )

    @app.exception_handler(WorkbenchReadError)
    async def handle_workbench_error(_: Any, error: WorkbenchReadError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
        )

    @app.exception_handler(UniverseSearchError)
    async def handle_universe_error(_: Any, error: UniverseSearchError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "stratgauge-api"}

    @app.get("/v1/stocks/{symbol}/analysis", response_model=AnalysisRecord)
    def get_stock_analysis(symbol: str, as_of: date) -> AnalysisRecord:
        return service.get(symbol, as_of)

    @app.post("/v1/universe/overrides", response_model=SecurityOverrideRequest, status_code=201)
    def create_universe_override(request: SecurityOverrideRequest) -> SecurityOverrideRequest:
        return SecurityOverrideRequest.model_validate(
            universe.create_override(request).model_dump()
        )

    @app.get("/v1/universe/master", response_model=SecurityMasterSearchResponse)
    def search_security_master(
        effective_date: date,
        known_at: datetime,
        query: str = "",
        limit: int = 50,
    ) -> SecurityMasterSearchResponse:
        return universe.search_master(
            query=query,
            effective_date=effective_date,
            known_at=known_at,
            limit=limit,
        )

    @app.get("/v1/universe/securities", response_model=UniverseSearchResponse)
    def search_universe(
        as_of: datetime,
        query: str = "",
        limit: int = 50,
    ) -> UniverseSearchResponse:
        return universe.search(query=query, as_of=as_of, limit=limit)

    @app.post("/v1/market-sync/main-board", response_model=MarketSyncResponse)
    def sync_main_board_market_close(request: MarketSyncRequest) -> MarketSyncResponse:
        try:
            return sync_main_board_close(
                request,
                market_root=runtime.market_root,
                universe_root=runtime.universe_root,
                adapter=cast(TushareAdapter, create_market_adapter("tushare")),
            )
        except MarketSyncApiError:
            raise
        except Exception as exc:
            raise to_market_sync_api_error(exc) from exc

    workbench_router = APIRouter(prefix="/v1/workbench", tags=["workbench"])
    register_workbench_routes(workbench_router, workbench)
    app.include_router(workbench_router)

    @app.get("/v1/positions", response_model=list[PortfolioPosition])
    def list_positions() -> list[PortfolioPosition]:
        return list(portfolio.positions())

    @app.get("/v1/manual-orders", response_model=list[ManualOrder])
    def list_manual_orders() -> list[ManualOrder]:
        return list(portfolio.orders())

    @app.get("/v1/manual-fills", response_model=list[ManualFill])
    def list_manual_fills() -> list[ManualFill]:
        return list(portfolio.fills())

    @app.get("/v1/portfolio-reviews", response_model=list[PortfolioReview])
    def list_portfolio_reviews() -> list[PortfolioReview]:
        return list(portfolio.reviews())

    @app.post("/v1/manual-orders", response_model=ManualOrder, status_code=201)
    def create_manual_order(request: ManualOrderRequest) -> ManualOrder:
        return portfolio.create_order(request)

    @app.post(
        "/v1/manual-trades/confirm",
        response_model=tuple[ManualOrder, ManualFill],
        status_code=201,
    )
    def confirm_manual_trade(
        request: ConfirmedManualTradeRequest,
    ) -> tuple[ManualOrder, ManualFill]:
        return portfolio.confirm_trade(request)

    @app.post("/v1/manual-fills", response_model=ManualFill, status_code=201)
    def record_manual_fill(request: ManualFillRequest) -> ManualFill:
        return portfolio.record_fill(request)

    @app.post(
        "/v1/selection-candidate-trades",
        response_model=tuple[ManualOrder, ManualFill],
        status_code=201,
    )
    def record_selection_candidate_trade(
        request: CandidateTradeRequest,
    ) -> tuple[ManualOrder, ManualFill]:
        if request.filled_at < request.ordered_at:
            raise PortfolioApiError(
                "PORTFOLIO_TRADE_REJECTED",
                "成交时间不能早于下单时间。",
                422,
            )
        candidate = selections.get_candidate(request.selection_candidate_id)
        if candidate.local_code != request.local_code or candidate.exchange != request.exchange:
            raise PortfolioApiError(
                "PORTFOLIO_TRADE_REJECTED",
                "实际买入的证券必须与候选证券一致。",
                422,
            )
        return portfolio.record_candidate_trade(request)

    @app.post("/v1/portfolio-reviews", response_model=PortfolioReview, status_code=201)
    def write_portfolio_review(request: PortfolioReviewRequest) -> PortfolioReview:
        return portfolio.write_review(request)

    @app.get("/v1/formulas", response_model=list[SelectionFormula])
    def list_formulas() -> list[SelectionFormula]:
        return list(selections.list_formulas())

    @app.get("/v1/annotation-tags", response_model=list[SelectionReusableTagView])
    def list_annotation_tags() -> list[SelectionReusableTagView]:
        return list(selections.list_reusable_tags())

    @app.post("/v1/annotation-tags", response_model=SelectionReusableTagView, status_code=201)
    def create_annotation_tag(
        request: SelectionReusableTagCreateRequest,
    ) -> SelectionReusableTagView:
        return selections.create_reusable_tag(request)

    @app.get(
        "/v1/data-management/annotation-tags",
        response_model=list[SelectionReusableTagView],
    )
    def list_managed_annotation_tags() -> list[SelectionReusableTagView]:
        return list(selections.list_managed_reusable_tags())

    @app.post(
        "/v1/data-management/annotation-tags/{tag_id}/rename",
        response_model=SelectionReusableTagView,
    )
    def rename_annotation_tag(
        tag_id: str,
        request: SelectionReusableTagRenameRequest,
    ) -> SelectionReusableTagView:
        return selections.rename_reusable_tag(tag_id, request)

    @app.post(
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

    @app.post(
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

    @app.post("/v1/formulas", response_model=SelectionFormulaView, status_code=201)
    def create_formula(request: SelectionFormulaCreateRequest) -> SelectionFormulaView:
        return selections.create_formula(request)

    @app.get(
        "/v1/data-management/formulas",
        response_model=list[SelectionManagedFormulaView],
    )
    def list_managed_formulas() -> list[SelectionManagedFormulaView]:
        return list(selections.list_managed_formulas())

    @app.post(
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

    @app.post(
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

    @app.get("/v1/selection-batches", response_model=list[SelectionBatch])
    def list_selection_batches() -> list[SelectionBatch]:
        return list(selections.list_batches())

    @app.get(
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

    @app.post("/v1/selection-batches", response_model=SelectionBatchView, status_code=201)
    def import_selection_batch(request: SelectionImportRequest) -> SelectionBatchView:
        return selections.import_batch(request)

    @app.post(
        "/v1/selection-batches/manual",
        response_model=SelectionBatchView,
        status_code=201,
    )
    def create_manual_selection_batch(request: ManualSelectionRequest) -> SelectionBatchView:
        return selections.create_manual_batch(request)

    @app.post(
        "/v1/selection-batches/{batch_id}/maturity-tasks",
        response_model=SelectionMaturityTask,
        status_code=201,
    )
    def create_maturity_task(
        batch_id: str,
        request: SelectionMatureRequest,
    ) -> SelectionMaturityTask:
        return selections.create_maturity_task(batch_id, request)

    @app.get(
        "/v1/selection-batches/{batch_id}/maturity-tasks",
        response_model=list[SelectionMaturityTask],
    )
    def list_maturity_tasks(batch_id: str) -> list[SelectionMaturityTask]:
        return list(selections.list_maturity_tasks(batch_id))

    @app.post(
        "/v1/maturity-tasks/{task_id}/run",
        response_model=SelectionMaturityTask,
    )
    def run_maturity_task(task_id: str) -> SelectionMaturityTask:
        task = selections.get_maturity_task(task_id)
        return selections.run_maturity_task(
            task_id,
            adapter=create_market_adapter(task.provider),
            market_store=SnapshotStore(runtime.market_root),
        )

    @app.get(
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

    @app.get(
        "/v1/data-management/batches",
        response_model=list[SelectionManagedBatchView],
    )
    def list_managed_batches() -> list[SelectionManagedBatchView]:
        return list(selections.list_managed_batches())

    @app.get(
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

    @app.get(
        "/v1/data-management/batches/{batch_id}/dataset",
        response_class=Response,
    )
    def download_managed_batch_dataset(batch_id: str) -> Response:
        content = selections.export_dataset(batch_id)
        return Response(
            content=content,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": (f"attachment; filename=selection-{quote(batch_id)}.jsonl")
            },
        )

    @app.get(
        "/v1/data-management/batches/{batch_id}/annotation-dataset",
        response_class=Response,
    )
    def download_managed_batch_annotation_dataset(batch_id: str) -> Response:
        content = selections.export_annotation_dataset(batch_id)
        return Response(
            content=content,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=selection-annotations-{quote(batch_id)}.jsonl"
                )
            },
        )

    @app.get(
        "/v1/data-management/batches/{batch_id}",
        response_model=SelectionManagedBatchDetailView,
    )
    def get_managed_batch(batch_id: str) -> SelectionManagedBatchDetailView:
        return selections.get_managed_batch(batch_id)

    @app.post(
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

    @app.post(
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

    @app.get("/v1/selection-batches/{batch_id}", response_model=SelectionBatchView)
    def get_selection_batch(batch_id: str) -> SelectionBatchView:
        return selections.get_batch(batch_id)

    @app.get("/v1/selection-batches/{batch_id}/stats", response_model=SelectionStatsView)
    def summarize_selection_batch(batch_id: str) -> SelectionStatsView:
        return selections.summarize_batch(batch_id)

    @app.get(
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

    @app.get(
        "/v1/selection-batches/{batch_id}/candidates/{candidate_id}/model-scores",
        response_model=CandidateModelScoresView,
    )
    def get_candidate_model_scores(
        batch_id: str,
        candidate_id: str,
    ) -> CandidateModelScoresView:
        return model_scores.get_candidate_scores(batch_id, candidate_id)

    @app.get(
        "/v1/selection-batches/{batch_id}/recommendation-workbench",
        response_model=RecommendationWorkbench,
    )
    def get_recommendation_workbench(batch_id: str) -> RecommendationWorkbench:
        return recommendations.get(batch_id)

    @app.post(
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
            adapter=create_market_adapter(request.provider),
            market_store=SnapshotStore(runtime.market_root),
        )

    @app.post("/v1/selection-batches/{batch_id}/labels", response_model=SelectionBatchView)
    def label_selection_candidate(
        batch_id: str,
        request: SelectionLabelRequest,
    ) -> SelectionBatchView:
        return selections.label_candidate(batch_id, request)

    @app.post(
        "/v1/selection-batches/{batch_id}/annotations",
        response_model=SelectionBatchView,
    )
    def annotate_selection_candidate(
        batch_id: str,
        request: SelectionAnnotationRequest,
    ) -> SelectionBatchView:
        return selections.annotate_candidate(batch_id, request)

    @app.post(
        "/v1/selection-batches/{batch_id}/exclusions",
        response_model=SelectionBatchView,
    )
    def exclude_selection_candidate(
        batch_id: str,
        request: SelectionExclusionRequest,
    ) -> SelectionBatchView:
        return selections.exclude_candidate(batch_id, request)

    return app


def _create_market_adapter(provider: str) -> MarketDataAdapter:
    adapters: dict[str, type[MarketDataAdapter]] = {
        "tushare": TushareAdapter,
        "akshare": AKShareAdapter,
        "baostock": BaoStockAdapter,
        "waizao": WaizaoAdapter,
    }
    return adapters[provider]()


app = create_app()
