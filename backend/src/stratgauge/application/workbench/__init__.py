"""Read-model projections for the research workbench."""

from __future__ import annotations

from stratgauge.application.workbench.service import WorkbenchService
from stratgauge.application.workbench.stock_performance import (
    StockPerformanceError,
    build_stock_performance,
)
from stratgauge.application.workbench.views import (
    MarketEvidence,
    MarketEvidenceStatus,
    WorkbenchAnalyticsView,
    WorkbenchCohortMetrics,
    WorkbenchHorizonMetric,
    WorkbenchObservationPerformance,
    WorkbenchPositionView,
    WorkbenchReadError,
    WorkbenchSignalView,
    WorkbenchStockDetail,
    WorkbenchStockPerformance,
    WorkbenchStockPerformancePoint,
    WorkbenchStrategyView,
    WorkbenchSummary,
    WorkbenchTimelineEvent,
    WorkbenchTradeView,
)

__all__ = [
    "WorkbenchReadError",
    "MarketEvidenceStatus",
    "MarketEvidence",
    "WorkbenchObservationPerformance",
    "WorkbenchSignalView",
    "WorkbenchSummary",
    "WorkbenchTimelineEvent",
    "WorkbenchStockDetail",
    "WorkbenchPositionView",
    "WorkbenchStockPerformancePoint",
    "WorkbenchStockPerformance",
    "WorkbenchTradeView",
    "StockPerformanceError",
    "build_stock_performance",
    "WorkbenchHorizonMetric",
    "WorkbenchCohortMetrics",
    "WorkbenchStrategyView",
    "WorkbenchAnalyticsView",
    "WorkbenchService",
]
