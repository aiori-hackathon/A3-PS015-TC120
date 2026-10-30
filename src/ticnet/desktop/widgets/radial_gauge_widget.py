"""Standalone interactive radial gauge widget for the TicNet diagnostic pipeline.

Renders a 240-degree circular progress gauge with native Qt painting,
displaying overall pipeline progress, execution state, active stage name,
and live verified measurement metrics.
"""

from __future__ import annotations

from enum import Enum
import math
from typing import Any, Optional, Union

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ticnet.desktop.theme import ThemeTokens


class GaugeState(str, Enum):
    """Supported execution display states for the radial diagnostic gauge."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RadialGaugeWidget(QWidget):
    """High-contrast 240-degree radial gauge for diagnostic pipeline progress.

    Features:
    - Smooth QVariantAnimation between verified backend progress updates.
    - Semantic color-coding matching TicNet's network monitoring theme.
    - Responsive layout with text elision for varying dimensions.
    - Strict presentation-only architecture (no synthetic progress or internal timers).
    """

    # Signal emitted when displayed progress changes during animation
    progress_changed = Signal(float)

    # Geometry constants (Angles in degrees)
    # In Cartesian coordinates (Qt drawArc):
    # 210 degrees is bottom-left (7 o'clock).
    # -240 degrees is a clockwise sweep through 90 degrees (12 o'clock) to -30 degrees (5 o'clock).
    # In screen coordinates (where Y points downwards), this corresponds to a 150-degree start
    # and a 240-degree clockwise sweep.
    START_ANGLE_DEG: float = 210.0
    SWEEP_ANGLE_DEG: float = -240.0
    ANIMATION_DURATION_MS: int = 400

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._target_progress: float = 0.0
        self._displayed_progress: float = 0.0
        self._status: GaugeState = GaugeState.IDLE
        self._active_stage: str = ""
        self._current_metric: Optional[str] = None

        # Setup animation
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.ANIMATION_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation_value_changed)

        # Widget sizing and styling
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    # ------------------------------------------------------------------
    # QWidget Overrides
    # ------------------------------------------------------------------

    def sizeHint(self) -> QSize:
        """Return recommended default size."""
        return QSize(220, 220)

    def minimumSizeHint(self) -> QSize:
        """Return minimum usable size."""
        return QSize(140, 140)

    # ------------------------------------------------------------------
    # Public Properties & Accessors
    # ------------------------------------------------------------------

    @property
    def progress(self) -> float:
        """Return the target confirmed progress (0.0 to 100.0)."""
        return self._target_progress

    @property
    def displayed_progress(self) -> float:
        """Return the currently displayed progress value."""
        return self._displayed_progress

    @property
    def status(self) -> GaugeState:
        """Return the current gauge display state."""
        return self._status

    @property
    def active_stage(self) -> str:
        """Return the active diagnostic stage description."""
        return self._active_stage

    @property
    def current_metric(self) -> Optional[str]:
        """Return the current verified metric string, if any."""
        return self._current_metric

    @property
    def is_animating(self) -> bool:
        """Return whether a progress transition animation is running."""
        return self._animation.state() == QVariantAnimation.State.Running

    # ------------------------------------------------------------------
    # Public Control API
    # ------------------------------------------------------------------

    def set_progress(self, value: Any, animate: bool = True) -> None:
        """Set the overall diagnostic pipeline progress (0 to 100).

        Args:
            value: Progress value. Non-numeric or invalid values are safely ignored.
            animate: Whether to animate the transition to the new progress value.
        """
        # Safe input validation
        try:
            val_float = float(value)
            if math.isnan(val_float) or math.isinf(val_float):
                return
        except (TypeError, ValueError):
            return

        # Clamp between 0.0 and 100.0
        clamped = max(0.0, min(100.0, val_float))
        self._target_progress = clamped

        if not animate:
            if self._animation.state() == QVariantAnimation.State.Running:
                self._animation.stop()
            self._displayed_progress = clamped
            self.progress_changed.emit(self._displayed_progress)
            self.update()
            return

        # Smooth animation transition
        if self._animation.state() == QVariantAnimation.State.Running:
            self._animation.stop()

        if abs(self._displayed_progress - clamped) < 0.1:
            self._displayed_progress = clamped
            self.progress_changed.emit(self._displayed_progress)
            self.update()
            return

        self._animation.setStartValue(self._displayed_progress)
        self._animation.setEndValue(clamped)
        self._animation.start()

    def set_status(self, status: Union[GaugeState, str]) -> None:
        """Set the execution state of the diagnostic gauge.

        Args:
            status: A GaugeState enum or case-insensitive string equivalent.
        """
        if isinstance(status, GaugeState):
            new_state = status
        elif isinstance(status, str):
            clean = status.strip().upper().replace(" ", "_").replace("-", "_")
            try:
                new_state = GaugeState(clean)
            except ValueError:
                # Fallback heuristics for common variants
                if "WARN" in clean:
                    new_state = GaugeState.COMPLETED_WITH_WARNINGS
                elif "FAIL" in clean or "ERR" in clean:
                    new_state = GaugeState.FAILED
                elif "CANCEL" in clean or "ABORT" in clean:
                    new_state = GaugeState.CANCELLED
                elif "RUN" in clean or "EXEC" in clean or "DIAG" in clean:
                    new_state = GaugeState.RUNNING
                elif "COMPLET" in clean or "PASS" in clean or "SUCCESS" in clean:
                    new_state = GaugeState.COMPLETED
                else:
                    new_state = GaugeState.IDLE
        else:
            new_state = GaugeState.IDLE

        # If cancelled, stop any pending animation and preserve last confirmed progress
        if new_state == GaugeState.CANCELLED:
            if self._animation.state() == QVariantAnimation.State.Running:
                self._animation.stop()

        self._status = new_state
        self.update()

    def set_active_stage(self, stage_name: Optional[str]) -> None:
        """Set the active diagnostic stage label.

        Args:
            stage_name: Name of current executing stage, or empty string if none.
        """
        self._active_stage = str(stage_name).strip() if stage_name is not None else ""
        self.update()

    def set_current_metric(self, metric_text: Optional[str]) -> None:
        """Set the optional live verified metric chip readout.

        Args:
            metric_text: Metric string (e.g., 'Gateway RTT: 2.1 ms') or None to clear.
        """
        if metric_text is None or not str(metric_text).strip():
            self._current_metric = None
        else:
            self._current_metric = str(metric_text).strip()
        self.update()

    def reset(self) -> None:
        """Reset the gauge to initial idle state."""
        if self._animation.state() == QVariantAnimation.State.Running:
            self._animation.stop()

        self._target_progress = 0.0
        self._displayed_progress = 0.0
        self._status = GaugeState.IDLE
        self._active_stage = ""
        self._current_metric = None
        self.progress_changed.emit(0.0)
        self.update()

    # ------------------------------------------------------------------
    # Internal Animation Handler
    # ------------------------------------------------------------------

    def _on_animation_value_changed(self, value: Any) -> None:
        """Handle incremental progress changes from QVariantAnimation."""
        try:
            self._displayed_progress = float(value)
            self.progress_changed.emit(self._displayed_progress)
            self.update()
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Theming & Color Helpers
    # ------------------------------------------------------------------

    def _get_accent_color(self) -> QColor:
        """Return the primary accent color corresponding to current state."""
        if self._status == GaugeState.RUNNING:
            return QColor(ThemeTokens.BRAND_PRIMARY)
        elif self._status == GaugeState.COMPLETED:
            return QColor(ThemeTokens.COLOR_SUCCESS)
        elif self._status == GaugeState.COMPLETED_WITH_WARNINGS:
            return QColor(ThemeTokens.COLOR_WARNING)
        elif self._status == GaugeState.FAILED:
            return QColor(ThemeTokens.COLOR_ERROR)
        elif self._status == GaugeState.CANCELLED:
            return QColor("#64748b")
        else:  # IDLE
            return QColor("#94a3b8")

    def _get_status_label_text(self) -> str:
        """Return readable uppercase status headline text."""
        if self._status == GaugeState.RUNNING:
            return "RUNNING"
        elif self._status == GaugeState.COMPLETED:
            return "COMPLETED"
        elif self._status == GaugeState.COMPLETED_WITH_WARNINGS:
            return "WARNING"
        elif self._status == GaugeState.FAILED:
            return "FAILED"
        elif self._status == GaugeState.CANCELLED:
            return "CANCELLED"
        else:
            return "READY"

    # ------------------------------------------------------------------
    # Painting & Rendering
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the 240-degree radial gauge with antialiased Qt primitives."""
        w = float(self.width())
        h = float(self.height())
        if w < 10 or h < 10:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Scale thickness smoothly with widget size, bounded between 8.0 and 13.0
        thickness = max(8.0, min(min(w, h) * 0.065, 13.0))

        # Safe margins to guarantee stroke outer edge and caps never clip:
        margin_top = (thickness / 2.0) + 8.0
        margin_bottom = 8.0
        margin_sides = (thickness / 2.0) + 8.0

        avail_w = max(20.0, w - 2.0 * margin_sides)
        avail_h = max(20.0, h - margin_top - margin_bottom)
        diameter = min(avail_w, avail_h)
        if diameter < 20:
            painter.end()
            return

        cx = w / 2.0
        # Position cy so the top of the outer stroke (cy - diameter/2 - thickness/2)
        # has exactly margin_top clearance from the top edge of the widget.
        cy = margin_top + (thickness / 2.0) + (diameter / 2.0)

        arc_rect = QRectF(cx - diameter / 2.0, cy - diameter / 2.0, diameter, diameter)

        # 1. Background Track Arc (240 degrees from 210° to -30°)
        track_color = QColor(ThemeTokens.BORDER_DEFAULT)
        track_pen = QPen(track_color, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        start_angle_16ths = int(round(self.START_ANGLE_DEG * 16))
        total_sweep_16ths = int(round(self.SWEEP_ANGLE_DEG * 16))
        painter.drawArc(arc_rect, start_angle_16ths, total_sweep_16ths)

        # 2. Active Progress Arc
        accent_color = self._get_accent_color()
        if self._displayed_progress > 0.1:
            prog_pen = QPen(accent_color, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(prog_pen)
            active_sweep = self.SWEEP_ANGLE_DEG * (self._displayed_progress / 100.0)
            painter.drawArc(arc_rect, start_angle_16ths, int(round(active_sweep * 16)))

        # 3. Typography & Information Overlay
        inner_width = max(30.0, diameter - 2.0 * thickness - 16.0)

        # Proportional font sizes based on widget diameter
        status_pt = max(7, int(diameter * 0.042))
        percent_pt = max(13, int(diameter * 0.125))
        stage_pt = max(7, int(diameter * 0.040))
        metric_pt = max(7, int(diameter * 0.038))

        font_status = QFont("Segoe UI", status_pt, QFont.Weight.DemiBold)
        font_percent = QFont("Cascadia Code", percent_pt, QFont.Weight.Bold)
        font_stage = QFont("Segoe UI", stage_pt, QFont.Weight.Normal)
        font_metric = QFont("Segoe UI", metric_pt, QFont.Weight.DemiBold)

        fm_percent = QFontMetrics(font_percent)
        fm_status = QFontMetrics(font_status)
        fm_stage = QFontMetrics(font_stage)
        fm_metric = QFontMetrics(font_metric)

        # Large Percentage Text (Center Anchor)
        percent_text = f"{int(round(self._displayed_progress))}%"
        percent_h = fm_percent.height()
        percent_y = cy - (percent_h / 2.0) - 2.0
        rect_percent = QRectF(cx - inner_width / 2.0, percent_y, inner_width, percent_h)

        painter.setFont(font_percent)
        painter.setPen(QColor(ThemeTokens.TEXT_PRIMARY))
        painter.drawText(rect_percent, Qt.AlignmentFlag.AlignCenter, percent_text)

        # Status Headline (Above Percentage)
        status_text = self._get_status_label_text()
        status_h = fm_status.height()
        status_y = rect_percent.top() - status_h - 1.0
        rect_status = QRectF(cx - inner_width / 2.0, status_y, inner_width, status_h)

        painter.setFont(font_status)
        painter.setPen(accent_color)
        painter.drawText(rect_status, Qt.AlignmentFlag.AlignCenter, status_text)

        # Active Stage Label (Below Percentage)
        stage_text = self._active_stage if self._active_stage else "Awaiting execution"
        stage_h = fm_stage.height()
        stage_y = rect_percent.bottom() + 2.0
        elided_stage = fm_stage.elidedText(stage_text, Qt.TextElideMode.ElideRight, int(inner_width))
        rect_stage = QRectF(cx - inner_width / 2.0, stage_y, inner_width, stage_h)

        painter.setFont(font_stage)
        painter.setPen(QColor(ThemeTokens.TEXT_MUTED))
        painter.drawText(rect_stage, Qt.AlignmentFlag.AlignCenter, elided_stage)

        # Optional Verified Metric Chip (Below Stage)
        if self._current_metric:
            metric_h = fm_metric.height()
            elided_metric = fm_metric.elidedText(
                self._current_metric, Qt.TextElideMode.ElideRight, int(inner_width - 16)
            )
            text_advance = fm_metric.horizontalAdvance(elided_metric)
            chip_w = min(inner_width, text_advance + 14.0)
            chip_h = metric_h + 3.0
            chip_x = cx - chip_w / 2.0
            chip_y = rect_stage.bottom() + 4.0

            chip_rect = QRectF(chip_x, chip_y, chip_w, chip_h)

            # Soft background container
            painter.setPen(QPen(QColor(ThemeTokens.BORDER_DEFAULT), 1))
            painter.setBrush(QColor(ThemeTokens.BG_MUTED))
            painter.drawRoundedRect(chip_rect, 4.0, 4.0)

            # Metric text
            painter.setFont(font_metric)
            painter.setPen(QColor(ThemeTokens.TEXT_SECONDARY))
            painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, elided_metric)

        painter.end()
