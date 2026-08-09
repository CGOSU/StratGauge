"""FastAPI application factory for the first analysis endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from stratgauge.api.analysis import (
    AnalysisError,
    AnalysisRecord,
    AnalysisService,
    InMemoryAnalysisRepository,
)


def create_app(
    records: Mapping[tuple[str, date], AnalysisRecord] | None = None,
) -> FastAPI:
    service = AnalysisService(InMemoryAnalysisRepository(records or {}))
    app = FastAPI(title="StratGauge API", version="0.1.0")

    @app.exception_handler(AnalysisError)
    async def handle_analysis_error(_: Any, error: AnalysisError) -> JSONResponse:
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

    return app


app = create_app()
