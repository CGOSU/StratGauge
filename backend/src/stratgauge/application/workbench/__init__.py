"""Read-model projections for the research workbench."""

from __future__ import annotations

from stratgauge.application.workbench.service import WorkbenchService
from stratgauge.application.workbench.views import (
    MarketEvidence,
    MarketEvidenceStatus,
    WorkbenchAnalyticsView,
    WorkbenchCohortMetrics,
    WorkbenchHorizonMetric,
    WorkbenchPositionView,
    WorkbenchReadError,
    WorkbenchSignalView,
    WorkbenchStockDetail,
    WorkbenchStrategyView,
    WorkbenchSummary,
    WorkbenchTimelineEvent,
    WorkbenchTradeView,
)

__all__ = [
    "WorkbenchReadError",
    "MarketEvidenceStatus",
    "MarketEvidence",
    "WorkbenchSignalView",
    "WorkbenchSummary",
    "WorkbenchTimelineEvent",
    "WorkbenchStockDetail",
    "WorkbenchPositionView",
    "WorkbenchTradeView",
    "WorkbenchHorizonMetric",
    "WorkbenchCohortMetrics",
    "WorkbenchStrategyView",
    "WorkbenchAnalyticsView",
    "WorkbenchService",
]
