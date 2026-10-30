"""Modern Reference Widgets for TicNet Results & Evidence Page.

Implements the pixel-accurate reference design:
1. ResultsHeaderWidget (Title, Subtitle, Run ID badge, Timestamp badge, Copy Report button).
2. FindingBannerWidget (Warm amber root-cause banner with rule ID, evidence strength, explanation, limits, and View Details action).
3. LatencyOverviewChartWidget (Interactive 4-target multi-line latency chart with 1h/Day/Week/Month filters, tooltips, and legend).
4. KeyMetricsWidget (2x2 grid: Gateway, External Latency, DNS Resolution, Download Throughput).
5. LatencyBufferbloatDetailsWidget (Loaded vs Baseline stats, delta, comparison bars, dynamic interpretation).
6. CompactEvidenceMatrixWidget (Measurement, Status, Interpretation table with View All action).
7. BufferbloatComparisonWidget (Median delay, rating pill, 0-50ms continuous scale bar, and lightbulb callout card).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens

try:
    from PySide6.QtCharts import (
        QBarCategoryAxis,
        QBarSeries,
        QBarSet,
        QChart,
        QChartView,
        QValueAxis,
    )
    QT_CHARTS_AVAILABLE = True
except ImportError:
    QT_CHARTS_AVAILABLE = False


# -----------------------------------------------------------------------------
# Color Constants & Palette
# -----------------------------------------------------------------------------
COLOR_BG_PAGE = "#f0f4fa"
COLOR_BG_CARD = "#ffffff"
COLOR_BORDER = "#e2e8f0"
COLOR_TEXT_MAIN = "#0f172a"
COLOR_TEXT_MUTED = "#64748b"

COLOR_AMBER_BG = "#fef9ee"
COLOR_AMBER_BORDER = "#fde68a"
COLOR_AMBER_TEXT = "#92400e"
COLOR_AMBER_ICON = "#f59e0b"

COLOR_GREEN_BG = "#ecfdf5"
COLOR_GREEN_BORDER = "#a7f3d0"
COLOR_GREEN_TEXT = "#059669"

COLOR_BLUE_PRIMARY = "#2563eb"
COLOR_PURPLE_BG = "#f5f3ff"
COLOR_PURPLE_BORDER = "#ddd6fe"
COLOR_PURPLE_TEXT = "#6b21a8"

# Target series line colors & dot colors matching user reference graph
TARGET_COLORS = {
    "gw": "#362f2d",          # Dark charcoal brown: Local Gateway
    "cloudflare": "#c89666",  # Caramel / tan / orange: Cloudflare (1.1.1.1)
    "google": "#debfae",      # Peach / light beige: Google DNS (8.8.8.8)
    "quad9": "#8b5cf6",       # Purple: Quad9 (9.9.9.9)
}

TARGET_DOT_COLORS = {
    "gw": "#2a2c21",
    "cloudflare": "#ae8b64",
    "google": "#c2c1bf",
    "quad9": "#7c3aed",
}


# -----------------------------------------------------------------------------
# 1. Header Widget
# -----------------------------------------------------------------------------
class ResultsHeaderWidget(QWidget):
    """Header displaying page title, subtitle, Run ID badge, Timestamp, and Copy Report button."""

    sig_copy_report = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(12)

        # Title & Subtitle
        title_col = QVBoxLayout()
        title_col.setSpacing(3)

        self.lbl_title = QLabel("Results & Evidence", self)
        self.lbl_title.setStyleSheet("font-size: 20px; font-weight: 800; color: #0f172a;")

        self.lbl_subtitle = QLabel(
            "Deterministic root-cause findings backed by measured telemetry", self
        )
        self.lbl_subtitle.setStyleSheet("font-size: 12px; font-weight: 500; color: #64748b;")

        title_col.addWidget(self.lbl_title)
        title_col.addWidget(self.lbl_subtitle)
        layout.addLayout(title_col)
        layout.addStretch()

        # Badges Row
        # 1. Run ID Badge
        self.badge_run = QFrame(self)
        self.badge_run.setStyleSheet(
            "background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 4px 10px;"
        )
        b_run_layout = QHBoxLayout(self.badge_run)
        b_run_layout.setContentsMargins(4, 2, 4, 2)
        b_run_layout.setSpacing(6)

        icon_check = QLabel("✔", self.badge_run)
        icon_check.setStyleSheet(
            "color: #ffffff; background-color: #10b981; font-size: 9px; font-weight: 800; border-radius: 7px; padding: 1px 3px;"
        )
        b_run_layout.addWidget(icon_check)

        self.lbl_run_id = QLabel("Run ID: —", self.badge_run)
        self.lbl_run_id.setStyleSheet("font-size: 11px; font-weight: 700; color: #0f172a; border: none;")
        b_run_layout.addWidget(self.lbl_run_id)
        layout.addWidget(self.badge_run)

        # 2. Timestamp Badge
        self.badge_time = QFrame(self)
        self.badge_time.setStyleSheet(
            "background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 4px 10px;"
        )
        b_time_layout = QHBoxLayout(self.badge_time)
        b_time_layout.setContentsMargins(4, 2, 4, 2)
        b_time_layout.setSpacing(6)

        icon_cal = QLabel("📅", self.badge_time)
        icon_cal.setStyleSheet("font-size: 11px; border: none;")
        b_time_layout.addWidget(icon_cal)

        self.lbl_time = QLabel("—", self.badge_time)
        self.lbl_time.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569; border: none;")
        b_time_layout.addWidget(self.lbl_time)
        layout.addWidget(self.badge_time)

        # 3. Copy Report Button
        self.btn_copy = QPushButton("📋  Copy Report", self)
        self.btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 11px;
                font-weight: 700;
                color: #0f172a;
            }
            QPushButton:hover {
                background-color: #f8fafc;
                border-color: #94a3b8;
            }
            QPushButton:pressed {
                background-color: #f1f5f9;
            }
        """)
        self.btn_copy.clicked.connect(self._on_copy_clicked)
        layout.addWidget(self.btn_copy)

    def set_run_metadata(self, run_id: str, timestamp_str: str) -> None:
        self.lbl_run_id.setText(f"Run ID: {run_id}")
        self.lbl_time.setText(timestamp_str)

    def _on_copy_clicked(self) -> None:
        self.sig_copy_report.emit()
        self.btn_copy.setText("✔  Copied!")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self.btn_copy.setText("📋  Copy Report"))


