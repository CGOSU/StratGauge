"""FastAPI application factory for the first analysis endpoint."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from stratgauge.api.analysis import (
    AnalysisError,
    AnalysisRecord,
    AnalysisService,
    InMemoryAnalysisRepository,
)
from stratgauge.api.market_sync import (
    MarketSyncApiError,
    create_market_sync_router,
)
from stratgauge.api.ml_readiness import MLReadinessApiError
from stratgauge.api.model_scores import (
    CandidateModelScoresView,
    ModelScoreApiError,
    ModelScoreService,
)
from stratgauge.api.portfolio import (
    PortfolioApiError,
    PortfolioService,
    create_portfolio_router,
)
from stratgauge.api.recommendations import (
    RecommendationApiError,
    RecommendationWorkbenchService,
)
from stratgauge.api.selections import (
    SelectionApiError,
    SelectionService,
    create_selections_router,
)
from stratgauge.api.universe import (
    UniverseSearchError,
    UniverseSearchService,
    create_universe_router,
)
from stratgauge.api.workbench import create_workbench_router
from stratgauge.application.job_runner import LocalJobStore
from stratgauge.application.ml_readiness import (
    MLReadinessError,
    MLReadinessReport,
    MLReadinessService,
)
from stratgauge.application.workbench import (
    WorkbenchReadError,
    WorkbenchService,
)
from stratgauge.backtest.recommendations import RecommendationWorkbench
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
    ml_readiness = MLReadinessService(
        selection_store_instance,
        data_root=runtime.data_root,
        market_root=runtime.market_root,
        model_root=runtime.model_root,
    )
    recommendations = RecommendationWorkbenchService(
        selection_store_instance,
        score_store=score_store,
        market_store=market_store_instance,
    )
    create_market_adapter = market_adapter_factory or _create_market_adapter
    app = FastAPI(title="StratGauge API", version="0.1.0")
    app.state.runtime_config = runtime
    market_sync_job_store = LocalJobStore(runtime.data_root)
    app.state.market_sync_job_store = market_sync_job_store

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

    @app.exception_handler(MLReadinessApiError)
    async def handle_ml_readiness_error(_: Any, error: MLReadinessApiError) -> JSONResponse:
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

    @app.get(
        "/v1/selection-batches/{batch_id}/candidates/{candidate_id}/model-scores",
        response_model=CandidateModelScoresView,
    )
    def get_candidate_model_scores(
        batch_id: str,
        candidate_id: str,
    ) -> CandidateModelScoresView:
        return model_scores.get_candidate_scores(batch_id, candidate_id)

    @app.get("/v1/ml/readiness", response_model=MLReadinessReport)
    def get_ml_readiness() -> MLReadinessReport:
        try:
            return ml_readiness.get_readiness()
        except MLReadinessError as exc:
            raise MLReadinessApiError("ML_READINESS_STORAGE_ERROR", str(exc), 500) from exc

    @app.get(
        "/v1/selection-batches/{batch_id}/recommendation-workbench",
        response_model=RecommendationWorkbench,
    )
    def get_recommendation_workbench(batch_id: str) -> RecommendationWorkbench:
        return recommendations.get(batch_id)

    workbench = WorkbenchService(
        selection_store_instance,
        portfolio_store_instance,
        market_store_instance,
    )
    app.include_router(
        create_market_sync_router(
            market_root=runtime.market_root,
            universe_root=runtime.universe_root,
            job_store=market_sync_job_store,
            adapter_factory=create_market_adapter,
        )
    )
    app.include_router(create_workbench_router(workbench))
    app.include_router(create_portfolio_router(portfolio, selections))
    app.include_router(
        create_selections_router(
            selections,
            adapter_factory=create_market_adapter,
            market_root=runtime.market_root,
        )
    )
    app.include_router(create_universe_router(universe))

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
