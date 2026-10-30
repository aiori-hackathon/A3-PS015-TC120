"""Historical trend visualization widget using PySide6.QtCharts.

Provides longitudinal telemetry across diagnostic sessions adhering to Stage 5 rules:
- Reads solely from persisted SQLite session records and summaries.
- Never interpolates missing values or substitutes zero for missing telemetry.
- Compares Gateway latency vs External multi-target latency over time.
- Visualizes probe packet loss percentage across sessions.
- Renders an honest, informative empty state when fewer than 2 completed sessions exist.
"""

from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtCharts import (
        QCategoryAxis,
        QChart,
        QChartView,
        QLineSeries,
        QScatterSeries,
        QValueAxis,
    )
    QT_CHARTS_AVAILABLE = True
except ImportError:
    QT_CHARTS_AVAILABLE = False
    QCategoryAxis: Any = None
    QChart: Any = None
    QChartView: Any = None
    QLineSeries: Any = None
    QScatterSeries: Any = None
    QValueAxis: Any = None

from ticnet.desktop.services.history_service import HistoricalSessionSummary
from ticnet.desktop.theme import ThemeTokens


class HistoricalTrendsWidget(QFrame):
    """Widget displaying longitudinal latency and packet-loss trends across diagnostic sessions."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("historicalTrendsWidget")
        self.setStyleSheet(
            f"#historicalTrendsWidget {{ "
            f"  background-color: {ThemeTokens.BG_CARD}; "
            f"  border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"  border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
            f"  padding: 16px; "
            f"}}"
        )

        self._sessions: List[HistoricalSessionSummary] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(8)

        # Header Title & Description
        header_row = QWidget(self)
        h_layout = QHBoxLayout(header_row)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(10)

        title_col = QWidget(header_row)
        tc_layout = QVBoxLayout(title_col)
        tc_layout.setContentsMargins(0, 0, 0, 0)
        tc_layout.setSpacing(2)

        lbl_title = QLabel("Historical Performance Trends (Cross-Run Telemetry)", title_col)
        lbl_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY};")
        lbl_sub = QLabel(
            "Longitudinal analysis of gateway round-trip time, external probe latency, and packet loss across diagnostic runs.",
            title_col,
        )
        lbl_sub.setStyleSheet(f"font-size: 12px; color: {ThemeTokens.TEXT_SECONDARY};")
        lbl_sub.setWordWrap(True)

        tc_layout.addWidget(lbl_title)
        tc_layout.addWidget(lbl_sub)
        h_layout.addWidget(title_col)
        h_layout.addStretch()

        # Dynamic Live Status Indicator
        self.live_badge = QLabel("● LIVE TELEMETRY", header_row)
        self.live_badge.setStyleSheet(
            f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; "
            f"color: {ThemeTokens.COLOR_SUCCESS}; "
            f"font-size: 11px; font-weight: 700; "
            f"padding: 3px 10px; border-radius: {ThemeTokens.RADIUS_PILL}; "
            f"border: 1px solid {ThemeTokens.BORDER_SUCCESS};"
        )
        h_layout.addWidget(self.live_badge)

        root_layout.addWidget(header_row)

        # KPI Summary Cards Row
        self.kpi_row = QWidget(self)
        kpi_layout = QHBoxLayout(self.kpi_row)
        kpi_layout.setContentsMargins(0, 0, 0, 0)
        kpi_layout.setSpacing(8)

        self.card_gw_rtt = self._create_kpi_card("Latest Gateway RTT", "-- ms", ThemeTokens.BRAND_PRIMARY)
        self.card_ext_rtt = self._create_kpi_card("Latest External RTT", "-- ms", "#818cf8")
        self.card_loss = self._create_kpi_card("Latest Packet Loss", "-- %", ThemeTokens.COLOR_ERROR)
        self.card_count = self._create_kpi_card("Evaluated Runs", "0 runs", ThemeTokens.TEXT_SECONDARY)

        kpi_layout.addWidget(self.card_gw_rtt)
        kpi_layout.addWidget(self.card_ext_rtt)
        kpi_layout.addWidget(self.card_loss)
        kpi_layout.addWidget(self.card_count)

        root_layout.addWidget(self.kpi_row)

        # Stacked Container: View 0 = Chart, View 1 = Insufficient Data Empty State
        self.chart_container = QWidget(self)
        self.chart_container.setMinimumHeight(190)
        self.stack_layout = QStackedLayout(self.chart_container)

        # View 0: QtChart View
        self.chart_view = self._build_chart_view()
        self.stack_layout.addWidget(self.chart_view)

        # View 1: Honest Empty State
        self.empty_view = self._build_empty_view()
        self.stack_layout.addWidget(self.empty_view)

        root_layout.addWidget(self.chart_container)

        # Default to empty state until data loaded
        self.stack_layout.setCurrentIndex(1)

    def _create_kpi_card(self, label: str, default_val: str, accent_color: str) -> QFrame:
        card = QFrame(self.kpi_row)
        card.setStyleSheet(
            f"background-color: {ThemeTokens.BG_WINDOW}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
            f"padding: 6px 10px;"
        )
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(2)

        lbl = QLabel(label, card)
        lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ThemeTokens.TEXT_MUTED};")
        lbl.setWordWrap(True)
        val = QLabel(default_val, card)
        val.setObjectName("kpi_val")
        val.setStyleSheet(
            f"font-size: 20px; font-weight: 700; font-family: {ThemeTokens.FONT_MONO}; color: {accent_color};"
        )

        c_layout.addWidget(lbl)
        c_layout.addWidget(val)
        card.val_label = val  # type: ignore[attr-defined]
        return card

    def _build_chart_view(self) -> QWidget:
        if not QT_CHARTS_AVAILABLE:
            fallback = QLabel("QtCharts module not available in runtime.", self)
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return fallback

        self.chart = QChart()
        self.chart.setBackgroundVisible(False)
        self.chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        self.chart.layout().setContentsMargins(4, 4, 4, 4)
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chart.legend().setLabelColor(QColor(ThemeTokens.TEXT_SECONDARY))

        # Gateway Latency Series (Sky Blue)
        self.series_gw = QLineSeries()
        self.series_gw.setName("Gateway Latency (ms)")
        pen_gw = QPen(QColor(ThemeTokens.BRAND_PRIMARY), 2.5)
        self.series_gw.setPen(pen_gw)
        self.series_gw.setPointsVisible(True)
        self.chart.addSeries(self.series_gw)

        # External Latency Series (Indigo)
        self.series_ext = QLineSeries()
        self.series_ext.setName("External Latency (ms)")
        pen_ext = QPen(QColor("#818cf8"), 2.5)
        self.series_ext.setPen(pen_ext)
        self.series_ext.setPointsVisible(True)
        self.chart.addSeries(self.series_ext)

        # Packet Loss Series (Red dashed)
        self.series_loss = QLineSeries()
        self.series_loss.setName("Packet Loss (%)")
        pen_loss = QPen(QColor(ThemeTokens.COLOR_ERROR), 2.0, Qt.PenStyle.DashLine)
        self.series_loss.setPen(pen_loss)
        self.series_loss.setPointsVisible(True)
        self.chart.addSeries(self.series_loss)

        # Left Axis: Latency in milliseconds
        self.axis_x = QValueAxis()
        self.axis_x.setLabelFormat("%d")
        self.axis_x.setTitleText("Run Index (Chronological)")
        self.axis_x.setTitleBrush(QColor(ThemeTokens.TEXT_MUTED))
        self.axis_x.setLabelsColor(QColor(ThemeTokens.TEXT_MUTED))
        self.axis_x.setGridLineColor(QColor(ThemeTokens.BORDER_DEFAULT))

        self.axis_y_lat = QValueAxis()
        self.axis_y_lat.setTitleText("Latency (ms)")
        self.axis_y_lat.setTitleBrush(QColor(ThemeTokens.TEXT_MUTED))
        self.axis_y_lat.setLabelsColor(QColor(ThemeTokens.TEXT_MUTED))
        self.axis_y_lat.setGridLineColor(QColor(ThemeTokens.BORDER_DEFAULT))
        self.axis_y_lat.setRange(0, 50)

        # Right Axis: Packet Loss in percentage
        self.axis_y_loss = QValueAxis()
        self.axis_y_loss.setTitleText("Packet Loss (%)")
        self.axis_y_loss.setTitleBrush(QColor(ThemeTokens.COLOR_ERROR))
        self.axis_y_loss.setLabelsColor(QColor(ThemeTokens.COLOR_ERROR))
        self.axis_y_loss.setGridLineColor(QColor("transparent"))
        self.axis_y_loss.setRange(0, 20)

        self.chart.addAxis(self.axis_x, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.axis_y_lat, Qt.AlignmentFlag.AlignLeft)
        self.chart.addAxis(self.axis_y_loss, Qt.AlignmentFlag.AlignRight)

        self.series_gw.attachAxis(self.axis_x)
        self.series_gw.attachAxis(self.axis_y_lat)
        self.series_ext.attachAxis(self.axis_x)
        self.series_ext.attachAxis(self.axis_y_lat)
        self.series_loss.attachAxis(self.axis_x)
        self.series_loss.attachAxis(self.axis_y_loss)

        # Interactive point hover tooltips
        self.series_gw.hovered.connect(lambda pt, st: self._on_point_hovered("Gateway Latency", pt, st, "ms"))
        self.series_ext.hovered.connect(lambda pt, st: self._on_point_hovered("External Latency", pt, st, "ms"))
        self.series_loss.hovered.connect(lambda pt, st: self._on_point_hovered("Packet Loss", pt, st, "%"))

        view = QChartView(self.chart, self.chart_container)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setStyleSheet("background: transparent; border: none;")
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return view

    def _build_empty_view(self) -> QWidget:
        container = QFrame(self.chart_container)
        container.setStyleSheet(
            f"background-color: {ThemeTokens.BG_WINDOW}; "
            f"border: 1px dashed {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
            f"padding: 24px;"
        )
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        lbl_icon = QLabel("📈", container)
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_icon.setStyleSheet("font-size: 28px;")

        lbl_title = QLabel("Longitudinal Trends Require Multiple Diagnostic Runs", container)
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY};")

        lbl_msg = QLabel(
            "At least 2 completed runs with recorded telemetry are required to generate cross-run "
            "trend lines. Run additional diagnostic runs to establish an empirical performance baseline. "
            "Historical values are never fabricated or simulated.",
            container,
        )
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_msg.setStyleSheet(f"font-size: 12px; color: {ThemeTokens.TEXT_MUTED}; max-width: 480px;")
        lbl_msg.setWordWrap(True)

        layout.addWidget(lbl_icon)
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_msg)
        return container

    def set_historical_sessions(self, sessions: List[HistoricalSessionSummary]) -> None:
        """Update chart and KPI summary cards with actual chronological sessions."""
        self._sessions = sessions

        # Filter for sessions that have at least one valid measurement
        valid_sessions = [
            s for s in sessions
            if s.gateway_latency_ms is not None or s.avg_latency_ms is not None or s.packet_loss_pct is not None
        ]
        self._valid_sessions = valid_sessions

        if len(valid_sessions) < 2 or not QT_CHARTS_AVAILABLE:
            self.stack_layout.setCurrentIndex(1)
            self._update_kpis_empty(len(sessions))
            return

        self.stack_layout.setCurrentIndex(0)
        self._render_series(valid_sessions)
        self._update_kpis(valid_sessions)

    def _render_series(self, sessions: List[HistoricalSessionSummary]) -> None:
        if len(sessions) > 80:
            self.chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        else:
            self.chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        self.series_gw.clear()
        self.series_ext.clear()
        self.series_loss.clear()

        max_lat = 10.0

        for idx, s in enumerate(sessions):
            # Session index on X axis: 1, 2, ..., N
            x_val = float(idx + 1)

            if s.gateway_latency_ms is not None:
                self.series_gw.append(x_val, s.gateway_latency_ms)
                if s.gateway_latency_ms > max_lat:
                    max_lat = s.gateway_latency_ms

            if s.avg_latency_ms is not None:
                self.series_ext.append(x_val, s.avg_latency_ms)
                if s.avg_latency_ms > max_lat:
                    max_lat = s.avg_latency_ms

            if s.packet_loss_pct is not None:
                self.series_loss.append(x_val, s.packet_loss_pct)

        # Scale axes
        n = len(sessions)
        self.axis_x.setRange(1, max(2, n))
        self.axis_x.setTickCount(min(n, 12))

        # Add 25% headroom to Y latency axis
        upper_lat = max(20.0, max_lat * 1.25)
        self.axis_y_lat.setRange(0, upper_lat)

        # Scale Y loss axis
        losses = [s.packet_loss_pct for s in sessions if s.packet_loss_pct is not None]
        max_loss = max(losses) if losses else 0.0
        upper_loss = max(20.0, min(100.0, max_loss * 1.5))
        self.axis_y_loss.setRange(0, upper_loss)

    def _on_point_hovered(self, name: str, point: QPointF, state: bool, unit: str) -> None:
        from PySide6.QtWidgets import QToolTip
        if not state:
            QToolTip.hideText()
            return

        if hasattr(self, "chart_view") and self.chart_view:
            from PySide6.QtGui import QCursor

            idx = int(round(point.x())) - 1
            if hasattr(self, "_valid_sessions") and 0 <= idx < len(self._valid_sessions):
                s = self._valid_sessions[idx]
                gw_str = f"{s.gateway_latency_ms:.1f} ms" if s.gateway_latency_ms is not None else "N/A"
                ext_str = f"{s.avg_latency_ms:.1f} ms" if s.avg_latency_ms is not None else "N/A"
                loss_str = f"{s.packet_loss_pct:.1f}%" if s.packet_loss_pct is not None else "0.0%"
                diag = s.diagnosis or "Normal / Nominal"
                if len(diag) > 42:
                    diag = diag[:39] + "..."

                tip_html = (
                    "<div style='background-color:#0f172a; color:#ffffff; padding:6px 8px; font-family:Segoe UI, sans-serif; font-size:12px;'>"
                    f"<div style='font-weight:700; font-size:13px; color:#60a5fa; margin-bottom:2px;'>Run #{idx + 1} &bull; {s.run_id}</div>"
                    f"<div style='color:#94a3b8; font-size:11px; margin-bottom:6px;'>Time: {s.start_time_utc}</div>"
                    f"<div style='margin-bottom:2px;'>&bull; Gateway Latency: <b style='color:#38bdf8;'>{gw_str}</b></div>"
                    f"<div style='margin-bottom:2px;'>&bull; External Latency: <b style='color:#a5b4fc;'>{ext_str}</b></div>"
                    f"<div style='margin-bottom:4px;'>&bull; Packet Loss: <b style='color:#f87171;'>{loss_str}</b></div>"
                    f"<div style='color:#cbd5e1; font-size:11px; border-top:1px solid #334155; padding-top:4px;'>Diagnosis: {diag}</div>"
                    "</div>"
                )
            else:
                tip_html = (
                    "<div style='background-color:#0f172a; color:#ffffff; padding:6px 8px; font-family:Segoe UI, sans-serif; font-size:12px;'>"
                    f"<div style='font-weight:700; color:#60a5fa;'>{name}</div>"
                    f"<div>Run #{int(round(point.x()))}: <b>{point.y():.1f} {unit}</b></div>"
                    "</div>"
                )

            QToolTip.showText(QCursor.pos(), tip_html, self.chart_view)

    def _update_kpis(self, sessions: List[HistoricalSessionSummary]) -> None:
        latest = sessions[-1]
        gw = latest.gateway_latency_ms
        ext = latest.avg_latency_ms
        loss = latest.packet_loss_pct

        gw_text = f"{gw:.1f} ms" if gw is not None else "Not Measured"
        ext_text = f"{ext:.1f} ms" if ext is not None else "Not Measured"
        loss_text = f"{loss:.1f} %" if loss is not None else "Not Measured"

        self.card_gw_rtt.val_label.setText(gw_text)  # type: ignore[attr-defined]
        self.card_ext_rtt.val_label.setText(ext_text)  # type: ignore[attr-defined]
        self.card_loss.val_label.setText(loss_text)  # type: ignore[attr-defined]
        self.card_count.val_label.setText(f"{len(sessions)} runs")  # type: ignore[attr-defined]

    def _update_kpis_empty(self, total_sessions: int) -> None:
        self.card_gw_rtt.val_label.setText("-- ms")  # type: ignore[attr-defined]
        self.card_ext_rtt.val_label.setText("-- ms")  # type: ignore[attr-defined]
        self.card_loss.val_label.setText("-- %")  # type: ignore[attr-defined]
        self.card_count.val_label.setText(f"{total_sessions} runs")  # type: ignore[attr-defined]
