"""Radar / Spider Chart Widget for multi-metric network performance visualization.

Paints an elegant, antialiased 6-axis radar comparison between Current Network Profile
and Historical Baseline using QPainter, matching TicNet's premium design language.

Mathematical Normalization Methodology (0–100 Scale):
------------------------------------------------------
All metrics are mapped to their respective axis and normalized to a 0–100 performance score,
where HIGHER SCORES CONSISTENTLY MAP TO LARGER RADII (outer perimeter = 100% optimal).

1. Latency (ms) — LOWER IS BETTER (Inverted):
   - Physical range: [0 ms, 150 ms].
   - 0 ms = 100% score (optimal).
   - Baseline latency (35 ms) = ~78% score.

2. Delay Variation / Jitter (ms) — LOWER IS BETTER (Inverted):
   - Physical range: [0 ms, 20 ms].
   - 0.0 ms = 100% score (optimal).
   - Baseline jitter (1.5 ms) = ~93% score.

3. Packet Loss (%) — LOWER IS BETTER (Inverted):
   - Physical range: [0.0%, 5.0%].
   - 0.0% loss = 100% score (optimal score).

4. Download Throughput (Mbps) — HIGHER IS BETTER (Direct):
   - Physical range: [0 Mbps, capacity ceiling].
   - Scales linearly from 0 to capacity ceiling.
   - Unmeasured: neutrally matches baseline score with display label '—'.

5. Stability (%) — HIGHER IS BETTER (Direct):
   - Derived mathematically from transmission variance and packet loss.
   - 100% = optimal stability.

6. Recovery (%) — HIGHER IS BETTER (Direct):
   - Derived mathematically from baseline latency ratio and packet drop rates.
   - 100% = nominal recovery.

Consistent Scoring Rules:
Both Current Network Profile and Baseline use the identical centralized normalization rules.
Identical inputs produce identical plotted positions.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ticnet.monitoring.normalization import (
    METRIC_SPECS,
    ORDERED_METRIC_KEYS,
    MetricDirection,
    MetricSpecification,
    NormalizedMetric,
    RadarMetricNormalizer,
    safe_float,
    score_to_radius_factor,
    to_radar_score,
    toRadarScore,
)

# Backward-compatibility alias
_safe_float = safe_float


class RadarChartWidget(QWidget):
    """Custom painted 6-axis radar / spider chart comparing network profile against baseline."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(380, 310)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: transparent;")
        self.setMouseTracking(True)

        self.normalizer = RadarMetricNormalizer()

        # 6 Evaluation Axes: (Display Name, Unit, Default Reference)
        self.axes_config: List[Tuple[str, str, float]] = [
            ("Latency", "ms", 35.0),               # Axis 0 (top, -90 deg)
            ("Jitter", "ms", 1.5),                 # Axis 1 (top-right, -30 deg)
            ("Packet Loss", "%", 0.0),             # Axis 2 (bottom-right, +30 deg)
            ("Download Throughput", "Mbps", 35.0), # Axis 3 (bottom, +90 deg)
            ("Stability", "%", 100.0),             # Axis 4 (bottom-left, +150 deg)
            ("Recovery", "%", 100.0),              # Axis 5 (top-left, +210 deg)
        ]

        # Mathematically equidistant 6-axis angles starting at -90 deg (top)
        self.angles: List[float] = [-90.0, -30.0, 30.0, 90.0, 150.0, 210.0]

        # Display state
        self.has_data: bool = False
        self.current_metrics: List[NormalizedMetric] = []
        self.baseline_metrics: List[NormalizedMetric] = []
        self.current_labels: List[str] = ["—", "—", "—", "—", "—", "—"]
        self.baseline_labels: List[str] = ["35.0 ms", "1.5 ms", "0.0 %", "35.0 Mbps", "100.0 %", "100.0 %"]
        self.raw_current: Dict[str, Optional[float]] = {}
        self.raw_baseline: Dict[str, Optional[float]] = {}
        self._scores_curr: List[float] = [75.0, 75.0, 75.0, 75.0, 75.0, 75.0]
        self._scores_base: List[float] = [75.0, 75.0, 75.0, 75.0, 75.0, 75.0]
        self.current_radii: List[float] = [0.75, 0.75, 0.75, 0.75, 0.75, 0.75]
        self.baseline_radii: List[float] = [0.75, 0.75, 0.75, 0.75, 0.75, 0.75]

        # Geometry cache for hover hit-testing
        self._curr_points: List[QPointF] = []
        self._base_points: List[QPointF] = []
        self.focused_axis: Optional[int] = None

    def set_focused_axis(self, axis_idx: Optional[int]) -> None:
        """Set which metric axis should be accentuated / highlighted (or None for multi-metric overview)."""
        if self.focused_axis != axis_idx:
            self.focused_axis = axis_idx
            self.update()

    def update_metrics(
        self,
        current: Dict[str, Optional[float]],
        baseline: Dict[str, Optional[float]],
        has_data: bool = True,
    ) -> None:
        """Update radar chart data and trigger repaint.

        Recalculates normalized 0–100 performance scores for both Current Network Profile (pink)
        and Baseline (blue) polygons using centralized, consistent normalization rules.
        Higher scores consistently map to larger radii.

        Args:
            current: Dict with keys 'latency_ms', 'jitter_ms', 'packet_loss_pct', 'download_mbps'.
            baseline: Dict with baseline reference values.
            has_data: True if valid measurements exist.
        """
        self.has_data = has_data
        self.raw_current = current
        self.raw_baseline = baseline

        if not has_data:
            self.update()
            return

        # Centralized normalization ensuring identical rules for both profiles
        self.current_metrics = self.normalizer.normalize_profile(current, baseline, is_baseline=False)
        self.baseline_metrics = self.normalizer.normalize_profile(baseline, baseline, is_baseline=True)

        self.current_labels = [m.display_str for m in self.current_metrics]
        self.baseline_labels = [m.display_str for m in self.baseline_metrics]
        self._scores_curr = [m.score for m in self.current_metrics]
        self._scores_base = [m.score for m in self.baseline_metrics]
        self.current_radii = [m.radius_factor for m in self.current_metrics]
        self.baseline_radii = [m.radius_factor for m in self.baseline_metrics]

        self.update()

    def paintEvent(self, event) -> None:
        """Render antialiased radar grid, polygons, labels, and legend."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        cy = (h / 2.0) + 4.0

        # Mathematical radius: strictly bounded so labels have guaranteed breathing room and never clip
        radius = min(cx - 82.0, cy - 62.0, (h - cy) - 56.0)
        if radius < 50.0:
            radius = 50.0

        # 1. Subtle circular ambient background glow giving depth
        glow_grad = QRadialGradient(QPointF(cx, cy), radius * 1.15)
        glow_grad.setColorAt(0.0, QColor(241, 245, 249, 140))
        glow_grad.setColorAt(0.85, QColor(248, 250, 252, 90))
        glow_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(glow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), radius * 1.15, radius * 1.15)

        # 2. Concentric web grid (4 levels: 25%, 50%, 75%, 100%)
        levels = [0.25, 0.50, 0.75, 1.00]
        grid_pen = QPen(QColor("#e2e8f0"), 1.2, Qt.PenStyle.SolidLine)
        painter.setPen(grid_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for lvl in levels:
            lvl_r = radius * lvl
            poly = QPolygonF()
            for deg in self.angles:
                rad = math.radians(deg)
                poly.append(QPointF(cx + lvl_r * math.cos(rad), cy + lvl_r * math.sin(rad)))
            painter.drawPolygon(poly)

        # Subtle calibrated grid percentage labels along top spoke
        lvl_font = QFont()
        lvl_font.setPointSize(7)
        painter.setFont(lvl_font)
        painter.setPen(QColor("#94a3b8"))
        for lvl in [0.50, 0.75, 1.00]:
            lvl_y = cy - (radius * lvl)
            painter.drawText(
                QRectF(cx + 4.0, lvl_y - 7.0, 32.0, 14.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{int(lvl * 100)}%",
            )

        # 3. Draw radial spoke lines from center to outer vertices
        for idx, deg in enumerate(self.angles):
            rad = math.radians(deg)
            px = cx + radius * math.cos(rad)
            py = cy + radius * math.sin(rad)
            if self.focused_axis == idx:
                spoke_pen = QPen(QColor("#2563eb"), 2.0, Qt.PenStyle.SolidLine)
            else:
                spoke_pen = QPen(QColor("#cbd5e1"), 1.0, Qt.PenStyle.SolidLine)
            painter.setPen(spoke_pen)
            painter.drawLine(QPointF(cx, cy), QPointF(px, py))

        if not self.has_data:
            painter.setPen(QColor("#94a3b8"))
            f_empty = QFont()
            f_empty.setPointSize(10)
            f_empty.setBold(True)
            painter.setFont(f_empty)
            painter.drawText(
                QRectF(cx - 140, cy - 20, 280, 40),
                Qt.AlignmentFlag.AlignCenter,
                "Awaiting Monitoring Data\n(Radar active upon first test)",
            )
            return

        # 4. Draw Baseline Polygon (Blue #3b82f6)
        base_poly = QPolygonF()
        self._base_points = []
        for i, deg in enumerate(self.angles):
            rad = math.radians(deg)
            factor = min(1.0, max(0.0, float(self.baseline_radii[i])))
            r_val = radius * factor
            pt = QPointF(cx + r_val * math.cos(rad), cy + r_val * math.sin(rad))
            base_poly.append(pt)
            self._base_points.append(pt)

        base_brush = QBrush(QColor(59, 130, 246, 30))  # Translucent blue
        base_pen = QPen(QColor("#3b82f6"), 2.0, Qt.PenStyle.SolidLine)
        painter.setBrush(base_brush)
        painter.setPen(base_pen)
        painter.drawPolygon(base_poly)

        # Baseline vertex circular dots
        painter.setBrush(QBrush(QColor("#3b82f6")))
        painter.setPen(QPen(QColor("#ffffff"), 1.6))
        for pt in base_poly:
            painter.drawEllipse(pt, 3.8, 3.8)

        # 5. Draw Current Network Profile Polygon (Pink #ec4899)
        curr_poly = QPolygonF()
        self._curr_points = []
        for i, deg in enumerate(self.angles):
            rad = math.radians(deg)
            factor = min(1.0, max(0.0, float(self.current_radii[i])))
            r_val = radius * factor
            pt = QPointF(cx + r_val * math.cos(rad), cy + r_val * math.sin(rad))
            curr_poly.append(pt)
            self._curr_points.append(pt)

        curr_brush = QBrush(QColor(236, 72, 153, 38))  # Translucent pink
        curr_pen = QPen(QColor("#ec4899"), 2.4, Qt.PenStyle.SolidLine)
        painter.setBrush(curr_brush)
        painter.setPen(curr_pen)
        painter.drawPolygon(curr_poly)

        # Current vertex circular dots with focus halo
        painter.setBrush(QBrush(QColor("#ec4899")))
        painter.setPen(QPen(QColor("#ffffff"), 1.8))
        for idx, pt in enumerate(curr_poly):
            if self.focused_axis == idx:
                painter.save()
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#2563eb"), 2.2))
                painter.drawEllipse(pt, 7.5, 7.5)
                painter.restore()
            painter.drawEllipse(pt, 4.4, 4.4)

        # 6. Mathematically Aligned Axis Labels (Zero Hardcoding)
        label_font = QFont()
        label_font.setPointSize(9)
        label_font.setWeight(QFont.Weight.Normal)

        val_font = QFont()
        val_font.setPointSize(9)
        val_font.setBold(True)

        for i, (name, unit, _) in enumerate(self.axes_config):
            deg = self.angles[i]
            rad = math.radians(deg)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)

            # Mathematical anchor distance outside the 1.00 outer grid line
            pad = 15.0
            lx = cx + (radius + pad) * cos_a
            ly = cy + (radius + pad) * sin_a

            val_str = self.current_labels[i]

            bw = 130.0
            bh = 32.0

            # Mathematical anchor alignment based on trigonometry
            if cos_a > 0.25:
                # Right hemisphere (Jitter, Packet Loss): anchor left
                bx = lx + 4.0
                align_flag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            elif cos_a < -0.25:
                # Left hemisphere (Stability, Recovery): anchor right
                bx = lx - bw - 4.0
                align_flag = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            else:
                # Vertical polar axes (Latency at top, Download at bottom): center horizontally
                bx = lx - (bw / 2.0)
                align_flag = Qt.AlignmentFlag.AlignCenter

            if sin_a < -0.4:
                # Top polar axis (Latency): anchor above vertex
                by = ly - bh + 2.0
            elif sin_a > 0.4:
                # Bottom polar axis (Throughput): anchor below vertex
                by = ly - 2.0
            else:
                # Side vertices: vertically centered
                by = ly - (bh / 2.0)

            is_focused = (self.focused_axis == i)

            # Draw Title
            painter.setFont(val_font if is_focused else label_font)
            painter.setPen(QColor("#2563eb") if is_focused else QColor("#64748b"))
            painter.drawText(QRectF(bx, by, bw, 15), align_flag, name)

            # Draw Value
            painter.setFont(val_font)
            painter.setPen(QColor("#1d4ed8") if is_focused else QColor("#0f172a"))
            painter.drawText(QRectF(bx, by + 15, bw, 17), align_flag, val_str)

        # 7. Legend at Bottom-Right
        leg_x = max(cx + 60.0, w - 145.0)
        leg_y = cy + radius - 6.0

        painter.setFont(label_font)

        # Pink Dot: Current Network Profile
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#ec4899")))
        painter.drawEllipse(QPointF(leg_x, leg_y), 4.0, 4.0)
        painter.setPen(QColor("#64748b"))
        painter.drawText(
            QRectF(leg_x + 9, leg_y - 8, 134, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Current Network Profile",
        )

        # Blue Dot: Baseline
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#3b82f6")))
        painter.drawEllipse(QPointF(leg_x, leg_y + 16), 4.0, 4.0)
        painter.setPen(QColor("#64748b"))
        painter.drawText(
            QRectF(leg_x + 9, leg_y + 8, 134, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Baseline",
        )

    def mouseMoveEvent(self, event) -> None:
        """Handle vertex hover to display detailed tooltips with metrics and scores."""
        pos = event.position()
        tooltip_shown = False

        if self.has_data and self._curr_points and self.current_metrics:
            for idx, pt in enumerate(self._curr_points):
                dist = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
                if dist < 14.0:
                    curr = self.current_metrics[idx]
                    base = self.baseline_metrics[idx] if idx < len(self.baseline_metrics) else None
                    base_str = base.display_str if base else "—"
                    score_pct = int(round(curr.score))
                    base_pct = int(round(base.score)) if base else 75

                    if not curr.is_measured and curr.key == "download":
                        tip = (
                            f"<b>{curr.display_name}</b><br>"
                            f"Status: <b>Not Measured (ping-only test)</b><br>"
                            f"Baseline Target: {base_str}"
                        )
                    else:
                        tip = (
                            f"<b>{curr.display_name}</b><br>"
                            f"Current Measured: <b>{curr.display_str}</b><br>"
                            f"Baseline Target: {base_str}<br>"
                            f"Performance Score: <b>{score_pct}%</b> (Baseline: {base_pct}%)"
                        )
                    QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                    tooltip_shown = True
                    break

        if not tooltip_shown and self.has_data and self._base_points and self.baseline_metrics:
            for idx, pt in enumerate(self._base_points):
                dist = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
                if dist < 14.0:
                    base = self.baseline_metrics[idx]
                    base_pct = int(round(base.score))
                    tip = (
                        f"<b>{base.display_name} (Baseline Reference)</b><br>"
                        f"Target: <b>{base.display_str}</b><br>"
                        f"Baseline Score: <b>{base_pct}%</b>"
                    )
                    QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                    tooltip_shown = True
                    break

        super().mouseMoveEvent(event)
