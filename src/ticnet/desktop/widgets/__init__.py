"""Custom desktop UI widgets for TicNet presentation layer."""

from __future__ import annotations

from ticnet.desktop.widgets.chart_widget import (
    LatencyTimelineChart,
    LoadedLatencyBarChart,
    MeasurementChartsContainer,
)
from ticnet.desktop.widgets.diagnosis_summary_widget import DiagnosisSummaryPanel
from ticnet.desktop.widgets.diagnostic_timeline_widget import DiagnosticWorkflowTimeline
from ticnet.desktop.widgets.evidence_matrix_widget import StructuredEvidenceMatrix
from ticnet.desktop.widgets.recommendations_widget import RecommendationsPanel
from ticnet.desktop.widgets.target_trace_widget import (
    LossBarWidget,
    TargetTraceTableWidget,
)

from ticnet.desktop.widgets.diagnostic_activity_widget import (
    ActivityCategory,
    ActivityEvent,
    ActivityItemWidget,
    DiagnosticActivityFeedWidget,
)
from ticnet.desktop.widgets.historical_detail_widget import HistoricalDetailPanel
from ticnet.desktop.widgets.historical_trends_widget import HistoricalTrendsWidget
from ticnet.desktop.widgets.radial_gauge_widget import GaugeState, RadialGaugeWidget
from ticnet.desktop.widgets.run_id_badge import RunIdBadge
from ticnet.desktop.widgets.stage_card_widget import (
    DiagnosticStageCard,
    Stage1Data,
    Stage2Data,
    Stage3Data,
    Stage4Data,
    Stage5Data,
    StageCardState,
    StageMeasurementExtractor,
)
from ticnet.desktop.widgets.interactive_globe_widget import InteractiveGlobeWidget
from ticnet.desktop.widgets.global_ping_graph_widget import GlobalPingGraphWidget
from ticnet.desktop.widgets.network_analytics_widget import NetworkAnalyticsWidget
from ticnet.desktop.widgets.time_series_line_graph_widget import (
    TimeSeriesLineGraphWidget,
    TimeSeriesPoint,
)
from ticnet.desktop.widgets.incident_history_widget import IncidentHistoryWidget

__all__ = [
    "NetworkAnalyticsWidget",
    "TimeSeriesLineGraphWidget",
    "TimeSeriesPoint",
    "IncidentHistoryWidget",
    "InteractiveGlobeWidget",
    "GlobalPingGraphWidget",
    "LatencyTimelineChart",
    "LoadedLatencyBarChart",
    "MeasurementChartsContainer",
    "TargetTraceTableWidget",
    "LossBarWidget",
    "DiagnosisSummaryPanel",
    "DiagnosticWorkflowTimeline",
    "StructuredEvidenceMatrix",
    "RecommendationsPanel",
    "HistoricalTrendsWidget",
    "HistoricalDetailPanel",
    "RunIdBadge",
    "RadialGaugeWidget",
    "GaugeState",
    "DiagnosticActivityFeedWidget",
    "ActivityItemWidget",
    "ActivityCategory",
    "ActivityEvent",
    "DiagnosticStageCard",
    "StageCardState",
    "StageMeasurementExtractor",
    "Stage1Data",
    "Stage2Data",
    "Stage3Data",
    "Stage4Data",
    "Stage5Data",
]

