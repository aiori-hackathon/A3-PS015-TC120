"""Antialiased, data-driven time-series line graph for network performance analytics.

Renders dual-series time trends comparing Actual measured telemetry (blue line
with circular markers and gradient area fill) against empirical Baseline (purple dashed line),
matching TicNet's reference analytics dashboard layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ticnet.monitoring.analytics import TimeWindow


@dataclass
class TimeSeriesPoint:
    """A single chronological measurement with actual and baseline values."""

    timestamp_dt: datetime
    actual_value: Optional[float]
    baseline_value: Optional[float]
    time_str: str              # Short time label for axis, e.g. "12:30"
    tooltip_time_str: str      # Full timestamp for tooltip, e.g. "2026-10-29 12:30"


# Metric Display Configurations
METRIC_CONFIGS: Dict[str, Dict[str, Any]] = {
    "latency": {
        "title": "External Latency",
        "y_axis_title": "Latency (ms)",
        "unit": "ms",
        "default_baseline": 38.0,
        "default_max_y": 100.0,
        "format": lambda v: f"{v:.1f} ms" if v is not None else "—",
    },
    "gateway_latency": {
        "title": "Gateway Latency",
        "y_axis_title": "Latency (ms)",
        "unit": "ms",
        "default_baseline": 1.0,
        "default_max_y": 10.0,
        "format": lambda v: f"{v:.1f} ms" if v is not None else "—",
    },
    "jitter": {
        "title": "Delay Variation / Jitter",
        "y_axis_title": "Jitter (ms)",
        "unit": "ms",
        "default_baseline": 1.5,
        "default_max_y": 25.0,
        "format": lambda v: f"{v:.2f} ms" if v is not None else "—",
    },
    "loss": {
        "title": "Packet Loss",
        "y_axis_title": "Packet Loss (%)",
        "unit": "%",
        "default_baseline": 0.0,
        "default_max_y": 10.0,
        "format": lambda v: f"{v:.1f} %" if v is not None else "—",
    },
    "download": {
        "title": "Download Throughput",
        "y_axis_title": "Throughput (Mbps)",
        "unit": "Mbps",
        "default_baseline": 35.0,
        "default_max_y": 100.0,
        "format": lambda v: f"{v:.1f} Mbps" if v is not None else "—",
    },
    "upload": {
        "title": "Upload Throughput",
        "y_axis_title": "Throughput (Mbps)",
        "unit": "Mbps",
        "default_baseline": 15.0,
        "default_max_y": 50.0,
        "format": lambda v: f"{v:.1f} Mbps" if v is not None else "—",
    },
}


class TimeSeriesLineGraphWidget(QWidget):
    """Custom antialiased time-series line graph rendering Actual vs Baseline waveforms."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: transparent;")

        self.points: List[TimeSeriesPoint] = []
        self.active_metric_key: str = "latency"
        self.active_window: TimeWindow = TimeWindow.H24

        # Interactive hover state
        self._hover_idx: Optional[int] = None
        self._hover_pt_screen: Optional[QPointF] = None

        # Pixel coordinate cache for hover testing
        self._cached_actual_pts: List[Tuple[float, float, int]] = []  # (px, py, index)

    def set_data(
        self,
        points: List[TimeSeriesPoint],
        metric_key: str = "latency",
        window: TimeWindow = TimeWindow.H24,
    ) -> None:
        """Update chart with new chronological telemetry points and trigger repaint."""
        self.points = sorted(points, key=lambda p: p.timestamp_dt)
        self.active_metric_key = metric_key
        self.active_window = window
        self._hover_idx = None
        self._cached_actual_pts = []
        self.update()

    def set_metric(self, metric_key: str) -> None:
        """Switch active metric without replacing points list."""
        if metric_key != self.active_metric_key:
            self.active_metric_key = metric_key
            self._hover_idx = None
            self.update()

    def set_window(self, window: TimeWindow) -> None:
        """Switch active time window."""
        if window != self.active_window:
            self.active_window = window
            self.update()

    def mouseMoveEvent(self, event) -> None:
        """Track mouse hover and find nearest data point for interactive tooltip."""
        pos = event.position()
        mx = pos.x()
        my = pos.y()

        if not self._cached_actual_pts:
            self._hover_idx = None
            self.update()
            super().mouseMoveEvent(event)
            return

        pad_l = 54.0
        pad_r = 24.0
        pad_t = 20.0
        pad_b = 46.0
        w = float(self.width())
        h = float(self.height())
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        # Check if cursor is completely outside the plot area
        if mx < pad_l - 5.0 or mx > pad_l + plot_w + 5.0 or my < pad_t - 10.0 or my > pad_t + plot_h + 15.0:
            if self._hover_idx is not None:
                self._hover_idx = None
                self.update()
            super().mouseMoveEvent(event)
            return

        # Find closest point by Euclidean distance
        best_idx: Optional[int] = None
        min_dist: float = 999999.0

        for px, py, idx in self._cached_actual_pts:
            dist = math.hypot(px - mx, py - my)
            if dist < min_dist:
                min_dist = dist
                best_idx = idx

        # Snap if within 35px
        if min_dist <= 35.0 and best_idx is not None:
            if self._hover_idx != best_idx:
                self._hover_idx = best_idx
                self.update()
        else:
            if self._hover_idx is not None:
                self._hover_idx = None
                self.update()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        """Clear tooltip on mouse leave."""
        if self._hover_idx is not None:
            self._hover_idx = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        """Handle tap/click for mobile and touchscreen interactions."""
        self.mouseMoveEvent(event)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        """Render high-fidelity time-series chart with Actual and Baseline curves."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Background fill
        painter.fillRect(self.rect(), QColor("#ffffff"))

        w = float(self.width())
        h = float(self.height())

        cfg = METRIC_CONFIGS.get(self.active_metric_key, METRIC_CONFIGS["latency"])

        # Plot margins: generous left margin for Y-axis numbers & title, bottom for X-axis timestamps
        pad_l = 54.0
        pad_r = 24.0
        pad_t = 20.0
        pad_b = 46.0

        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        if plot_w <= 30.0 or plot_h <= 30.0:
            return

        # ---------------------------------------------------------------------
        # 1. Determine Y-Axis Scaling & Dynamic Ticks
        # ---------------------------------------------------------------------
        valid_actuals = [p.actual_value for p in self.points if p.actual_value is not None]
        valid_bases = [p.baseline_value for p in self.points if p.baseline_value is not None]
        all_vals = valid_actuals + valid_bases

        default_max = cfg["default_max_y"]
        observed_max = max(all_vals) if all_vals else default_max

        # Pick a clean maximum Y that is at least default_max
        if observed_max <= default_max:
            max_y = default_max
        else:
            # Round up to clean step
            step = 20.0 if default_max <= 50.0 else 50.0
            max_y = math.ceil((observed_max * 1.15) / step) * step

        # Generate 5-6 ticks (e.g. 0, 20, 40, 60, 80, 100 for latency)
        tick_count = 5
        y_ticks = [i * (max_y / tick_count) for i in range(tick_count + 1)]

        # Draw Horizontal Grid Lines & Y Ticks
        grid_pen = QPen(QColor("#f1f5f9"), 1.0, Qt.PenStyle.SolidLine)
        lbl_font = QFont()
        lbl_font.setPointSize(8)
        painter.setFont(lbl_font)

        for y_val in y_ticks:
            frac = y_val / max_y
            py = pad_t + plot_h - (frac * plot_h)

            painter.setPen(grid_pen)
            painter.drawLine(QPointF(pad_l, py), QPointF(pad_l + plot_w, py))

            # Y-axis numeric labels
            painter.setPen(QColor("#94a3b8"))
            val_str = f"{int(y_val)}" if y_val == int(y_val) else f"{y_val:.1f}"
            painter.drawText(
                QRectF(0.0, py - 8.0, pad_l - 10.0, 16.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                val_str,
            )

        # Draw Vertical Y-Axis Title (rotated -90 deg on left margin)
        title_font = QFont()
        title_font.setPointSize(8)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#64748b"))

        painter.save()
        painter.translate(14.0, pad_t + (plot_h / 2.0))
        painter.rotate(-90.0)
        painter.drawText(
            QRectF(-70.0, -8.0, 140.0, 16.0),
            Qt.AlignmentFlag.AlignCenter,
            cfg["y_axis_title"],
        )
        painter.restore()

        # ---------------------------------------------------------------------
        # 2. X-Axis Ticks & Labels Matching Reference Image
        # ---------------------------------------------------------------------
        # Reference image for 24 Hours: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00, 24:00
        x_tick_labels: List[Tuple[float, str]] = []  # (frac_x, label)

        if self.active_window == TimeWindow.H24:
            hours = [0, 4, 8, 12, 16, 20, 24]
            for h_val in hours:
                frac = h_val / 24.0
                label = f"{h_val:02d}:00"
                x_tick_labels.append((frac, label))
        elif self.active_window == TimeWindow.D7:
            for day_idx in range(7):
                frac = day_idx / 6.0 if day_idx < 6 else 1.0
                label = f"Day {day_idx + 1}"
                x_tick_labels.append((frac, label))
        else:  # D30 (30 Days)
            intervals = [0, 6, 12, 18, 24, 30]
            for d_val in intervals:
                frac = d_val / 30.0
                label = f"Day {d_val if d_val > 0 else 1}"
                x_tick_labels.append((frac, label))

        # Draw subtle vertical grid lines & X tick labels
        v_grid_pen = QPen(QColor("#f8fafc"), 1.0, Qt.PenStyle.SolidLine)
        lbl_font_x = QFont()
        lbl_font_x.setPointSize(8)
        painter.setFont(lbl_font_x)

        for frac, x_label in x_tick_labels:
            px = pad_l + (frac * plot_w)
            painter.setPen(v_grid_pen)
            painter.drawLine(QPointF(px, pad_t), QPointF(px, pad_t + plot_h))

            # Draw bottom tick mark and text
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(
                QRectF(px - 22.0, pad_t + plot_h + 6.0, 44.0, 16.0),
                Qt.AlignmentFlag.AlignCenter,
                x_label,
            )

        # Baseline axis rule at bottom (Y=0)
        base_line_pen = QPen(QColor("#e2e8f0"), 1.2, Qt.PenStyle.SolidLine)
        painter.setPen(base_line_pen)
        painter.drawLine(
            QPointF(pad_l, pad_t + plot_h),
            QPointF(pad_l + plot_w, pad_t + plot_h),
        )

        # ---------------------------------------------------------------------
        # 3. Empty State Handling
        # ---------------------------------------------------------------------
        if not self.points:
            empty_font = QFont()
            empty_font.setPointSize(9)
            empty_font.setBold(True)
            painter.setFont(empty_font)
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(
                QRectF(pad_l, pad_t + (plot_h / 2.0) - 15.0, plot_w, 30.0),
                Qt.AlignmentFlag.AlignCenter,
                "Awaiting Telemetry Data — Line graph updates automatically after test runs",
            )
            self._draw_legend(painter, w, h)
            return

        # ---------------------------------------------------------------------
        # 4. Map Points to Screen Coordinates
        # ---------------------------------------------------------------------
        # Calculate time span of data or window
        count = len(self.points)
        screen_pts_actual: List[QPointF] = []
        screen_pts_baseline: List[QPointF] = []
        self._cached_actual_pts = []

        # If 24H window, map points proportionally to time of day or relative span
        t_first = self.points[0].timestamp_dt
        t_last = self.points[-1].timestamp_dt
        time_span_sec = max(1.0, (t_last - t_first).total_seconds())

        for idx, pt in enumerate(self.points):
            # Calculate fractional X [0.0, 1.0]
            if count == 1:
                frac_x = 0.5
            elif self.active_window == TimeWindow.H24:
                # Fractional time within 24h span
                rel_sec = (pt.timestamp_dt - t_first).total_seconds()
                frac_x = min(1.0, max(0.0, rel_sec / max(1.0, min(86400.0, time_span_sec))))
                if time_span_sec < 60.0:  # If close timestamps, space evenly
                    frac_x = idx / max(1.0, count - 1)
            else:
                frac_x = idx / max(1.0, count - 1)

            px = pad_l + (frac_x * plot_w)

            # Map Actual Y
            if pt.actual_value is not None:
                val_y = max(0.0, min(max_y, pt.actual_value))
                py = pad_t + plot_h - ((val_y / max_y) * plot_h)
                screen_pts_actual.append(QPointF(px, py))
                self._cached_actual_pts.append((px, py, idx))

            # Map Baseline Y
            if pt.baseline_value is not None:
                base_y = max(0.0, min(max_y, pt.baseline_value))
                py_base = pad_t + plot_h - ((base_y / max_y) * plot_h)
                screen_pts_baseline.append(QPointF(px, py_base))

        # ---------------------------------------------------------------------
        # 5. Draw Translucent Gradient Area Fill Under Actual Curve
        # ---------------------------------------------------------------------
        if len(screen_pts_actual) >= 2:
            fill_path = QPainterPath()
            fill_path.moveTo(screen_pts_actual[0].x(), pad_t + plot_h)
            fill_path.lineTo(screen_pts_actual[0])
            for pt in screen_pts_actual[1:]:
                fill_path.lineTo(pt)
            fill_path.lineTo(screen_pts_actual[-1].x(), pad_t + plot_h)
            fill_path.closeSubpath()

            grad = QLinearGradient(QPointF(0, pad_t), QPointF(0, pad_t + plot_h))
            grad.setColorAt(0.0, QColor(59, 130, 246, 45))    # #3b82f6 translucent
            grad.setColorAt(0.7, QColor(59, 130, 246, 12))
            grad.setColorAt(1.0, QColor(59, 130, 246, 0))

            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(fill_path)

        # ---------------------------------------------------------------------
        # 6. Draw Baseline Curve (Purple Dashed Line #c084fc)
        # ---------------------------------------------------------------------
        if len(screen_pts_baseline) >= 2:
            base_pen = QPen(QColor("#c084fc"), 1.8, Qt.PenStyle.CustomDashLine)
            base_pen.setDashPattern([6, 4])
            painter.setPen(base_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            base_path = QPainterPath()
            base_path.moveTo(screen_pts_baseline[0])
            for pt in screen_pts_baseline[1:]:
                base_path.lineTo(pt)
            painter.drawPath(base_path)

        # ---------------------------------------------------------------------
        # 7. Draw Actual Curve (Vibrant Blue #3b82f6) & Data Point Markers
        # ---------------------------------------------------------------------
        if len(screen_pts_actual) >= 2:
            actual_pen = QPen(QColor("#3b82f6"), 2.2, Qt.PenStyle.SolidLine)
            painter.setPen(actual_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            actual_path = QPainterPath()
            actual_path.moveTo(screen_pts_actual[0])
            for pt in screen_pts_actual[1:]:
                actual_path.lineTo(pt)
            painter.drawPath(actual_path)

        # Draw Circular Markers on Actual Data Points
        painter.setBrush(QBrush(QColor("#3b82f6")))
        painter.setPen(QPen(QColor("#ffffff"), 1.2))
        for pt in screen_pts_actual:
            painter.drawEllipse(pt, 3.2, 3.2)

        # ---------------------------------------------------------------------
        # 8. Interactive Hover Halo & Tooltip Callout Matching Reference Image
        # ---------------------------------------------------------------------
        if self._hover_idx is not None and 0 <= self._hover_idx < len(self.points):
            hover_pt_data = self.points[self._hover_idx]

            # Find screen coordinate for this point
            matching = [p for p in self._cached_actual_pts if p[2] == self._hover_idx]
            if matching:
                hx, hy, _ = matching[0]

                # 1. Accentuated Blue Dot with Focus Halo
                painter.save()
                painter.setBrush(QBrush(QColor(37, 99, 235, 45)))  # Halo
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(hx, hy), 8.5, 8.5)

                painter.setBrush(QBrush(QColor("#2563eb")))
                painter.setPen(QPen(QColor("#ffffff"), 2.0))
                painter.drawEllipse(QPointF(hx, hy), 4.8, 4.8)
                painter.restore()

                # 2. Tooltip Card (Styled white card with pointer notch)
                tw = 136.0
                th = 58.0

                # Compute bubble top-left
                bx = hx - (tw / 2.0)
                by = hy - th - 10.0

                # Prevent overflow out of canvas bounds
                if bx < pad_l:
                    bx = pad_l
                elif bx + tw > w - pad_r:
                    bx = w - pad_r - tw

                is_flipped = False
                if by < 6.0:  # Flip below marker if too close to top
                    by = hy + 14.0
                    is_flipped = True

                # Draw drop shadow
                shadow_rect = QRectF(bx + 1.0, by + 1.5, tw, th)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(15, 23, 42, 18)))
                painter.drawRoundedRect(shadow_rect, 8.0, 8.0)

                # Draw Tooltip Card Body
                card_rect = QRectF(bx, by, tw, th)
                painter.setPen(QPen(QColor("#e2e8f0"), 1.0))
                painter.setBrush(QBrush(QColor("#ffffff")))
                painter.drawRoundedRect(card_rect, 8.0, 8.0)

                # Draw Pointer Notch Triangle
                notch_poly = QPolygonF()
                if not is_flipped:
                    notch_poly.append(QPointF(hx, hy - 2.0))
                    notch_poly.append(QPointF(hx - 5.0, by + th))
                    notch_poly.append(QPointF(hx + 5.0, by + th))
                else:
                    notch_poly.append(QPointF(hx, hy + 2.0))
                    notch_poly.append(QPointF(hx - 5.0, by))
                    notch_poly.append(QPointF(hx + 5.0, by))
                painter.drawPolygon(notch_poly)

                # Tooltip Text Contents
                # Line 1: Timestamp (bold dark)
                f_time = QFont()
                f_time.setPointSize(8)
                f_time.setBold(True)
                painter.setFont(f_time)
                painter.setPen(QColor("#0f172a"))
                painter.drawText(
                    QRectF(bx + 8.0, by + 6.0, tw - 16.0, 14.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    hover_pt_data.tooltip_time_str,
                )

                # Line 2: Actual Value
                f_val = QFont()
                f_val.setPointSize(8)
                painter.setFont(f_val)
                painter.setPen(QColor("#334155"))
                act_str = cfg["format"](hover_pt_data.actual_value)
                short_title = "Latency" if "latency" in self.active_metric_key else cfg["title"].split("/")[0].strip()
                painter.drawText(
                    QRectF(bx + 8.0, by + 22.0, tw - 16.0, 14.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{short_title}: {act_str}",
                )

                # Line 3: Baseline Value
                painter.setPen(QColor("#64748b"))
                base_str = cfg["format"](hover_pt_data.baseline_value)
                painter.drawText(
                    QRectF(bx + 8.0, by + 37.0, tw - 16.0, 14.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"Baseline: {base_str}",
                )

        # ---------------------------------------------------------------------
        # 9. Bottom-Right Legend Matching Reference Image
        # ---------------------------------------------------------------------
        self._draw_legend(painter, w, h)

    def _draw_legend(self, painter: QPainter, w: float, h: float) -> None:
        """Draw 'Actual' and 'Baseline' legend indicators at bottom-right."""
        leg_x = w - 160.0
        leg_y = h - 18.0

        leg_font = QFont()
        leg_font.setPointSize(8)
        painter.setFont(leg_font)

        # 1. Actual (Solid Blue Dot)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#3b82f6")))
        painter.drawEllipse(QPointF(leg_x, leg_y + 4.0), 3.5, 3.5)

        painter.setPen(QColor("#64748b"))
        painter.drawText(
            QRectF(leg_x + 8.0, leg_y - 3.0, 42.0, 14.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Actual",
        )

        # 2. Baseline (Dashed Purple Line)
        base_x = leg_x + 60.0
        dash_pen = QPen(QColor("#c084fc"), 1.8, Qt.PenStyle.CustomDashLine)
        dash_pen.setDashPattern([5, 3])
        painter.setPen(dash_pen)
        painter.drawLine(QPointF(base_x, leg_y + 4.0), QPointF(base_x + 14.0, leg_y + 4.0))

        painter.setPen(QColor("#64748b"))
        painter.drawText(
            QRectF(base_x + 20.0, leg_y - 3.0, 50.0, 14.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Baseline",
        )
