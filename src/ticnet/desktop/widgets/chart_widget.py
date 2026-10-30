"""Real measurement visualization widgets using PySide6.QtCharts (PingPlotter-Inspired).

Adheres strictly to Phase 10 / Stage 3 data integrity rules:
- Reads solely from persisted SQLite Observation entities.
- Never interpolates missing values or represents missing data as 0 ms.
- Represents packet loss / timeouts as prominent full-height red vertical bars.
- Provides interactive target selection (Local Gateway vs External Targets).
- Renders PingPlotter-style latency threshold background zones (0-50ms green, 50-100ms amber, >100ms red).
- Distinctly displays honest 'No measurement available' messages when data is absent.
- Displays sample counts (n), units, and empirical measurement periods.
"""

from __future__ import annotations

import json
import statistics
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtCharts import (
        QBarCategoryAxis,
        QBarSeries,
        QBarSet,
        QChart,
        QChartView,
        QLineSeries,
        QScatterSeries,
        QValueAxis,
    )

try:
    from PySide6.QtCharts import (
        QBarCategoryAxis,
        QBarSeries,
        QBarSet,
        QChart,
        QChartView,
        QLineSeries,
        QScatterSeries,
        QValueAxis,
    )
    QT_CHARTS_AVAILABLE = True
except ImportError:
    QT_CHARTS_AVAILABLE = False

from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.target_trace_widget import TargetTraceTableWidget
from ticnet.models.entities import Observation, Summary, Target, TestRun
from ticnet.models.enums import MeasurementStatus, TargetType


# Distinct curated series colors for targets
TARGET_COLOR_MAP: Dict[str, str] = {
    "gw": "#0284c7",          # Sky Blue (Checkpoint 1 Gateway)
    "cloudflare": "#f97316",  # Orange (Cloudflare)
    "google": "#10b981",      # Emerald (Google)
    "quad9": "#a855f7",       # Purple (Quad9)
}
FALLBACK_COLORS = ["#38bdf8", "#ec4899", "#14b8a6", "#fbbf24", "#6366f1"]


if QT_CHARTS_AVAILABLE:
    class PingPlotterChartView(QChartView):
        """Custom QChartView rendering PingPlotter-style latency threshold background zones."""

        def __init__(self, chart: QChart, parent: Optional[QWidget] = None) -> None:
            super().__init__(chart, parent)
            self.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.setStyleSheet("background: transparent; border: none;")

        def drawBackground(self, painter: QPainter, rect: Union[QRectF, QRect]) -> None:
            super().drawBackground(painter, rect)
            chart = self.chart()
            if not chart:
                return

            plot_area = chart.plotArea()
            if plot_area.isEmpty() or plot_area.width() <= 0 or plot_area.height() <= 0:
                return

            series_list = chart.series()
            if not series_list:
                return

            ref_series = series_list[0]
            left_x = plot_area.left()
            width = plot_area.width()
            bottom_y = plot_area.bottom()
            top_y = plot_area.top()

            painter.save()
            painter.setClipRect(plot_area)

            try:
                # Map latency thresholds (0 ms, 50 ms, 100 ms) to screen pixel coordinates
                pos_0 = chart.mapToPosition(QPointF(0, 0), ref_series).y()
                pos_50 = chart.mapToPosition(QPointF(0, 50), ref_series).y()
                pos_100 = chart.mapToPosition(QPointF(0, 100), ref_series).y()

                # Clamp to plot area bounds
                pos_0 = min(bottom_y, max(top_y, pos_0))
                pos_50 = min(bottom_y, max(top_y, pos_50))
                pos_100 = min(bottom_y, max(top_y, pos_100))

                # Zone 1: Healthy (0 - 50 ms) -> Soft Green
                if pos_0 > pos_50:
                    painter.fillRect(QRectF(left_x, pos_50, width, pos_0 - pos_50), QColor(16, 185, 129, 14))

                # Zone 2: Warning (50 - 100 ms) -> Soft Amber
                if pos_50 > pos_100:
                    painter.fillRect(QRectF(left_x, pos_100, width, pos_50 - pos_100), QColor(245, 158, 11, 14))

                # Zone 3: Degraded (> 100 ms) -> Soft Red
                if pos_100 > top_y:
                    painter.fillRect(QRectF(left_x, top_y, width, pos_100 - top_y), QColor(239, 68, 68, 14))

                # Guide lines at 50 ms and 100 ms
                pen_dash = QPen(QColor(ThemeTokens.BORDER_DEFAULT))
                pen_dash.setStyle(Qt.PenStyle.DashLine)
                pen_dash.setWidth(1)
                painter.setPen(pen_dash)

                if top_y < pos_50 < bottom_y:
                    painter.drawLine(int(left_x), int(pos_50), int(left_x + width), int(pos_50))
                if top_y < pos_100 < bottom_y:
                    painter.drawLine(int(left_x), int(pos_100), int(left_x + width), int(pos_100))
            except Exception:
                pass
            finally:
                painter.restore()
