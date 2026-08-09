"""FastAPI application factory for the first analysis endpoint."""

from __future__ import annotations

from collections.abc import Mapping
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
from stratgauge.api.selections import (
    SelectionApiError,
    SelectionBatchView,
    SelectionImportRequest,
    SelectionLabelRequest,
    SelectionService,
    SelectionStatsView,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.domain.selections import SelectionBatch


def create_app(
    records: Mapping[tuple[str, date], AnalysisRecord] | None = None,
    *,
    selection_store: SelectionImportStore | None = None,
    selection_root: str | Path = Path("data/selection"),
) -> FastAPI:
    service = AnalysisService(InMemoryAnalysisRepository(records or {}))
    selections = SelectionService(selection_store or SelectionImportStore(selection_root))
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

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "stratgauge-api"}

    @app.get("/v1/stocks/{symbol}/analysis", response_model=AnalysisRecord)
    def get_stock_analysis(symbol: str, as_of: date) -> AnalysisRecord:
        return service.get(symbol, as_of)

    @app.get("/v1/selection-batches", response_model=list[SelectionBatch])
    def list_selection_batches() -> list[SelectionBatch]:
        return list(selections.list_batches())

    @app.post("/v1/selection-batches", response_model=SelectionBatchView, status_code=201)
    def import_selection_batch(request: SelectionImportRequest) -> SelectionBatchView:
        return selections.import_batch(request)

    @app.get("/v1/selection-batches/{batch_id}", response_model=SelectionBatchView)
    def get_selection_batch(batch_id: str) -> SelectionBatchView:
        return selections.get_batch(batch_id)

    @app.get("/v1/selection-batches/{batch_id}/stats", response_model=SelectionStatsView)
    def summarize_selection_batch(batch_id: str) -> SelectionStatsView:
        return selections.summarize_batch(batch_id)

    @app.post("/v1/selection-batches/{batch_id}/labels", response_model=SelectionBatchView)
    def label_selection_candidate(
        batch_id: str,
        request: SelectionLabelRequest,
    ) -> SelectionBatchView:
        return selections.label_candidate(batch_id, request)

    return app


app = create_app()
