"""FastAPI application factory for the first analysis endpoint."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from stratgauge.api.analysis import (
    AnalysisError,
    AnalysisRecord,
    AnalysisService,
    InMemoryAnalysisRepository,
)
from stratgauge.api.model_scores import (
    CandidateModelScoresView,
    ModelScoreApiError,
    ModelScoreService,
)
from stratgauge.api.portfolio import (
    ManualFillRequest,
    ManualOrderRequest,
    PortfolioApiError,
    PortfolioReviewRequest,
    PortfolioService,
)
from stratgauge.api.selections import (
    ManualSelectionRequest,
    SelectionApiError,
    SelectionBatchView,
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
    SelectionService,
    SelectionStatsView,
)
from stratgauge.api.universe import (
    UniverseSearchError,
    UniverseSearchResponse,
    UniverseSearchService,
)
from stratgauge.backtest.selection_analysis import SelectionCandidateAnalysis
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.universe import UniverseSnapshotStore
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.waizao import WaizaoAdapter
from stratgauge.domain.portfolio import ManualFill, ManualOrder, PortfolioPosition, PortfolioReview
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionBatch,
    SelectionFormula,
    SelectionLifecycleStatus,
    SelectionMaturityTask,
)
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.shadow import ShadowScoreStore


def create_app(
    records: Mapping[tuple[str, date], AnalysisRecord] | None = None,
    *,
    selection_store: SelectionImportStore | None = None,
    selection_root: str | Path = Path("data/selection"),
    market_root: str | Path = Path("data/selection-market"),
    market_adapter_factory: Callable[[str], MarketDataAdapter] | None = None,
    portfolio_store: ManualPortfolioStore | None = None,
    portfolio_root: str | Path = Path("data/portfolio"),
    universe_store: UniverseSnapshotStore | None = None,
    universe_root: str | Path = Path("data"),
    model_root: str | Path = Path("data"),
) -> FastAPI:
    service = AnalysisService(InMemoryAnalysisRepository(records or {}))
    selection_store_instance = selection_store or SelectionImportStore(selection_root)
    selections = SelectionService(selection_store_instance)
    portfolio = PortfolioService(portfolio_store or ManualPortfolioStore(portfolio_root))
    universe = UniverseSearchService(universe_store or UniverseSnapshotStore(universe_root))
    model_scores = ModelScoreService(
        selection_store_instance,
        model_registry=ModelRegistryStore(model_root),
        score_store=ShadowScoreStore(model_root),
    )
    create_market_adapter = market_adapter_factory or _create_market_adapter
    app = FastAPI(title="StratGauge API", version="0.1.0")

    @app.exception_handler(AnalysisError)
    async def handle_analysis_error(_: Any, error: AnalysisError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message, "details": {}}},
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

    @app.exception_handler(PortfolioApiError)
    async def handle_portfolio_error(_: Any, error: PortfolioApiError) -> JSONResponse:
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

    @app.get("/v1/universe/securities", response_model=UniverseSearchResponse)
    def search_universe(
        as_of: datetime,
        query: str = "",
        limit: int = 50,
    ) -> UniverseSearchResponse:
        return universe.search(query=query, as_of=as_of, limit=limit)

    @app.get("/v1/positions", response_model=list[PortfolioPosition])
    def list_positions() -> list[PortfolioPosition]:
        return list(portfolio.positions())

    @app.post("/v1/manual-orders", response_model=ManualOrder, status_code=201)
    def create_manual_order(request: ManualOrderRequest) -> ManualOrder:
        return portfolio.create_order(request)

    @app.post("/v1/manual-fills", response_model=ManualFill, status_code=201)
    def record_manual_fill(request: ManualFillRequest) -> ManualFill:
        return portfolio.record_fill(request)

    @app.post("/v1/portfolio-reviews", response_model=PortfolioReview, status_code=201)
    def write_portfolio_review(request: PortfolioReviewRequest) -> PortfolioReview:
        return portfolio.write_review(request)

    @app.get("/v1/formulas", response_model=list[SelectionFormula])
    def list_formulas() -> list[SelectionFormula]:
        return list(selections.list_formulas())

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
            market_store=SnapshotStore(market_root),
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
            market_store=SnapshotStore(market_root),
        )

    @app.post("/v1/selection-batches/{batch_id}/labels", response_model=SelectionBatchView)
    def label_selection_candidate(
        batch_id: str,
        request: SelectionLabelRequest,
    ) -> SelectionBatchView:
        return selections.label_candidate(batch_id, request)

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