else:
    PingPlotterChartView = None  # type: ignore


class LatencyTimelineChart(QWidget):
    """Line and packet-loss timeline chart showing sequential probe samples.

    PingPlotter-Inspired Features:
    - Plots empirical latency in milliseconds.
    - Displays full-height red vertical bars for timed-out/lost packets (never 0 ms).
    - Multi-target filtering (All Targets, Gateway Only, External Only, Individual).
    - Sample range controls (All Probes, First 10, Recent).
    - Rich interactive tooltips for data points and packet-loss events.
    - Latency threshold background zones (0-50ms green, 50-100ms amber, >100ms red).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(320)

        self._all_observations: List[Observation] = []
        self._targets_meta: List[Target] = []

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(8)

        # 1. Interactive Control & Badge Bar
        self.control_bar = QWidget(self)
        ctrl_layout = QHBoxLayout(self.control_bar)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(10)

        lbl_target = QLabel("Target:", self.control_bar)
        lbl_target.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ThemeTokens.TEXT_SECONDARY};")
        ctrl_layout.addWidget(lbl_target)

        self.cmb_target = QComboBox(self.control_bar)
        self.cmb_target.setStyleSheet(
            f"background-color: {ThemeTokens.BG_MUTED}; color: {ThemeTokens.TEXT_PRIMARY}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; border-radius: 4px; padding: 3px 8px; font-size: 11px;"
        )
        self.cmb_target.addItem("All Targets (Overlay View)", "ALL")
        self.cmb_target.addItem("Local Gateway Only [Checkpoint 1]", "GATEWAY")
        self.cmb_target.addItem("External Targets [Checkpoint 2]", "EXTERNAL")
        self.cmb_target.currentIndexChanged.connect(self._on_filter_changed)
        ctrl_layout.addWidget(self.cmb_target)

        lbl_range = QLabel("Range:", self.control_bar)
        lbl_range.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ThemeTokens.TEXT_SECONDARY};")
        ctrl_layout.addWidget(lbl_range)

        self.cmb_range = QComboBox(self.control_bar)
        self.cmb_range.setStyleSheet(
            f"background-color: {ThemeTokens.BG_MUTED}; color: {ThemeTokens.TEXT_PRIMARY}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; border-radius: 4px; padding: 3px 8px; font-size: 11px;"
        )
        self.cmb_range.addItem("All Probes in Run", "ALL")
        self.cmb_range.addItem("First 10 Probes", "FIRST_10")
        self.cmb_range.addItem("Recent Probes (Last 10)", "LAST_10")
        self.cmb_range.currentIndexChanged.connect(self._on_filter_changed)
        ctrl_layout.addWidget(self.cmb_range)

        ctrl_layout.addStretch()

        # Summary Badges on the right
        self.lbl_loss_badge = QLabel("Packet Loss: 0.0%", self.control_bar)
        self.lbl_loss_badge.setStyleSheet(
            f"background-color: {ThemeTokens.BG_MUTED}; color: {ThemeTokens.COLOR_SUCCESS}; "
            f"font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: {ThemeTokens.RADIUS_PILL};"
        )
        ctrl_layout.addWidget(self.lbl_loss_badge)

        self.lbl_rtt_badge = QLabel("Min: -- | Med: -- | Max: --", self.control_bar)
        self.lbl_rtt_badge.setStyleSheet(
            f"color: {ThemeTokens.TEXT_MUTED}; font-size: 11px; font-family: Consolas, monospace;"
        )
        ctrl_layout.addWidget(self.lbl_rtt_badge)

        self.main_layout.addWidget(self.control_bar)

        # 2. Stacked Layout (Empty State vs Active Chart)
        self.stack_widget = QWidget(self)
        self.stack_layout = QStackedLayout(self.stack_widget)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)

        # Empty State
        self.empty_widget = QFrame(self.stack_widget)
        self.empty_widget.setStyleSheet(
            f"background-color: {ThemeTokens.BG_MUTED}; "
            f"border: 1px dashed {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty = QLabel("No latency probe measurements available for this session.", self.empty_widget)
        self.lbl_empty.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 13px; font-weight: 500;")
        empty_layout.addWidget(self.lbl_empty)
        self.stack_layout.addWidget(self.empty_widget)

        # QtChart View
        if QT_CHARTS_AVAILABLE:
            self.chart = QChart()
            self.chart.setBackgroundVisible(False)
            self.chart.layout().setContentsMargins(0, 0, 0, 0)
            self.chart.legend().setVisible(True)
            self.chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
            self.chart.legend().setLabelColor(QColor(ThemeTokens.TEXT_SECONDARY))

            self.chart_view = PingPlotterChartView(self.chart, self.stack_widget)
            self.stack_layout.addWidget(self.chart_view)
        else:
            self.chart = None
            self.chart_view = None

        self.main_layout.addWidget(self.stack_widget)

        # Footnote metadata label
        self.lbl_meta = QLabel("", self)
        self.lbl_meta.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 11px;")
        self.main_layout.addWidget(self.lbl_meta)

    def set_selected_target(self, target_id: str) -> None:
        """Filter chart view to a specific target identifier."""
        for idx in range(self.cmb_target.count()):
            if self.cmb_target.itemData(idx) == target_id:
                self.cmb_target.setCurrentIndex(idx)
                return

    def _on_filter_changed(self) -> None:
        if self._all_observations:
            self._render_filtered_data()

    def load_rtt_observations(self, observations: List[Observation]) -> None:
        """Populate chart with RTT observations (backwards-compatible API).

        Invariants:
        - Filters observations for metric_name == 'round_trip_time'.
        - Only plots valid numbers on line curves; never coerces loss to 0.
        - Renders timed-out probes as PingPlotter red vertical timeout bars.
        """
        self.load_multi_target_observations(observations)

    def load_multi_target_observations(
        self,
        observations: List[Observation],
        targets: Optional[List[Target]] = None,
    ) -> None:
        """Populate chart with multi-target observations and setup target selector."""
        self._all_observations = [
            obs for obs in observations if obs.metric_name == "round_trip_time"
        ]
        self._targets_meta = targets or []

        if not QT_CHARTS_AVAILABLE or self.chart is None:
            self.lbl_empty.setText("QtCharts component is unavailable.")
            self.stack_layout.setCurrentIndex(0)
            return

        # Setup dynamic target selector dropdown
        self.cmb_target.blockSignals(True)
        current_data = self.cmb_target.currentData()
        self.cmb_target.clear()
        self.cmb_target.addItem("All Targets (Overlay View)", "ALL")
        self.cmb_target.addItem("Local Gateway Only [Checkpoint 1]", "GATEWAY")
        self.cmb_target.addItem("External Targets [Checkpoint 2]", "EXTERNAL")

        target_ids = sorted(
            list({o.target_id for o in self._all_observations if o.target_id}),
            key=lambda tid: (0 if self._is_gateway_target(tid) else 1, tid),
        )
        for tid in target_ids:
            t_meta = next((t for t in self._targets_meta if t.target_id == tid), None)
            if self._is_gateway_target(tid):
                gw_ip = t_meta.ip_address if (t_meta and t_meta.ip_address) else ""
                label = f"Local Gateway ({gw_ip})" if gw_ip else "Local Gateway [Checkpoint 1]"
            elif "cloudflare" in tid.lower():
                label = "Cloudflare Primary (1.1.1.1)"
            elif "google" in tid.lower():
                label = "Google Public DNS (8.8.8.8)"
            elif "quad9" in tid.lower():
                label = "Quad9 Anycast (9.9.9.9)"
            elif t_meta and t_meta.provider_label:
                ip_suffix = f" ({t_meta.ip_address})" if t_meta.ip_address else ""
                label = f"{t_meta.provider_label}{ip_suffix}"
            else:
                label = tid
            self.cmb_target.addItem(label, tid)

        # Restore previous selection if still available, else default to ALL
        idx = self.cmb_target.findData(current_data)
        if idx >= 0:
            self.cmb_target.setCurrentIndex(idx)
        else:
            self.cmb_target.setCurrentIndex(0)
        self.cmb_target.blockSignals(False)

        self._render_filtered_data()

    def _is_gateway_target(self, tid: str) -> bool:
        """Deterministically identify whether a target identifier represents the local gateway."""
        if not tid:
            return False
        tid_lower = tid.lower()
        if "gw" in tid_lower or "gateway" in tid_lower:
            return True
        t_meta = next((t for t in self._targets_meta if t.target_id == tid), None)
        if t_meta:
            if t_meta.target_type == TargetType.GATEWAY:
                return True
            if t_meta.provider_label and ("gateway" in t_meta.provider_label.lower() or "router" in t_meta.provider_label.lower()):
                return True
        # Heuristic fallback: if it doesn't match external, resolver, or throughput prefixes
        if not any(k in tid_lower for k in ("ext-", "dns-", "tp-", "cloudflare", "google", "quad9")):
            return True
        return False

    def _render_filtered_data(self) -> None:
        """Render series, axes, tooltips, and vertical packet loss bars based on active filters."""
        if not QT_CHARTS_AVAILABLE or self.chart is None:
            return

        target_filter = self.cmb_target.currentData()
        range_filter = self.cmb_range.currentData()

        # Filter observations by target
        filtered: List[Observation] = []
        for obs in self._all_observations:
            tid = obs.target_id or ""
            is_gw = self._is_gateway_target(tid)
            if target_filter == "ALL":
                filtered.append(obs)
            elif target_filter == "GATEWAY":
                if is_gw:
                    filtered.append(obs)
            elif target_filter == "EXTERNAL":
                if not is_gw:
                    filtered.append(obs)
            elif obs.target_id == target_filter:
                filtered.append(obs)

        # Group by target_id
        target_groups: Dict[str, List[Observation]] = {}
        for obs in filtered:
            target_groups.setdefault(obs.target_id or "unspecified", []).append(obs)

        # Apply range filter to each group
        total_attempted = 0
        total_valid = 0
        total_timeouts = 0
        all_valid_rtts: List[float] = []

        active_groups: Dict[str, List[Observation]] = {}
        for tid, o_list in target_groups.items():
            o_list_sorted = sorted(o_list, key=lambda o: (o.sample_index, o.timestamp_utc))
            if range_filter == "FIRST_10":
                o_list_sorted = o_list_sorted[:10]
            elif range_filter == "LAST_10":
                o_list_sorted = o_list_sorted[-10:]
            active_groups[tid] = o_list_sorted

            for o in o_list_sorted:
                total_attempted += 1
                if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None:
                    total_valid += 1
                    all_valid_rtts.append(float(o.value_numeric))
                else:
                    total_timeouts += 1

        # Check for empty or timeout-only cases
        if total_valid == 0:
            self.chart.removeAllSeries()
            for ax in list(self.chart.axes()):
                self.chart.removeAxis(ax)

            if total_attempted == 0:
                self.lbl_empty.setText("No successful round-trip latency samples recorded (no measurements available).")
            else:
                self.lbl_empty.setText("No successful round-trip latency samples recorded (all timed out or skipped).")
            self.stack_layout.setCurrentIndex(0)
            self.lbl_meta.setText("Empirical Samples: n = 0 | Missing values are excluded, never coerced to 0.")
            self.lbl_loss_badge.setText(f"Packet Loss: 100% ({total_timeouts}/{total_attempted})")
            self.lbl_loss_badge.setStyleSheet(
                f"background-color: {ThemeTokens.BG_ERROR_LIGHT}; color: {ThemeTokens.COLOR_ERROR}; "
                f"font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: {ThemeTokens.RADIUS_PILL};"
            )
            self.lbl_rtt_badge.setText("Min: -- | Med: -- | Max: --")
            return

        # Active Chart Rendering
        self.chart.removeAllSeries()
        for ax in list(self.chart.axes()):
            self.chart.removeAxis(ax)

        min_val = min(all_valid_rtts)
        max_val = max(all_valid_rtts)
        med_val = statistics.median(all_valid_rtts)

        loss_pct = (total_timeouts / total_attempted * 100.0) if total_attempted > 0 else 0.0

        # Update badges
        if loss_pct <= 0.0:
            self.lbl_loss_badge.setText(f"Packet Loss: 0.0% (0 / {total_attempted})")
            self.lbl_loss_badge.setStyleSheet(
                f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; color: {ThemeTokens.COLOR_SUCCESS}; "
                f"font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: {ThemeTokens.RADIUS_PILL};"
            )
        else:
            self.lbl_loss_badge.setText(f"Packet Loss: {loss_pct:.1f}% ({total_timeouts} / {total_attempted})")
            self.lbl_loss_badge.setStyleSheet(
                f"background-color: {ThemeTokens.BG_ERROR_LIGHT}; color: {ThemeTokens.COLOR_ERROR}; "
                f"font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: {ThemeTokens.RADIUS_PILL};"
            )

        self.lbl_rtt_badge.setText(f"Min: {min_val:.1f} ms | Med: {med_val:.1f} ms | Max: {max_val:.1f} ms")

        # Determine axes bounds
        min_sample = 1
        max_sample = 1
        for o_list in active_groups.values():
            for o in o_list:
                s_idx = o.sample_index or 1
                min_sample = min(min_sample, s_idx)
                max_sample = max(max_sample, s_idx)

        y_ceiling = max(30.0, max_val * 1.25)

        # 1. Add Line Series for each target
        added_series_list: List[QLineSeries] = []
        color_idx = 0

        for tid, o_list in active_groups.items():
            valid_obs = [o for o in o_list if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]
            if not valid_obs:
                continue

            series = QLineSeries()
            target_name = self._get_target_display_name(tid)
            series.setName(target_name)

            color_hex = self._get_target_color(tid, color_idx)
            color_idx += 1
            pen = QPen(QColor(color_hex))
            pen.setWidth(2)
            series.setPen(pen)

            obs_by_sample: Dict[int, Observation] = {}
            for obs in valid_obs:
                sample_num = obs.sample_index
                val = float(obs.value_numeric)  # type: ignore
                series.append(sample_num, val)
                obs_by_sample[sample_num] = obs

            # Connect interactive tooltip
            series.hovered.connect(self._create_point_hover_handler(target_name, obs_by_sample))

            self.chart.addSeries(series)
            added_series_list.append(series)

        # 2. PingPlotter-Style Vertical Red Bars for Timed-Out Probes
        timeout_series_first = True
        for tid, o_list in active_groups.items():
            target_name = self._get_target_display_name(tid)
            timeout_obs = [o for o in o_list if o.status != MeasurementStatus.SUCCESS or o.value_numeric is None]

            for to_o in timeout_obs:
                s_idx = to_o.sample_index
                to_line = QLineSeries()
                if timeout_series_first:
                    to_line.setName("Packet Loss (Timeout)")
                    timeout_series_first = False
                else:
                    to_line.setName(f"Loss_{tid}_{s_idx}")

                pen_to = QPen(QColor(ThemeTokens.COLOR_ERROR))
                pen_to.setWidth(3)
                to_line.setPen(pen_to)
                to_line.append(s_idx, 0.0)
                to_line.append(s_idx, y_ceiling)

                # Connect hover tooltip on timeout bar
                to_line.hovered.connect(self._create_timeout_hover_handler(target_name, s_idx, to_o))

                self.chart.addSeries(to_line)
                added_series_list.append(to_line)

                # Hide duplicate legend markers for timeouts
                if not timeout_series_first:
                    markers = self.chart.legend().markers(to_line)
                    if markers:
                        markers[0].setVisible(False)

        # Configure Axes
        axis_x = QValueAxis()
        axis_x.setTitleText("Probe Sequence Sample")
        axis_x.setLabelFormat("%d")
        axis_x.setRange(max(1, min_sample - 0.5), max_sample + 0.5)
        axis_x.setTickCount(min(12, max_sample - min_sample + 2))
        axis_x.setLabelsColor(QColor(ThemeTokens.TEXT_SECONDARY))
        axis_x.setTitleBrush(QColor(ThemeTokens.TEXT_PRIMARY))
        axis_x.setGridLineColor(QColor(ThemeTokens.BORDER_DEFAULT))
        self.chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)

        axis_y = QValueAxis()
        axis_y.setTitleText("Round-Trip Delay (ms)")
        axis_y.setLabelFormat("%.1f")
        axis_y.setRange(0.0, y_ceiling)
        axis_y.setLabelsColor(QColor(ThemeTokens.TEXT_SECONDARY))
        axis_y.setTitleBrush(QColor(ThemeTokens.TEXT_PRIMARY))
        axis_y.setGridLineColor(QColor(ThemeTokens.BORDER_DEFAULT))
        self.chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)

        for s in added_series_list:
            s.attachAxis(axis_x)
            s.attachAxis(axis_y)

        self.stack_layout.setCurrentIndex(1)
        self.lbl_meta.setText(
            f"Empirical Samples: n = {len(all_valid_rtts)} | Min: {min_val:.1f} ms | Max: {max_val:.1f} ms | "
            f"Note: Charts display empirical samples and do not alone prove a root cause."
        )

    def _get_target_display_name(self, tid: str) -> str:
        tid_lower = tid.lower()
        if self._is_gateway_target(tid):
            t_meta = next((t for t in self._targets_meta if t.target_id == tid), None)
            gw_ip = t_meta.ip_address if (t_meta and t_meta.ip_address) else ""
            return f"Local Gateway ({gw_ip})" if gw_ip else "Local Gateway"
        if "cloudflare" in tid_lower:
            return "Cloudflare (1.1.1.1)"
        if "google" in tid_lower:
            return "Google DNS (8.8.8.8)"
        if "quad9" in tid_lower:
            return "Quad9 (9.9.9.9)"
        t_meta = next((t for t in self._targets_meta if t.target_id == tid), None)
        if t_meta and t_meta.provider_label:
            return t_meta.provider_label
        return tid

    def _get_target_color(self, tid: str, idx: int) -> str:
        if self._is_gateway_target(tid):
            return TARGET_COLOR_MAP.get("gw", "#0284c7")
        tid_lower = tid.lower()
        for key, color in TARGET_COLOR_MAP.items():
            if key in tid_lower:
                return color
        return FALLBACK_COLORS[idx % len(FALLBACK_COLORS)]

    def _create_point_hover_handler(self, target_name: str, obs_by_sample: Dict[int, Observation]):
        def handler(point: QPointF, state: bool) -> None:
            if state:
                s_idx = int(round(point.x()))
                obs = obs_by_sample.get(s_idx)
                ts = obs.timestamp_utc if obs else "N/A"
                tip_text = (
                    "<div style='background-color:#0f172a; color:#ffffff; padding:6px 8px; font-family:Segoe UI, sans-serif; font-size:12px; min-width:180px;'>"
                    f"<div style='font-weight:700; font-size:13px; color:#60a5fa; margin-bottom:3px;'>{target_name}</div>"
                    f"<div>Sample: <b>#{s_idx}</b></div>"
                    f"<div>Latency: <b style='color:#38bdf8;'>{point.y():.1f} ms</b></div>"
                    f"<div style='color:#94a3b8; font-size:11px; margin-top:3px;'>Time: {ts}</div>"
                    "</div>"
                )
                if self.chart_view:
                    QToolTip.showText(QCursor.pos(), tip_text, self.chart_view)
            else:
                QToolTip.hideText()
        return handler

    def _create_timeout_hover_handler(self, target_name: str, sample_idx: int, obs: Observation):
        def handler(point: QPointF, state: bool) -> None:
            if state:
                ts = obs.timestamp_utc or "N/A"
                err = obs.error_message or "Request timed out"
                tip_text = (
                    "<div style='background-color:#0f172a; color:#ffffff; padding:6px 8px; font-family:Segoe UI, sans-serif; font-size:12px; min-width:210px;'>"
                    f"<div style='font-weight:700; font-size:13px; color:#f87171; margin-bottom:3px;'>{target_name} &bull; TIMEOUT</div>"
                    f"<div>Sample: <b>#{sample_idx}</b></div>"
                    f"<div style='color:#ef4444; font-weight:700;'>Status: TIMEOUT / PACKET LOSS</div>"
                    f"<div style='color:#fca5a5;'>Error: {err}</div>"
                    f"<div style='color:#94a3b8; font-size:11px; margin-top:3px;'>Time: {ts}</div>"
                    f"<div style='color:#94a3b8; font-size:10px; font-style:italic; margin-top:3px;'>Missing probe represented as vertical red loss bar, never 0 ms.</div>"
                    "</div>"
                )
                if self.chart_view:
                    QToolTip.showText(QCursor.pos(), tip_text, self.chart_view)
            else:
                QToolTip.hideText()
        return handler


class LoadedLatencyBarChart(QWidget):
    """Bar chart comparing baseline unloaded latency versus loaded latency (Bufferbloat)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(260)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(6)

        self.stack_widget = QWidget(self)
        self.stack_layout = QStackedLayout(self.stack_widget)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)

        # 1. Honest Empty State Widget
        self.empty_widget = QFrame(self.stack_widget)
        self.empty_widget.setStyleSheet(
            f"background-color: {ThemeTokens.BG_MUTED}; "
            f"border: 1px dashed {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty = QLabel("Loaded latency (bufferbloat) was not evaluated for this session.", self.empty_widget)
        self.lbl_empty.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 13px; font-weight: 500;")
        empty_layout.addWidget(self.lbl_empty)
        self.stack_layout.addWidget(self.empty_widget)

        # 2. QtChart View
        if QT_CHARTS_AVAILABLE:
            self.chart = QChart()
            self.chart.setBackgroundVisible(False)
            self.chart.layout().setContentsMargins(0, 0, 0, 0)
            self.chart.legend().setVisible(True)
            self.chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
            self.chart.legend().setLabelColor(QColor(ThemeTokens.TEXT_SECONDARY))

            self.chart_view = QChartView(self.chart, self.stack_widget)
            self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.chart_view.setStyleSheet("background: transparent; border: none;")
            self.stack_layout.addWidget(self.chart_view)
        else:
            self.chart = None
            self.chart_view = None

        self.main_layout.addWidget(self.stack_widget)

        self.lbl_meta = QLabel("", self)
        self.lbl_meta.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 11px;")
        self.main_layout.addWidget(self.lbl_meta)

    def load_bufferbloat_observations(self, observations: List[Observation]) -> None:
        """Populate bufferbloat comparison from persisted loaded latency observations."""
        if not QT_CHARTS_AVAILABLE or self.chart is None:
            self.lbl_empty.setText("QtCharts component is unavailable.")
            self.stack_layout.setCurrentIndex(0)
            return

        delta_obs = next((obs for obs in observations if obs.metric_name == "loaded_latency_delta"), None)
        if not delta_obs or not delta_obs.method_metadata:
            self.lbl_empty.setText("Loaded latency not evaluated (Stage 5 skipped or gateway unreachable).")
            self.stack_layout.setCurrentIndex(0)
            self.lbl_meta.setText("Bufferbloat evaluation: Unavailable | No synthetic baseline generated.")
            return

        try:
            meta = json.loads(delta_obs.method_metadata)
            baseline_rtt = meta.get("baseline_median_rtt_ms")
            loaded_rtt = meta.get("loaded_median_rtt_ms")
            delta_ms = delta_obs.value_numeric

            if baseline_rtt is None or loaded_rtt is None:
                self.lbl_empty.setText("Incomplete bufferbloat data recorded in observation metadata.")
                self.stack_layout.setCurrentIndex(0)
                return

            self.chart.removeAllSeries()
            for ax in list(self.chart.axes()):
                self.chart.removeAxis(ax)

            # Bar set for Baseline vs Loaded
            set_baseline = QBarSet("Unloaded Baseline")
            set_baseline.setColor(QColor(ThemeTokens.BRAND_PRIMARY))
            set_baseline.append(float(baseline_rtt))

            set_loaded = QBarSet("Under Downstream Load")
            load_color = ThemeTokens.COLOR_ERROR if (delta_ms and delta_ms > 50.0) else ThemeTokens.COLOR_SUCCESS
            set_loaded.setColor(QColor(load_color))
            set_loaded.append(float(loaded_rtt))

            series = QBarSeries()
            series.append(set_baseline)
            series.append(set_loaded)
            self.chart.addSeries(series)

            categories = ["Median Delay"]
            axis_x = QBarCategoryAxis()
            axis_x.append(categories)
            axis_x.setLabelsColor(QColor(ThemeTokens.TEXT_SECONDARY))
            self.chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
            series.attachAxis(axis_x)

            axis_y = QValueAxis()
            axis_y.setTitleText("Median Round-Trip Delay (ms)")
            max_y = max(float(baseline_rtt), float(loaded_rtt)) * 1.25
            axis_y.setRange(0.0, max(10.0, max_y))
            axis_y.setLabelFormat("%.1f")
            axis_y.setLabelsColor(QColor(ThemeTokens.TEXT_SECONDARY))
            axis_y.setTitleBrush(QColor(ThemeTokens.TEXT_PRIMARY))
            axis_y.setGridLineColor(QColor(ThemeTokens.BORDER_DEFAULT))
            self.chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
            series.attachAxis(axis_y)

            self.stack_layout.setCurrentIndex(1)
            delta_str = f"+{delta_ms:.1f} ms" if delta_ms is not None else "N/A"
            self.lbl_meta.setText(
                f"Baseline: {baseline_rtt:.1f} ms | Loaded: {loaded_rtt:.1f} ms | Inflation Delta: {delta_str} | "
                f"Evaluated with concurrent bounded downstream HTTP transfer."
            )
        except Exception as exc:
            self.lbl_empty.setText(f"Unable to parse loaded latency telemetry: {exc}")
            self.stack_layout.setCurrentIndex(0)


class MeasurementChartsContainer(QFrame):
    """Composite container holding real measurement visualization charts and trace table."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background-color: {ThemeTokens.BG_CARD}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        container_layout = QVBoxLayout(self)
        container_layout.setContentsMargins(14, 12, 14, 12)
        container_layout.setSpacing(10)

        # Header
        header_widget = QWidget(self)
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel("Empirical Telemetry Visualizations", header_widget)
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY};")
        h_layout.addWidget(title_lbl)

        badge_lbl = QLabel("PingPlotter-Inspired Monitoring | Zero Synthetic Telemetry", header_widget)
        badge_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ThemeTokens.BRAND_PRIMARY}; "
            f"background-color: {ThemeTokens.BG_MUTED}; padding: 2px 8px; "
            f"border-radius: {ThemeTokens.RADIUS_PILL};"
        )
        h_layout.addWidget(badge_lbl)
        h_layout.addStretch()
        container_layout.addWidget(header_widget)

        # 1. Two-Checkpoint Target Comparison Trace Table
        self.target_trace = TargetTraceTableWidget(self)
        container_layout.addWidget(self.target_trace)

        # 2. RTT Timeline Chart with Vertical Packet-Loss Bars
        lbl_chart1 = QLabel("<b>Round-Trip Latency & Packet-Loss Timeline (ms):</b>", self)
        lbl_chart1.setTextFormat(Qt.TextFormat.RichText)
        container_layout.addWidget(lbl_chart1)

        self.rtt_chart = LatencyTimelineChart(self)
        container_layout.addWidget(self.rtt_chart)

        # Connect trace table row click to chart target filter
        self.target_trace.sig_target_selected.connect(self.rtt_chart.set_selected_target)

        # 3. Bufferbloat Bar Chart
        lbl_chart2 = QLabel("<b>Bufferbloat Latency Inflation (Loaded vs Baseline):</b>", self)
        lbl_chart2.setTextFormat(Qt.TextFormat.RichText)
        container_layout.addWidget(lbl_chart2)

        self.bufferbloat_chart = LoadedLatencyBarChart(self)
        container_layout.addWidget(self.bufferbloat_chart)

    def load_run_observations(
        self,
        observations: List[Observation],
        summaries: Optional[List[Summary]] = None,
        targets: Optional[List[Target]] = None,
        run: Optional[TestRun] = None,
    ) -> None:
        """Bind empirical observations of a run to trace table and visualization charts."""
        self.target_trace.load_trace_data(
            observations=observations,
            summaries=summaries,
            targets=targets,
            run=run,
        )
        self.rtt_chart.load_multi_target_observations(observations=observations, targets=targets)
        self.bufferbloat_chart.load_bufferbloat_observations(observations)