# -----------------------------------------------------------------------------
# 2. Root Cause Finding Banner
# -----------------------------------------------------------------------------
class FindingBannerWidget(QFrame):
    """Warm amber banner card displaying the diagnostic root-cause finding."""

    sig_view_details = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("findingBanner")
        self.setStyleSheet("""
            #findingBanner {
                background-color: #fef9ee;
                border: 1px solid #fde68a;
                border-radius: 12px;
            }
        """)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        # Warning Icon
        self.lbl_icon = QLabel("⚠️", self)
        self.lbl_icon.setStyleSheet("font-size: 26px; border: none; color: #f59e0b;")
        layout.addWidget(self.lbl_icon)

        # Text column
        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        # Top row: Title | Rule | Strength
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self.lbl_title = QLabel("Possible bufferbloat under load", self)
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: 800; color: #1e293b; border: none;")
        header_row.addWidget(self.lbl_title)

        sep1 = QLabel("|", self)
        sep1.setStyleSheet("color: #cbd5e1; font-weight: 300;")
        header_row.addWidget(sep1)

        self.lbl_rule = QLabel("Rule: R-LOAD-01", self)
        self.lbl_rule.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569; border: none;")
        header_row.addWidget(self.lbl_rule)

        sep2 = QLabel("|", self)
        sep2.setStyleSheet("color: #cbd5e1; font-weight: 300;")
        header_row.addWidget(sep2)

        self.lbl_strength = QLabel("Evidence Strength: Moderate", self)
        self.lbl_strength.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569; border: none;")
        header_row.addWidget(self.lbl_strength)
        header_row.addStretch()

        text_col.addLayout(header_row)

        # Explanation
        self.lbl_explanation = QLabel(
            "Latency can rise when downloads or uploads saturate the connection.", self
        )
        self.lbl_explanation.setStyleSheet("font-size: 11px; font-weight: 500; color: #334155; border: none;")
        self.lbl_explanation.setWordWrap(True)
        text_col.addWidget(self.lbl_explanation)

        # Limitations / Disclaimer
        self.lbl_limits = QLabel(
            "ℹ What this does not prove: The test cannot locate buffering definitively inside the router versus ISP access equipment.",
            self,
        )
        self.lbl_limits.setStyleSheet("font-size: 10px; font-weight: 500; color: #64748b; border: none;")
        self.lbl_limits.setWordWrap(True)
        text_col.addWidget(self.lbl_limits)

        layout.addLayout(text_col, stretch=1)

        # View Details Action Button
        self.btn_details = QPushButton("View Details  →", self)
        self.btn_details.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_details.setStyleSheet("""
            QPushButton {
                background-color: #fef08a;
                border: 1px solid #fde047;
                border-radius: 12px;
                padding: 6px 14px;
                font-size: 11px;
                font-weight: 700;
                color: #854d0e;
            }
            QPushButton:hover {
                background-color: #fde047;
            }
            QPushButton:pressed {
                background-color: #facc15;
            }
        """)
        self.btn_details.clicked.connect(self.sig_view_details.emit)
        layout.addWidget(self.btn_details)

    def set_finding(
        self,
        title: str,
        rule_id: str,
        strength: str,
        explanation: str,
        limits: Optional[str] = None,
        is_healthy: bool = False,
    ) -> None:
        self.lbl_title.setText(title)
        self.lbl_rule.setText(f"Rule: {rule_id}" if rule_id else "")
        self.lbl_strength.setText(f"Evidence Strength: {strength}" if strength else "")
        self.lbl_explanation.setText(explanation)
        if limits and limits != "None":
            self.lbl_limits.setText(f"ℹ What this does not prove: {limits}")
            self.lbl_limits.setVisible(True)
        else:
            self.lbl_limits.setVisible(False)

        if is_healthy:
            self.setStyleSheet("""
                #findingBanner {
                    background-color: #ecfdf5;
                    border: 1px solid #a7f3d0;
                    border-radius: 12px;
                }
            """)
            self.lbl_icon.setText("✔")
            self.lbl_icon.setStyleSheet("font-size: 24px; color: #10b981; border: none;")
            self.btn_details.setStyleSheet("""
                QPushButton {
                    background-color: #d1fae5;
                    border: 1px solid #a7f3d0;
                    border-radius: 12px;
                    padding: 6px 14px;
                    font-size: 11px;
                    font-weight: 700;
                    color: #065f46;
                }
                QPushButton:hover { background-color: #a7f3d0; }
            """)
        else:
            self.setStyleSheet("""
                #findingBanner {
                    background-color: #fef9ee;
                    border: 1px solid #fde68a;
                    border-radius: 12px;
                }
            """)
            self.lbl_icon.setText("⚠️")
            self.lbl_icon.setStyleSheet("font-size: 26px; color: #f59e0b; border: none;")


# -----------------------------------------------------------------------------
# 3. Interactive Multi-Line Latency Overview Chart
# -----------------------------------------------------------------------------
class LatencyOverviewCanvas(QWidget):
    """Custom painted time-series line chart rendering multi-target latency probes."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.series_data: Dict[str, List[Tuple[float, float, str]]] = {}  # key -> list of (x_sample, y_ms, timestamp)
        self._hover_pt: Optional[Tuple[float, float, str, str]] = None  # (x, y, text, target_name)
        self.x_title: str = "Probe Sequence Sample"
        self.x_labels: Optional[List[str]] = None

    def set_data(
        self,
        series: Dict[str, List[Tuple[float, float, str]]],
        x_title: str = "Probe Sequence Sample",
        x_labels: Optional[List[str]] = None,
    ) -> None:
        self.series_data = series
        self.x_title = x_title
        self.x_labels = x_labels
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # Layout Margins: left for Y axis, bottom for X axis, right for breathing room
        pad_l = 46.0
        pad_r = 18.0
        pad_t = 16.0
        pad_b = 32.0

        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        if plot_w <= 20 or plot_h <= 20:
            return

        # Determine Max Y (at least 40 ms as in reference image)
        max_y = 40.0
        for pts in self.series_data.values():
            for _, y, _ in pts:
                if y > max_y:
                    max_y = max(max_y, y * 1.15)
        # Round max_y up to nice multiple of 10
        max_y = math.ceil(max_y / 10.0) * 10.0

        # Max X samples (1 to 7 by default to match reference graph)
        max_x = 7.0
        for pts in self.series_data.values():
            if pts:
                max_x = max(max_x, max(p[0] for p in pts))

        # 1. Subtle Horizontal Grid Lines & Y Axis Ticks (0, 10, 20, 30, 40)
        y_ticks = [0.0, 10.0, 20.0, 30.0, 40.0] if max_y <= 45.0 else [0.0, max_y * 0.25, max_y * 0.5, max_y * 0.75, max_y]
        grid_pen = QPen(QColor("#e2e8f0"), 1.0, Qt.PenStyle.SolidLine)
        lbl_font = QFont("Segoe UI", 7)
        painter.setFont(lbl_font)
        painter.setPen(QColor("#94a3b8"))

        for y_val in y_ticks:
            frac = y_val / max_y
            py = pad_t + plot_h - (frac * plot_h)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(pad_l - 12, py), QPointF(pad_l + plot_w + 12, py))
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(QRectF(0, py - 7, pad_l - 16, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{int(y_val)}")

        # 2. X Axis Ticks & Labels (Clean ticks at bottom, matching reference without vertical line clutter)
        x_ticks = list(range(1, int(max_x) + 1))
        tick_pen = QPen(QColor("#cbd5e1"), 1.0, Qt.PenStyle.SolidLine)
        for idx, x_val in enumerate(x_ticks):
            frac = (x_val - 1) / max(1.0, (max_x - 1))
            px = pad_l + (frac * plot_w)
            painter.setPen(tick_pen)
            painter.drawLine(QPointF(px, pad_t + plot_h), QPointF(px, pad_t + plot_h + 3))
            painter.setPen(QColor("#94a3b8"))
            lbl_text = (
                self.x_labels[idx]
                if self.x_labels and idx < len(self.x_labels)
                else str(x_val)
            )
            painter.drawText(
                QRectF(px - 28, pad_t + plot_h + 4, 56, 14),
                Qt.AlignmentFlag.AlignCenter,
                lbl_text,
            )

        # 3. Axis Titles
        title_font = QFont("Segoe UI", 7, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor("#64748b"))

        # Y Axis Title (Vertical on Left)
        painter.save()
        painter.translate(11, pad_t + (plot_h / 2.0))
        painter.rotate(-90)
        painter.drawText(QRectF(-60, -7, 120, 14), Qt.AlignmentFlag.AlignCenter, "Round-Trip Delay (ms)")
        painter.restore()

        # X Axis Title (Bottom Center)
        painter.drawText(QRectF(pad_l, h - 14, plot_w, 14), Qt.AlignmentFlag.AlignCenter, self.x_title)

        # 4. Draw Lines & Points in structured layering order (Peach -> Caramel -> Dark)
        layer_order = ["quad9", "google", "cloudflare", "gw"]
        ordered_keys = [k for k in layer_order if k in self.series_data]
        for k in self.series_data:
            if k not in ordered_keys:
                ordered_keys.append(k)

        for key in ordered_keys:
            pts = self.series_data.get(key, [])
            if not pts:
                continue
            color_hex = TARGET_COLORS.get(key, "#362f2d")
            dot_hex = TARGET_DOT_COLORS.get(key, color_hex)
            line_pen = QPen(QColor(color_hex), 1.8, Qt.PenStyle.SolidLine)
            painter.setPen(line_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            screen_pts = []
            for idx, (x_val, y_val, t_str) in enumerate(pts):
                x_frac = (x_val - 1) / max(1.0, (max_x - 1))
                y_frac = y_val / max_y
                px = pad_l + (x_frac * plot_w)
                py = pad_t + plot_h - (y_frac * plot_h)
                screen_pts.append(QPointF(px, py))

            if screen_pts:
                # Flat lead-in extending to left margin boundary
                p1 = screen_pts[0]
                path.moveTo(pad_l - 12, p1.y())
                path.lineTo(p1.x(), p1.y())

                # Line segments through sample points
                for pt in screen_pts[1:]:
                    path.lineTo(pt.x(), pt.y())

                # Flat lead-out extending to right margin boundary
                p_last = screen_pts[-1]
                path.lineTo(pad_l + plot_w + 12, p_last.y())

                painter.drawPath(path)

                # Solid filled circular vertex dots
                painter.setBrush(QBrush(QColor(dot_hex)))
                painter.setPen(QPen(QColor(color_hex), 0.8))
                for pt in screen_pts:
                    painter.drawEllipse(pt, 3.3, 3.3)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        w = float(self.width())
        h = float(self.height())
        pad_l = 46.0
        pad_r = 18.0
        pad_t = 16.0
        pad_b = 32.0
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        max_y = 40.0
        for pts in self.series_data.values():
            for _, y, _ in pts:
                if y > max_y:
                    max_y = max(max_y, y * 1.15)
        max_y = math.ceil(max_y / 10.0) * 10.0

        max_x = 10.0
        for pts in self.series_data.values():
            if pts:
                max_x = max(max_x, max(p[0] for p in pts))

        # Check proximity to any plotted point
        found = False
        target_labels = {
            "gw": "Local Gateway (192.168.0.1)",
            "cloudflare": "Cloudflare (1.1.1.1)",
            "google": "Google DNS (8.8.8.8)",
            "quad9": "Quad9 (9.9.9.9)",
        }

        for key, pts in self.series_data.items():
            for x_val, y_val, t_str in pts:
                x_frac = (x_val - 1) / max(1.0, (max_x - 1))
                y_frac = y_val / max_y
                px = pad_l + (x_frac * plot_w)
                py = pad_t + plot_h - (y_frac * plot_h)
                dist = math.hypot(pos.x() - px, pos.y() - py)
                if dist < 10.0:
                    name = target_labels.get(key, key)
                    tip = f"<b>{name}</b><br>Latency: <b>{y_val:.1f} ms</b>"
                    if t_str:
                        prefix = "Time" if ":" in t_str else "Date"
                        tip += f"<br>{prefix}: {t_str}"
                    else:
                        tip += f"<br>Sample #{int(x_val)}"
                    QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                    found = True
                    break
            if found:
                break

        if not found:
            QToolTip.hideText()
        super().mouseMoveEvent(event)


class LatencyOverviewChartWidget(QFrame):
    """Card containing header, time filters (1h/Day/Week/Month), Canvas, and Legend."""

    sig_filter_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
        """)
        self._active_filter = "Day"
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        # Header Row: Title & Filter Pills
        h_row = QHBoxLayout()
        h_row.setSpacing(8)

        icon = QLabel("📈", self)
        icon.setStyleSheet("font-size: 15px; border: none;")
        h_row.addWidget(icon)

        title = QLabel("Round-trip Latency Overview", self)
        title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a; border: none;")
        h_row.addWidget(title)
        h_row.addStretch()

        # Filter Pills: 1h, Day, Week, Month
        self.btn_filters: Dict[str, QPushButton] = {}
        filters_container = QWidget(self)
        filters_container.setStyleSheet("border: none; background: transparent;")
        f_layout = QHBoxLayout(filters_container)
        f_layout.setContentsMargins(0, 0, 0, 0)
        f_layout.setSpacing(4)

        for f_label in ["1h", "Day", "Week", "Month"]:
            btn = QPushButton(f_label, filters_container)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda checked=False, fl=f_label: self._set_active_filter(fl))
            self.btn_filters[f_label] = btn
            f_layout.addWidget(btn)

        h_row.addWidget(filters_container)
        layout.addLayout(h_row)

        self._update_filter_styles()

        # Canvas Line Chart
        self.canvas = LatencyOverviewCanvas(self)
        layout.addWidget(self.canvas)

        # Legend Row at Bottom
        leg_row = QHBoxLayout()
        leg_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        leg_row.setSpacing(16)

        legend_items = [
            ("Local Gateway (192.168.0.1)", TARGET_COLORS["gw"]),
            ("Cloudflare (1.1.1.1)", TARGET_COLORS["cloudflare"]),
            ("Google DNS (8.8.8.8)", TARGET_COLORS["google"]),
        ]

        for name, color in legend_items:
            item_widget = QWidget(self)
            item_widget.setStyleSheet("border: none; background: transparent;")
            i_layout = QHBoxLayout(item_widget)
            i_layout.setContentsMargins(0, 0, 0, 0)
            i_layout.setSpacing(5)

            dot = QLabel("●", item_widget)
            dot.setStyleSheet(f"color: {color}; font-size: 12px; border: none;")
            i_layout.addWidget(dot)

            lbl = QLabel(name, item_widget)
            lbl.setStyleSheet("font-size: 10px; font-weight: 600; color: #475569; border: none;")
            i_layout.addWidget(lbl)

            leg_row.addWidget(item_widget)

        layout.addLayout(leg_row)

    def _set_active_filter(self, filter_name: str) -> None:
        self._active_filter = filter_name
        self._update_filter_styles()
        self.sig_filter_changed.emit(filter_name)

    def _update_filter_styles(self) -> None:
        for f_label, btn in self.btn_filters.items():
            if f_label == self._active_filter:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #0f172a;
                        color: #ffffff;
                        border: none;
                        border-radius: 11px;
                        padding: 3px 10px;
                        font-size: 10px;
                        font-weight: 700;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #f1f5f9;
                        color: #475569;
                        border: none;
                        border-radius: 11px;
                        padding: 3px 10px;
                        font-size: 10px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: #e2e8f0;
                    }
                """)

    def load_telemetry(
        self,
        series: Dict[str, List[Tuple[float, float, str]]],
        x_title: str = "Probe Sequence Sample",
        x_labels: Optional[List[str]] = None,
    ) -> None:
        self.canvas.set_data(series, x_title=x_title, x_labels=x_labels)


# -----------------------------------------------------------------------------
# 4. Key Metrics 2x2 Grid Widget
# -----------------------------------------------------------------------------
class KeyMetricCell(QFrame):
    """Individual rounded card cell inside the 2x2 Key Metrics grid."""

    def __init__(
        self,
        icon_symbol: str,
        icon_bg: str,
        icon_fg: str,
        title: str,
        value: str,
        status_text: str,
        status_bg: str = "#ecfdf5",
        status_fg: str = "#059669",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 10px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        # Icon box
        self.icon_box = QLabel(icon_symbol, self)
        self.icon_box.setStyleSheet(f"""
            background-color: {icon_bg};
            color: {icon_fg};
            font-size: 14px;
            border-radius: 6px;
            padding: 4px;
            border: none;
        """)
        self.icon_box.setFixedSize(28, 28)
        self.icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.icon_box)

        # Title & Value
        info_col = QVBoxLayout()
        info_col.setSpacing(1)
        self.lbl_title = QLabel(title, self)
        self.lbl_title.setStyleSheet("font-size: 10px; font-weight: 600; color: #64748b; border: none;")
        info_col.addWidget(self.lbl_title)

        self.lbl_val = QLabel(value, self)
        self.lbl_val.setStyleSheet("font-size: 16px; font-weight: 800; color: #0f172a; border: none;")
        info_col.addWidget(self.lbl_val)
        top_row.addLayout(info_col)
        top_row.addStretch()

        layout.addLayout(top_row)

        # Status Pill at bottom
        self.lbl_status = QLabel(status_text, self)
        self.lbl_status.setStyleSheet(f"""
            background-color: {status_bg};
            color: {status_fg};
            font-size: 9px;
            font-weight: 700;
            border-radius: 8px;
            padding: 2px 6px;
            border: none;
        """)
        self.lbl_status.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.lbl_status)

    def set_value(self, value: str, status_text: str, status_bg: str = "#ecfdf5", status_fg: str = "#059669") -> None:
        self.lbl_val.setText(value)
        self.lbl_status.setText(status_text)
        self.lbl_status.setStyleSheet(f"""
            background-color: {status_bg};
            color: {status_fg};
            font-size: 9px;
            font-weight: 700;
            border-radius: 8px;
            padding: 2px 6px;
            border: none;
        """)


class KeyMetricsWidget(QFrame):
    """Card containing 2x2 grid of Key Network Metrics."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
        """)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Header
        h_row = QHBoxLayout()
        h_row.setSpacing(8)

        icon = QLabel("📊", self)
        icon.setStyleSheet("font-size: 15px; border: none;")
        h_row.addWidget(icon)

        title = QLabel("Key Metrics", self)
        title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a; border: none;")
        h_row.addWidget(title)
        h_row.addStretch()
        layout.addLayout(h_row)

        # 2x2 Grid
        grid = QGridLayout()
        grid.setSpacing(10)

        # 1. Gateway
        self.cell_gw = KeyMetricCell(
            icon_symbol="🖧",
            icon_bg="#e0f2fe",
            icon_fg="#0284c7",
            title="Gateway",
            value="1.0 ms",
            status_text="✔ Healthy",
            parent=self,
        )
        grid.addWidget(self.cell_gw, 0, 0)

        # 2. External Latency
        self.cell_ext = KeyMetricCell(
            icon_symbol="🌐",
            icon_bg="#ffedd5",
            icon_fg="#ea580c",
            title="External Latency",
            value="28.1 ms",
            status_text="✔ Baseline Clean",
            parent=self,
        )
        grid.addWidget(self.cell_ext, 0, 1)

        # 3. DNS Resolution
        self.cell_dns = KeyMetricCell(
            icon_symbol="🗄️",
            icon_bg="#f3e8ff",
            icon_fg="#9333ea",
            title="DNS Resolution",
            value="32.3 ms",
            status_text="✔ 6 of 6 Resolved",
            parent=self,
        )
        grid.addWidget(self.cell_dns, 1, 0)

        # 4. Download Throughput
        self.cell_dl = KeyMetricCell(
            icon_symbol="📥",
            icon_bg="#dcfce7",
            icon_fg="#16a34a",
            title="Download Throughput",
            value="93.4 Mbps",
            status_text="Measured Load",
            status_bg="#f0fdf4",
            status_fg="#16a34a",
            parent=self,
        )
        grid.addWidget(self.cell_dl, 1, 1)

        layout.addLayout(grid)


# -----------------------------------------------------------------------------
# 5. Latency & Bufferbloat Details Widget
# -----------------------------------------------------------------------------
class LatencyBufferbloatDetailsWidget(QWidget):
    """Bar chart widget comparing baseline unloaded latency versus loaded latency (Bufferbloat).

    Pixel-accurately replicates the reference design:
    - Top header box: 'Bufferbloat Latency Inflation (Loaded vs Baseline):'
    - Center card: QChart bar chart (Unloaded Baseline vs Under Downstream Load)
    - Bottom footer: Baseline, Loaded, Delta metadata strip.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)

        # 1. Top Header Box
        self.header_frame = QFrame(self)
        self.header_frame.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }
        """)
        h_layout = QHBoxLayout(self.header_frame)
        h_layout.setContentsMargins(10, 4, 10, 4)
        h_layout.setSpacing(0)

        self.lbl_title = QLabel("Bufferbloat Latency Inflation (Loaded vs Baseline):", self.header_frame)
        self.lbl_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #0f172a; border: none;")
        h_layout.addWidget(self.lbl_title)
        h_layout.addStretch()
        root_layout.addWidget(self.header_frame)

        # 2. Main Chart Container Card
        self.chart_frame = QFrame(self)
        self.chart_frame.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }
        """)
        c_layout = QVBoxLayout(self.chart_frame)
        c_layout.setContentsMargins(6, 6, 6, 4)
        c_layout.setSpacing(0)

        if QT_CHARTS_AVAILABLE:
            self.chart = QChart()
            self.chart.setBackgroundVisible(False)
            self.chart.layout().setContentsMargins(0, 0, 0, 0)
            self.chart.legend().setVisible(True)
            self.chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
            self.chart.legend().setFont(QFont("Segoe UI", 9))
            self.chart.legend().setLabelColor(QColor("#334155"))

            self.chart_view = QChartView(self.chart, self.chart_frame)
            self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.chart_view.setStyleSheet("background: transparent; border: none;")
            self.chart_view.setMinimumHeight(185)
            c_layout.addWidget(self.chart_view)
        else:
            self.chart = None
            self.chart_view = None
            lbl_fallback = QLabel("QtCharts component unavailable.", self.chart_frame)
            c_layout.addWidget(lbl_fallback)

        root_layout.addWidget(self.chart_frame)

        # 3. Bottom Footer Box
        self.footer_frame = QFrame(self)
        self.footer_frame.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }
        """)
        f_layout = QHBoxLayout(self.footer_frame)
        f_layout.setContentsMargins(10, 4, 10, 4)
        f_layout.setSpacing(0)

        self.lbl_meta = QLabel(
            "Baseline: 1.0 ms | Loaded: 1.0 ms | Inflation Delta: +0.0 ms | Evaluated with concurrent bounded downstream HTTP transfer.",
            self.footer_frame,
        )
        self.lbl_meta.setStyleSheet("font-size: 10px; font-weight: 500; color: #64748b; border: none;")
        f_layout.addWidget(self.lbl_meta)
        f_layout.addStretch()
        root_layout.addWidget(self.footer_frame)

        # Compatibility labels for clipboard reporting & existing callers
        self.lbl_b_val = QLabel("1.0 ms", self)
        self.lbl_l_val = QLabel("1.0 ms", self)
        self.lbl_delta_val = QLabel("+0.0 ms", self)
        self.lbl_loaded_val = QLabel("1.0 ms", self)
        self.lbl_baseline_sub = QLabel("Baseline: 1.0 ms", self)
        self.lbl_info_text = QLabel("No significant latency increase observed.", self)
        for w in (self.lbl_b_val, self.lbl_l_val, self.lbl_delta_val, self.lbl_loaded_val, self.lbl_baseline_sub, self.lbl_info_text):
            w.setVisible(False)

        # Initialize default visual state
        self.set_bufferbloat_data(1.0, 1.0, 0.0)

    def set_bufferbloat_data(
        self, baseline_ms: float, loaded_ms: float, delta_ms: float, explanation: Optional[str] = None
    ) -> None:
        """Render baseline and loaded latency bars and update metadata footer."""
        if not QT_CHARTS_AVAILABLE or self.chart is None:
            return

        self.chart.removeAllSeries()
        for ax in list(self.chart.axes()):
            self.chart.removeAxis(ax)

        # Series & Bar Sets
        set_baseline = QBarSet("Unloaded Baseline")
        set_baseline.setColor(QColor("#2064ee"))  # Solid crisp blue matching Image 1
        set_baseline.setBorderColor(QColor("#2064ee"))
        set_baseline.append(float(baseline_ms))

        set_loaded = QBarSet("Under Downstream Load")
        load_col = "#ef4444" if delta_ms > 25.0 else ("#f59e0b" if delta_ms > 10.0 else "#0fba80")  # Emerald green matching Image 1
        set_loaded.setColor(QColor(load_col))
        set_loaded.setBorderColor(QColor(load_col))
        set_loaded.append(float(loaded_ms))

        series = QBarSeries()
        series.append(set_baseline)
        series.append(set_loaded)
        series.setBarWidth(0.48)
        self.chart.addSeries(series)

        # X Axis
        axis_x = QBarCategoryAxis()
        axis_x.append(["Median Delay"])
        axis_x.setLabelsColor(QColor("#64748b"))
        axis_x.setLabelsFont(QFont("Segoe UI", 9))
        self.chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)

        # Y Axis
        axis_y = QValueAxis()
        axis_y.setTitleText("Median Round-Trip Delay (ms)")
        axis_y.setTitleBrush(QColor("#475569"))
        axis_y.setTitleFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        axis_y.setLabelsColor(QColor("#64748b"))
        axis_y.setLabelsFont(QFont("Segoe UI", 8))
        axis_y.setGridLineColor(QColor("#e2e8f0"))
        axis_y.setLabelFormat("%.1f")

        max_val = max(float(baseline_ms), float(loaded_ms))
        if max_val <= 8.0:
            axis_y.setRange(0.0, 10.0)
            axis_y.setTickCount(5)  # 0.0, 2.5, 5.0, 7.5, 10.0 (Matches Image 1)
        else:
            y_ceil = max_val * 1.25
            axis_y.setRange(0.0, y_ceil)
            axis_y.setTickCount(5)

        self.chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)

        # Update Footer & Compatibility Labels
        sign = "+" if delta_ms >= 0 else ""
        delta_str = f"{sign}{delta_ms:.1f} ms"
        self.lbl_meta.setText(
            f"Baseline: {baseline_ms:.1f} ms | Loaded: {loaded_ms:.1f} ms | Inflation Delta: {delta_str} | Evaluated with concurrent bounded downstream HTTP transfer."
        )

        self.lbl_b_val.setText(f"{baseline_ms:.1f} ms")
        self.lbl_l_val.setText(f"{loaded_ms:.1f} ms")
        self.lbl_delta_val.setText(delta_str)
        self.lbl_loaded_val.setText(f"{loaded_ms:.1f} ms")
        self.lbl_baseline_sub.setText(f"Baseline: {baseline_ms:.1f} ms")
        if explanation:
            self.lbl_info_text.setText(explanation)


# -----------------------------------------------------------------------------
# 6. Compact Evidence Matrix Widget
# -----------------------------------------------------------------------------
class CompactEvidenceMatrixWidget(QFrame):
    """Card containing 4-row evidence table matching the reference image layout."""

    sig_view_all = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
        """)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Header
        h_row = QHBoxLayout()
        h_row.setSpacing(8)

        icon = QLabel("📋", self)
        icon.setStyleSheet("font-size: 15px; border: none; color: #2563eb;")
        h_row.addWidget(icon)

        title = QLabel("Evidence Matrix", self)
        title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a; border: none;")
        h_row.addWidget(title)
        h_row.addStretch()

        btn_view_all = QPushButton("View All  →", self)
        btn_view_all.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_view_all.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #2563eb;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover {
                color: #1d4ed8;
                text-decoration: underline;
            }
        """)
        btn_view_all.clicked.connect(self.sig_view_all.emit)
        h_row.addWidget(btn_view_all)

        layout.addLayout(h_row)

        # Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Measurement", "Status", "Interpretation"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: transparent;
                border: none;
            }
            QHeaderView::section {
                background-color: transparent;
                color: #475569;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
                font-weight: 700;
                border: none;
                border-bottom: 1px solid #e2e8f0;
                padding-bottom: 8px;
                padding-left: 2px;
                padding-right: 4px;
                text-align: left;
            }
            QTableWidget::item {
                border-bottom: 1px solid #f1f5f9;
                padding-left: 2px;
                padding-right: 6px;
            }
        """)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 122)
        self.table.setMinimumHeight(185)
        layout.addWidget(self.table)

    def set_rows(self, rows_data: List[Tuple[str, str, str, str, str]]) -> None:
        """Populate table rows: (measurement, status_text, status_bg, status_fg, interpretation)."""
        self.table.setRowCount(len(rows_data))
        for row_idx, (meas, st_text, st_bg, st_fg, interp) in enumerate(rows_data):
            # Col 0: Measurement
            item_m = QTableWidgetItem(meas)
            f_m = QFont("Segoe UI", 10)
            f_m.setBold(True)
            item_m.setFont(f_m)
            item_m.setForeground(QColor("#0f172a"))
            item_m.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_idx, 0, item_m)

            # Col 1: Status Pill
            pill = QLabel(st_text)
            pill.setStyleSheet(f"""
                QLabel {{
                    background-color: {st_bg};
                    color: {st_fg};
                    font-family: 'Segoe UI', -apple-system, sans-serif;
                    font-size: 11px;
                    font-weight: 700;
                    border-radius: 9px;
                    padding: 3px 10px;
                }}
            """)
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)

            pill_wrap = QWidget()
            pill_wrap.setStyleSheet("background: transparent; border-bottom: 1px solid #f1f5f9;")
            p_layout = QHBoxLayout(pill_wrap)
            p_layout.setContentsMargins(0, 4, 10, 4)
            p_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            p_layout.addWidget(pill)
            self.table.setCellWidget(row_idx, 1, pill_wrap)

            # Col 2: Interpretation
            item_i = QTableWidgetItem(interp)
            f_i = QFont("Segoe UI", 10)
            item_i.setFont(f_i)
            item_i.setForeground(QColor("#334155"))
            item_i.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_idx, 2, item_i)

            self.table.setRowHeight(row_idx, 38)

        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 122)


# -----------------------------------------------------------------------------
# 7. Bufferbloat Comparison & Summary Callout Widget
# -----------------------------------------------------------------------------
class BufferbloatScaleBar(QWidget):
    """Continuous scale bar showing 0 to 50ms with active filled segment."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        self.delay_ms: float = 1.0

    def set_delay(self, delay_ms: float) -> None:
        self.delay_ms = max(0.0, delay_ms)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = float(self.width())
        bar_h = 8.0
        bar_y = 6.0

        # Background track
        painter.setBrush(QBrush(QColor("#e2e8f0")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(0, bar_y, w, bar_h), 4.0, 4.0)

        # Active fill (0 to 50ms scale)
        max_scale = 50.0
        fill_frac = min(1.0, self.delay_ms / max_scale)
        fill_w = max(12.0, w * fill_frac)

        fill_color = QColor("#3b82f6") if self.delay_ms < 15.0 else (QColor("#f59e0b") if self.delay_ms < 30.0 else QColor("#ef4444"))
        painter.setBrush(QBrush(fill_color))
        painter.drawRoundedRect(QRectF(0, bar_y, fill_w, bar_h), 4.0, 4.0)

        # Markers at 0 ms, 10 ms, 30 ms, 50 ms
        f = QFont()
        f.setPointSize(7)
        painter.setFont(f)
        painter.setPen(QColor("#94a3b8"))

        markers = [(0.0, "0 ms"), (10.0, "10 ms"), (30.0, "30 ms"), (50.0, "50 ms")]
        for m_val, m_text in markers:
            frac = m_val / max_scale
            x = w * frac
            if m_val == 0.0:
                align = Qt.AlignmentFlag.AlignLeft
                rect = QRectF(x, bar_y + bar_h + 3, 30, 14)
            elif m_val == 50.0:
                align = Qt.AlignmentFlag.AlignRight
                rect = QRectF(x - 30, bar_y + bar_h + 3, 30, 14)
            else:
                align = Qt.AlignmentFlag.AlignCenter
                rect = QRectF(x - 15, bar_y + bar_h + 3, 30, 14)
            painter.drawText(rect, align, m_text)


class BufferbloatComparisonWidget(QWidget):
    """Bottom row containing the Bufferbloat Comparison scale and the lightbulb summary card."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # Left Card: Bufferbloat Comparison (70% width)
        self.card_comp = QFrame(self)
        self.card_comp.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
        """)
        c_layout = QVBoxLayout(self.card_comp)
        c_layout.setContentsMargins(16, 12, 16, 12)
        c_layout.setSpacing(8)

        # Title
        top_h = QHBoxLayout()
        top_h.setSpacing(6)
        icon_gauge = QLabel("⏱️", self.card_comp)
        icon_gauge.setStyleSheet("font-size: 14px; border: none; color: #8b5cf6;")
        top_h.addWidget(icon_gauge)

        title = QLabel("Bufferbloat Comparison", self.card_comp)
        title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a; border: none;")
        top_h.addWidget(title)
        top_h.addStretch()
        c_layout.addLayout(top_h)

        # Delay & Rating Pill row + Scale Bar
        scale_row = QHBoxLayout()
        scale_row.setSpacing(14)

        val_col = QVBoxLayout()
        val_col.setSpacing(2)

        lbl_delay_title = QLabel("Median Delay", self.card_comp)
        lbl_delay_title.setStyleSheet("font-size: 10px; font-weight: 600; color: #64748b; border: none;")
        val_col.addWidget(lbl_delay_title)

        delay_h = QHBoxLayout()
        delay_h.setSpacing(6)

        self.lbl_delay_val = QLabel("1.0 ms", self.card_comp)
        self.lbl_delay_val.setStyleSheet("font-size: 14px; font-weight: 800; color: #0f172a; border: none;")
        delay_h.addWidget(self.lbl_delay_val)

        self.pill_rating = QLabel("✔ Excellent", self.card_comp)
        self.pill_rating.setStyleSheet("""
            background-color: #ecfdf5;
            color: #059669;
            font-size: 9px;
            font-weight: 700;
            border-radius: 8px;
            padding: 2px 8px;
            border: none;
        """)
        delay_h.addWidget(self.pill_rating)
        val_col.addLayout(delay_h)
        scale_row.addLayout(val_col)

        # Scale bar
        self.scale_bar = BufferbloatScaleBar(self.card_comp)
        scale_row.addWidget(self.scale_bar, stretch=1)

        c_layout.addLayout(scale_row)
        layout.addWidget(self.card_comp, stretch=7)

        # Right Card: Lightbulb Summary Callout (30% width)
        self.card_callout = QFrame(self)
        self.card_callout.setStyleSheet("""
            QFrame {
                background-color: #f5f3ff;
                border: 1px solid #ddd6fe;
                border-radius: 14px;
            }
        """)
        co_layout = QHBoxLayout(self.card_callout)
        co_layout.setContentsMargins(14, 12, 14, 12)
        co_layout.setSpacing(10)

        icon_bulb = QLabel("💡", self.card_callout)
        icon_bulb.setStyleSheet("font-size: 20px; border: none; color: #8b5cf6;")
        co_layout.addWidget(icon_bulb)

        self.lbl_callout_text = QLabel("Bufferbloat is minimal under load.", self.card_callout)
        self.lbl_callout_text.setStyleSheet("font-size: 11px; font-weight: 700; color: #1e1b4b; border: none;")
        self.lbl_callout_text.setWordWrap(True)
        co_layout.addWidget(self.lbl_callout_text, stretch=1)

        layout.addWidget(self.card_callout, stretch=3)

    def set_bufferbloat_data(self, delay_ms: float, rating: str = "Excellent", summary: str = "Bufferbloat is minimal under load.") -> None:
        self.lbl_delay_val.setText(f"{delay_ms:.1f} ms")
        self.scale_bar.set_delay(delay_ms)

        if delay_ms <= 5.0:
            rating_text = "✔ Excellent"
            r_bg, r_fg = "#ecfdf5", "#059669"
        elif delay_ms <= 15.0:
            rating_text = "✔ Good"
            r_bg, r_fg = "#ecfdf5", "#059669"
        elif delay_ms <= 30.0:
            rating_text = "Fair"
            r_bg, r_fg = "#fffbeb", "#b45309"
        else:
            rating_text = "Poor"
            r_bg, r_fg = "#fef2f2", "#b91c1c"

        self.pill_rating.setText(rating_text)
        self.pill_rating.setStyleSheet(f"""
            background-color: {r_bg};
            color: {r_fg};
            font-size: 9px;
            font-weight: 700;
            border-radius: 8px;
            padding: 2px 8px;
            border: none;
        """)
        self.lbl_callout_text.setText(summary)


# -----------------------------------------------------------------------------
# 8. Modals: DiagnosticDetailsDialog & FullEvidenceDialog
# -----------------------------------------------------------------------------
class DiagnosticDetailsDialog(QDialog):
    """Modal displaying full 5-stage timeline and actionable recommendations."""

    def __init__(self, timeline_widget: QWidget, recs_widget: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TicNet Diagnostic Details & Actionable Recommendations")
        self.resize(780, 580)
        self.setStyleSheet("background-color: #f8fafc;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        header = QLabel("Diagnostic Stage Execution & Remediations", self)
        header.setStyleSheet("font-size: 16px; font-weight: 800; color: #0f172a;")
        layout.addWidget(header)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(14)

        c_layout.addWidget(timeline_widget)
        c_layout.addWidget(recs_widget)
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        btn_close = QPushButton("Close", self)
        btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #ffffff;
                font-weight: 700;
                font-size: 11px;
                border-radius: 8px;
                padding: 6px 18px;
            }
            QPushButton:hover { background-color: #1e293b; }
        """)
        btn_close.clicked.connect(self.accept)
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)


class FullEvidenceDialog(QDialog):
    """Modal displaying full transparent 7-category evidence matrix with distribution stats."""

    def __init__(self, evidence_widget: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TicNet Complete Empirical Evidence Matrix")
        self.resize(840, 600)
        self.setStyleSheet("background-color: #f8fafc;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        header = QLabel("Complete Empirical Telemetry Evidence Matrix", self)
        header.setStyleSheet("font-size: 16px; font-weight: 800; color: #0f172a;")
        layout.addWidget(header)

        layout.addWidget(evidence_widget, stretch=1)

        btn_close = QPushButton("Close", self)
        btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #ffffff;
                font-weight: 700;
                font-size: 11px;
                border-radius: 8px;
                padding: 6px 18px;
            }
            QPushButton:hover { background-color: #1e293b; }
        """)
        btn_close.clicked.connect(self.accept)
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)

