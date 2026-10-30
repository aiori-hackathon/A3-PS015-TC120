"""Live diagnostic activity feed widget for TicNet presentation layer.

Presents a chronological, real-time timeline of diagnostic workflow events:
- Stage lifecycle transitions (Started, Progress, Completed, Skipped, Failed).
- Measurement readouts and telemetry details.
- Deterministic diagnostic rule evaluations and findings.
- Auto-scroll with user-scroll preservation and critical event retention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Union

from PySide6.QtCore import QDateTime, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QResizeEvent
from ticnet.core.timezone import format_ist, now_ist, to_ist
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens


class ActivityCategory(str, Enum):
    """Supported event categories for diagnostic activity entries."""

    DIAGNOSTIC_STARTED = "DIAGNOSTIC_STARTED"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_PROGRESS = "STAGE_PROGRESS"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    STAGE_SKIPPED = "STAGE_SKIPPED"
    STAGE_FAILED = "STAGE_FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    DIAGNOSTIC_COMPLETED = "DIAGNOSTIC_COMPLETED"
    DIAGNOSTIC_ERROR = "DIAGNOSTIC_ERROR"
    DIAGNOSTIC_CANCELLED = "DIAGNOSTIC_CANCELLED"
    INFO = "INFO"


# Critical categories that must be preserved during event-pruning limits
CRITICAL_CATEGORIES = {
    ActivityCategory.STAGE_FAILED,
    ActivityCategory.DIAGNOSTIC_ERROR,
    ActivityCategory.DIAGNOSTIC_CANCELLED,
    ActivityCategory.DIAGNOSTIC_COMPLETED,
    ActivityCategory.INCONCLUSIVE,
}


@dataclass
class ActivityEvent:
    """Normalized structured event data for the diagnostic activity feed."""

    category: ActivityCategory
    message: str
    timestamp: Optional[Union[str, datetime]] = None
    stage_order: Optional[int] = None
    stage_name: Optional[str] = None
    status: Optional[str] = None
    measurement: Optional[str] = None
    event_id: Optional[str] = None
    is_terminal: bool = False
    display_time: str = field(init=False)

    def __post_init__(self) -> None:
        """Parse timestamp safely to local display format (HH:MM:SS)."""
        self.display_time = self._format_timestamp(self.timestamp)
        if self.category in CRITICAL_CATEGORIES:
            self.is_terminal = True

    @staticmethod
    def _format_timestamp(ts: Optional[Union[str, datetime]]) -> str:
        """Format input timestamp safely in Indian Standard Time (HH:MM:SS)."""
        if ts is None:
            return now_ist().strftime("%H:%M:%S")
        dt_ist = to_ist(ts)
        if dt_ist:
            return dt_ist.strftime("%H:%M:%S")
        if isinstance(ts, str):
            clean_ts = ts.strip()
            if len(clean_ts) >= 8 and clean_ts[2] == ":" and clean_ts[5] == ":":
                return clean_ts[:8]
        return now_ist().strftime("%H:%M:%S")


class ActivityItemWidget(QFrame):
    """Card widget rendering a single diagnostic activity event in the feed."""

    def __init__(self, event: ActivityEvent, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.activity_event: ActivityEvent = event
        self._init_ui()

    def _init_ui(self) -> None:
        """Build UI elements for the activity item."""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setObjectName("activityItemCard")

        # Styling based on category
        accent_color, bg_card, border_color, badge_text, badge_bg, badge_border, badge_fg = self._get_category_palette()

        self.setStyleSheet(
            f"#activityItemCard {{"
            f"  background-color: {bg_card};"
            f"  border: 1px solid {border_color};"
            f"  border-left: 4px solid {accent_color};"
            f"  border-radius: {ThemeTokens.RADIUS_DEFAULT};"
            f"}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Top row: Timestamp + Stage Badge + Status Badge
        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        # Timestamp
        self.lbl_time = QLabel(self.activity_event.display_time, top_row)
        self.lbl_time.setStyleSheet(
            f"font-family: {ThemeTokens.FONT_MONO}; "
            f"font-size: 11px; "
            f"color: {ThemeTokens.TEXT_MUTED}; "
            f"font-weight: 600; "
            f"border: none; background: transparent;"
        )
        top_layout.addWidget(self.lbl_time)

        # Stage indicator (if stage info present)
        if self.activity_event.stage_order or self.activity_event.stage_name:
            stage_text = f"Stage {self.activity_event.stage_order}" if self.activity_event.stage_order else "Stage"
            if self.activity_event.stage_name:
                stage_text += f" • {self.activity_event.stage_name}"
            self.lbl_stage = QLabel(stage_text, top_row)
            self.lbl_stage.setStyleSheet(
                f"font-size: 11px; "
                f"color: {ThemeTokens.TEXT_SECONDARY}; "
                f"font-weight: 600; "
                f"border: none; background: transparent;"
            )
            top_layout.addWidget(self.lbl_stage)

        top_layout.addStretch()

        # Status badge
        self.lbl_badge = QLabel(badge_text, top_row)
        self.lbl_badge.setStyleSheet(
            f"font-size: 10px; font-weight: 700; "
            f"background-color: {badge_bg}; color: {badge_fg}; "
            f"border: 1px solid {badge_border}; border-radius: 4px; "
            f"padding: 1px 7px;"
        )
        top_layout.addWidget(self.lbl_badge)
        layout.addWidget(top_row)

        # Middle row: Word-wrapped message
        self.lbl_message = QLabel(self.activity_event.message, self)
        self.lbl_message.setWordWrap(True)
        self.lbl_message.setStyleSheet(
            f"font-size: 12px; color: {ThemeTokens.TEXT_PRIMARY}; "
            f"border: none; background: transparent; line-height: 1.3;"
        )
        layout.addWidget(self.lbl_message)

        # Optional bottom row: Verified Measurement chip
        self.lbl_measurement: Optional[QLabel] = None
        if self.activity_event.measurement:
            self._add_measurement_chip(self.activity_event.measurement, layout)

    def _add_measurement_chip(self, metric_text: str, layout: QVBoxLayout) -> None:
        """Create and append a measurement pill chip."""
        self.lbl_measurement = QLabel(f"📊 {metric_text}", self)
        self.lbl_measurement.setWordWrap(True)
        self.lbl_measurement.setStyleSheet(
            f"font-family: {ThemeTokens.FONT_MONO}; "
            f"font-size: 11px; "
            f"color: {ThemeTokens.TEXT_SECONDARY}; "
            f"background-color: #ffffff; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: 4px; "
            f"padding: 3px 8px;"
        )
        layout.addWidget(self.lbl_measurement)

    def update_progress_in_place(self, message: str, measurement: Optional[str] = None) -> None:
        """Update an existing stage progress entry in-place without adding duplicate cards."""
        self.activity_event.message = message
        self.lbl_message.setText(message)
        if measurement:
            self.activity_event.measurement = measurement
            if self.lbl_measurement:
                self.lbl_measurement.setText(f"📊 {measurement}")
            else:
                self._add_measurement_chip(measurement, self.layout())  # type: ignore[arg-type]

    def _get_category_palette(self) -> tuple[str, str, str, str, str, str, str]:
        """Return theme color tuple for the current category.

        Returns:
            (accent_left_border, bg_card, border_color, badge_text, badge_bg, badge_border, badge_fg)
        """
        cat = self.activity_event.category
        status_override = (self.activity_event.status or "").upper()

        if cat == ActivityCategory.STAGE_STARTED:
            return (
                ThemeTokens.BRAND_PRIMARY,
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                "RUNNING",
                ThemeTokens.BRAND_LIGHT,
                "#bfdbfe",
                "#1d4ed8",
            )
        elif cat == ActivityCategory.STAGE_PROGRESS:
            return (
                "#3b82f6",
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                "PROGRESS",
                "#f0f9ff",
                "#bae6fd",
                "#0284c7",
            )
        elif cat == ActivityCategory.STAGE_COMPLETED or cat == ActivityCategory.DIAGNOSTIC_COMPLETED:
            badge_label = status_override if status_override and status_override != "COMPLETED" else "SUCCESS"
            return (
                ThemeTokens.COLOR_SUCCESS,
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                badge_label,
                ThemeTokens.BG_SUCCESS_LIGHT,
                ThemeTokens.BORDER_SUCCESS,
                "#065f46",
            )
        elif cat == ActivityCategory.STAGE_SKIPPED:
            return (
                ThemeTokens.COLOR_WARNING,
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                "SKIPPED",
                ThemeTokens.BG_WARNING_LIGHT,
                ThemeTokens.BORDER_WARNING,
                "#92400e",
            )
        elif cat in (ActivityCategory.STAGE_FAILED, ActivityCategory.DIAGNOSTIC_ERROR):
            badge_label = status_override if status_override and status_override != "FAILED" else "FAILED"
            return (
                ThemeTokens.COLOR_ERROR,
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                badge_label,
                ThemeTokens.BG_ERROR_LIGHT,
                ThemeTokens.BORDER_ERROR,
                "#991b1b",
            )
        elif cat == ActivityCategory.INCONCLUSIVE:
            return (
                ThemeTokens.COLOR_INCONCLUSIVE,
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                "INCONCLUSIVE",
                ThemeTokens.BG_INCONCLUSIVE_LIGHT,
                ThemeTokens.BORDER_INCONCLUSIVE,
                "#3730a3",
            )
        elif cat == ActivityCategory.DIAGNOSTIC_CANCELLED:
            return (
                "#64748b",
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                "CANCELLED",
                "#f1f5f9",
                "#cbd5e1",
                "#475569",
            )
        else:  # DIAGNOSTIC_STARTED, INFO
            return (
                ThemeTokens.COLOR_INFO,
                "#ffffff",
                ThemeTokens.BORDER_DEFAULT,
                "INFO",
                ThemeTokens.BG_INFO_LIGHT,
                ThemeTokens.BORDER_INFO,
                "#0369a1",
            )


class DiagnosticActivityFeedWidget(QWidget):
    """Standalone, reusable timeline widget displaying real-time diagnostic execution events.

    Invariants:
    - Independent presentation widget: decoupled from backend workers.
    - Honest timeline: never treats skipped or inconclusive stages as successful.
    - Smart auto-scrolling: auto-scrolls to latest unless the user has scrolled up to inspect history.
    - Critical event preservation: never prunes terminal failures or summary records under limits.
    - Deduplication: collapses consecutive in-flight progress messages for the same stage.
    """

    event_added = Signal(object)  # Emits ActivityEvent

    DEFAULT_MAX_EVENTS: int = 150

    def __init__(self, max_events: int = DEFAULT_MAX_EVENTS, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._max_events: int = max(20, max_events)
        self._events: List[ActivityEvent] = []
        self._item_widgets: List[ActivityItemWidget] = []
        self._auto_scroll_enabled: bool = True
        self._last_progress_stage: Optional[int] = None
        self._last_progress_widget: Optional[ActivityItemWidget] = None

        self._init_ui()

    def _init_ui(self) -> None:
        """Construct feed frame, header, scroll area, and item layout."""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Outer card container
        self._main_frame = QFrame(self)
        self._main_frame.setObjectName("activityFeedMainFrame")
        self._main_frame.setStyleSheet(
            f"#activityFeedMainFrame {{"
            f"  background-color: {ThemeTokens.BG_CARD};"
            f"  border: 1px solid {ThemeTokens.BORDER_DEFAULT};"
            f"  border-radius: {ThemeTokens.RADIUS_LARGE};"
            f"}}"
        )

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self._main_frame)

        frame_layout = QVBoxLayout(self._main_frame)
        frame_layout.setContentsMargins(14, 14, 14, 14)
        frame_layout.setSpacing(10)

        # 1. Header Row
        header_widget = QWidget(self._main_frame)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.lbl_title = QLabel("Live Diagnostic Activity", header_widget)
        self.lbl_title.setStyleSheet(
            f"font-size: {ThemeTokens.FONT_SIZE_CARD_TITLE}; "
            f"font-weight: 700; "
            f"color: {ThemeTokens.TEXT_PRIMARY};"
        )
        header_layout.addWidget(self.lbl_title)

        # Event count badge
        self.lbl_count_badge = QLabel("0 events", header_widget)
        self.lbl_count_badge.setStyleSheet(
            f"font-size: {ThemeTokens.FONT_SIZE_CAPTION}; "
            f"font-weight: 600; "
            f"color: {ThemeTokens.TEXT_SECONDARY}; "
            f"background-color: {ThemeTokens.BG_MUTED}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_PILL}; "
            f"padding: 2px 8px;"
        )
        header_layout.addWidget(self.lbl_count_badge)

        header_layout.addStretch()

        # Jump to latest button (hidden by default, shown when user scrolled up)
        self.btn_scroll_latest = QPushButton("⬇ Jump to latest", header_widget)
        self.btn_scroll_latest.setVisible(False)
        self.btn_scroll_latest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scroll_latest.setStyleSheet(
            f"font-size: 11px; font-weight: 600; "
            f"background-color: {ThemeTokens.BRAND_LIGHT}; color: {ThemeTokens.BRAND_PRIMARY}; "
            f"border: 1px solid #bfdbfe; border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
            f"padding: 2px 8px;"
        )
        self.btn_scroll_latest.clicked.connect(self.scroll_to_latest)
        header_layout.addWidget(self.btn_scroll_latest)

        # Live execution indicator
        self.lbl_exec_indicator = QLabel("● IDLE", header_widget)
        self.lbl_exec_indicator.setStyleSheet(
            f"font-size: 11px; font-weight: 700; "
            f"color: {ThemeTokens.TEXT_MUTED};"
        )
        header_layout.addWidget(self.lbl_exec_indicator)

        frame_layout.addWidget(header_widget)

        # 2. Scroll Area for Events
        self._scroll_area = QScrollArea(self._main_frame)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { width: 8px; background: transparent; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 4px; min-height: 20px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: #94a3b8; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        # Scrollbar tracking for user-intent awareness
        scrollbar = self._scroll_area.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll_changed)
        scrollbar.rangeChanged.connect(self._on_scroll_range_changed)

        # Container for events list
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._items_layout = QVBoxLayout(self._container)
        self._items_layout.setContentsMargins(0, 0, 6, 0)
        self._items_layout.setSpacing(6)
        self._items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Empty state placeholder
        self._empty_label = QLabel(
            "No diagnostic activity recorded yet.\nStart a diagnostic run to monitor real-time execution.",
            self._container,
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"font-size: 12px; color: {ThemeTokens.TEXT_MUTED}; "
            f"padding: 40px 20px; line-height: 1.5;"
        )
        self._items_layout.addWidget(self._empty_label)

        self._scroll_area.setWidget(self._container)
        frame_layout.addWidget(self._scroll_area)

    # ------------------------------------------------------------------
    # Public Properties
    # ------------------------------------------------------------------

    @property
    def event_count(self) -> int:
        """Return the number of events currently in the feed."""
        return len(self._events)

    @property
    def max_events(self) -> int:
        """Return the configured maximum events limit."""
        return self._max_events

    @property
    def is_auto_scroll_enabled(self) -> bool:
        """Return True if auto-scrolling to the latest event is active."""
        return self._auto_scroll_enabled

    @property
    def events(self) -> List[ActivityEvent]:
        """Return an immutable snapshot copy of recorded events."""
        return list(self._events)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_max_events(self, max_events: int) -> None:
        """Update the maximum event retention limit.

        Args:
            max_events: Maximum number of events to retain (minimum 20).
        """
        self._max_events = max(20, int(max_events))
        self._enforce_event_limit()

    def set_execution_state(self, state_text: str, is_active: bool = False) -> None:
        """Update the execution status indicator in the feed header.

        Args:
            state_text: Short text label (e.g. 'RUNNING', 'COMPLETED', 'IDLE').
            is_active: Whether diagnosis is actively executing.
        """
        clean_text = state_text.strip().upper()
        if is_active or "RUN" in clean_text or "EXEC" in clean_text:
            color = ThemeTokens.BRAND_PRIMARY
            self.lbl_exec_indicator.setText(f"● {clean_text}")
        elif "COMPLET" in clean_text or "PASS" in clean_text or "SUCCESS" in clean_text:
            color = ThemeTokens.COLOR_SUCCESS
            self.lbl_exec_indicator.setText(f"● {clean_text}")
        elif "WARN" in clean_text:
            color = ThemeTokens.COLOR_WARNING
            self.lbl_exec_indicator.setText(f"● {clean_text}")
        elif "FAIL" in clean_text or "ERR" in clean_text:
            color = ThemeTokens.COLOR_ERROR
            self.lbl_exec_indicator.setText(f"● {clean_text}")
        elif "CANCEL" in clean_text:
            color = "#64748b"
            self.lbl_exec_indicator.setText(f"● {clean_text}")
        else:
            color = ThemeTokens.TEXT_MUTED
            self.lbl_exec_indicator.setText(f"● {clean_text}")

        self.lbl_exec_indicator.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {color};"
        )

    def add_activity(
        self,
        category: Union[ActivityCategory, str],
        message: str,
        stage_order: Optional[int] = None,
        stage_name: Optional[str] = None,
        status: Optional[str] = None,
        timestamp: Optional[Union[str, datetime]] = None,
        measurement: Optional[str] = None,
        is_terminal: bool = False,
    ) -> ActivityItemWidget:
        """Add an event to the activity feed via typed parameters.

        Args:
            category: ActivityCategory or string equivalent.
            message: Informative event description.
            stage_order: Optional 1-based diagnostic stage index.
            stage_name: Optional diagnostic stage name.
            status: Optional execution status string.
            timestamp: Optional UTC ISO string or datetime object.
            measurement: Optional verified measurement readout.
            is_terminal: Whether this event is a critical/terminal outcome.

        Returns:
            The ActivityItemWidget added or updated in the timeline.
        """
        # Normalize category
        if isinstance(category, ActivityCategory):
            norm_cat = category
        elif isinstance(category, str):
            clean = category.strip().upper().replace(" ", "_")
            try:
                norm_cat = ActivityCategory(clean)
            except ValueError:
                # Heuristic mapping for common backend string values
                if "START" in clean and "STAGE" in clean:
                    norm_cat = ActivityCategory.STAGE_STARTED
                elif "PROGRESS" in clean:
                    norm_cat = ActivityCategory.STAGE_PROGRESS
                elif "COMPLET" in clean and "STAGE" in clean:
                    norm_cat = ActivityCategory.STAGE_COMPLETED
                elif "SKIP" in clean:
                    norm_cat = ActivityCategory.STAGE_SKIPPED
                elif "FAIL" in clean and "STAGE" in clean:
                    norm_cat = ActivityCategory.STAGE_FAILED
                elif "INCONCLU" in clean:
                    norm_cat = ActivityCategory.INCONCLUSIVE
                elif "CANCEL" in clean:
                    norm_cat = ActivityCategory.DIAGNOSTIC_CANCELLED
                elif "ERR" in clean:
                    norm_cat = ActivityCategory.DIAGNOSTIC_ERROR
                elif "COMPLET" in clean:
                    norm_cat = ActivityCategory.DIAGNOSTIC_COMPLETED
                else:
                    norm_cat = ActivityCategory.INFO
        else:
            norm_cat = ActivityCategory.INFO

        event = ActivityEvent(
            category=norm_cat,
            message=str(message).strip(),
            timestamp=timestamp,
            stage_order=stage_order,
            stage_name=stage_name,
            status=status,
            measurement=measurement,
            is_terminal=is_terminal,
        )

        return self.add_event(event)

    def add_event(self, event: Union[ActivityEvent, Dict[str, Any], Any]) -> ActivityItemWidget:
        """Add an event object or workflow event to the activity timeline.

        Supports ActivityEvent, WorkflowEvent, or dictionary payloads.
        """
        norm_event = self._normalize_event(event)

        # 1. Deduplication: exact identical event check
        if self._is_exact_duplicate(norm_event):
            # Return last widget without creating redundant duplicate
            return self._item_widgets[-1]

        # 2. Progress update in-place collapsing
        if (
            norm_event.category == ActivityCategory.STAGE_PROGRESS
            and self._last_progress_stage is not None
            and self._last_progress_stage == norm_event.stage_order
            and self._last_progress_widget is not None
        ):
            self._last_progress_widget.update_progress_in_place(
                norm_event.message, norm_event.measurement
            )
            # Update event stored in memory
            self._events[-1] = norm_event
            self.event_added.emit(norm_event)
            return self._last_progress_widget

        # Hide empty state placeholder on first real event
        self._empty_label.setVisible(False)

        # 3. Create and mount new item widget
        item_widget = ActivityItemWidget(norm_event, self._container)
        self._items_layout.addWidget(item_widget)

        self._events.append(norm_event)
        self._item_widgets.append(item_widget)

        # Track progress row collapsing
        if norm_event.category == ActivityCategory.STAGE_PROGRESS:
            self._last_progress_stage = norm_event.stage_order
            self._last_progress_widget = item_widget
        else:
            self._last_progress_stage = None
            self._last_progress_widget = None

        # Update event count badge
        self._update_event_count()

        # Enforce maximum retention limit (preserving critical terminal events)
        self._enforce_event_limit()

        # Auto-scroll if following latest
        if self._auto_scroll_enabled:
            QTimer.singleShot(10, self.scroll_to_latest)

        self.event_added.emit(norm_event)
        return item_widget

    def clear(self) -> None:
        """Clear all events and reset the feed to its pristine empty state."""
        for item in self._item_widgets:
            item.setParent(None)
            item.deleteLater()

        self._events.clear()
        self._item_widgets.clear()
        self._last_progress_stage = None
        self._last_progress_widget = None
        self._auto_scroll_enabled = True

        self._empty_label.setVisible(True)
        self.btn_scroll_latest.setVisible(False)
        self.lbl_exec_indicator.setText("● IDLE")
        self.lbl_exec_indicator.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ThemeTokens.TEXT_MUTED};"
        )
        self._update_event_count()

    def scroll_to_latest(self) -> None:
        """Scroll the activity feed smoothly to the newest event at the bottom."""
        scrollbar = self._scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self._auto_scroll_enabled = True
        self.btn_scroll_latest.setVisible(False)

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _normalize_event(self, event: Any) -> ActivityEvent:
        """Normalize various input formats into a typed ActivityEvent."""
        if isinstance(event, ActivityEvent):
            return event

        # Handle WorkflowEvent object
        if hasattr(event, "event_type"):
            return self._from_workflow_event(event)

        # Handle Dict payload
        if isinstance(event, dict):
            cat_str = str(event.get("category") or event.get("event_type") or "INFO")
            try:
                cat = ActivityCategory(cat_str.upper())
            except ValueError:
                cat = ActivityCategory.INFO

            return ActivityEvent(
                category=cat,
                message=str(event.get("message") or ""),
                timestamp=event.get("timestamp") or event.get("timestamp_utc"),
                stage_order=event.get("stage_order"),
                stage_name=event.get("stage_name"),
                status=event.get("status"),
                measurement=event.get("measurement"),
                is_terminal=bool(event.get("is_terminal", False)),
            )

        # Generic fallback
        return ActivityEvent(
            category=ActivityCategory.INFO,
            message=str(event),
            timestamp=datetime.now(),
        )

    def _from_workflow_event(self, wf_event: Any) -> ActivityEvent:
        """Convert a backend WorkflowEvent into an ActivityEvent."""
        event_type = getattr(wf_event, "event_type", None)
        type_str = getattr(event_type, "value", str(event_type or ""))

        # Mapping WorkflowEventType to ActivityCategory
        cat_map = {
            "RUN_STARTED": ActivityCategory.DIAGNOSTIC_STARTED,
            "STAGE_STARTED": ActivityCategory.STAGE_STARTED,
            "STAGE_PROGRESS": ActivityCategory.STAGE_PROGRESS,
            "STAGE_COMPLETED": ActivityCategory.STAGE_COMPLETED,
            "STAGE_SKIPPED": ActivityCategory.STAGE_SKIPPED,
            "STAGE_FAILED": ActivityCategory.STAGE_FAILED,
            "STAGE_CANCELLED": ActivityCategory.DIAGNOSTIC_CANCELLED,
            "RUN_COMPLETED": ActivityCategory.DIAGNOSTIC_COMPLETED,
            "RUN_PARTIAL": ActivityCategory.DIAGNOSTIC_COMPLETED,
            "RUN_CANCELLED": ActivityCategory.DIAGNOSTIC_CANCELLED,
            "RUN_FAILED": ActivityCategory.DIAGNOSTIC_ERROR,
            "LOG": ActivityCategory.INFO,
        }
        category = cat_map.get(type_str, ActivityCategory.INFO)

        # Extract potential measurement summary from event data dict
        measurement = None
        data = getattr(wf_event, "data", None)
        if isinstance(data, dict):
            if "metric" in data:
                measurement = str(data["metric"])
            elif "summary" in data:
                measurement = str(data["summary"])
            elif "latency_ms" in data:
                measurement = f"Latency: {data['latency_ms']} ms"

        return ActivityEvent(
            category=category,
            message=getattr(wf_event, "message", None) or f"{type_str} event",
            timestamp=getattr(wf_event, "timestamp_utc", None),
            stage_order=getattr(wf_event, "stage_order", None),
            stage_name=getattr(wf_event, "stage_name", None),
            status=getattr(wf_event, "status", None),
            measurement=measurement,
            is_terminal=category in CRITICAL_CATEGORIES,
        )

    def _is_exact_duplicate(self, new_event: ActivityEvent) -> bool:
        """Check if incoming event is an identical duplicate of the last entry."""
        if not self._events:
            return False

        last = self._events[-1]
        return (
            last.category == new_event.category
            and last.stage_order == new_event.stage_order
            and last.message == new_event.message
            and last.measurement == new_event.measurement
            and last.status == new_event.status
            and last.display_time == new_event.display_time
        )

    def _enforce_event_limit(self) -> None:
        """Enforce maximum event limit by pruning oldest non-critical events first."""
        excess = len(self._events) - self._max_events
        if excess <= 0:
            return

        # Find indices of oldest non-terminal events to prune
        pruned_indices = []
        for idx, event in enumerate(self._events):
            if not event.is_terminal:
                pruned_indices.append(idx)
                if len(pruned_indices) >= excess:
                    break

        # If all events are critical, fall back to pruning the absolute oldest
        if len(pruned_indices) < excess:
            for idx, event in enumerate(self._events):
                if idx not in pruned_indices:
                    pruned_indices.append(idx)
                    if len(pruned_indices) >= excess:
                        break

        # Remove in reverse index order
        for idx in sorted(pruned_indices, reverse=True):
            widget = self._item_widgets.pop(idx)
            widget.setParent(None)
            widget.deleteLater()
            self._events.pop(idx)

        self._update_event_count()

    def _update_event_count(self) -> None:
        """Update count badge text."""
        count = len(self._events)
        self.lbl_count_badge.setText(f"{count} event" if count == 1 else f"{count} events")

    def _on_scroll_changed(self, value: int) -> None:
        """Detect whether user scrolled up away from the latest event."""
        scrollbar = self._scroll_area.verticalScrollBar()
        # If within 15 pixels of the bottom, user is considered at the bottom
        at_bottom = value >= (scrollbar.maximum() - 15)
        self._auto_scroll_enabled = at_bottom
        self.btn_scroll_latest.setVisible(not at_bottom and len(self._events) > 3)

    def _on_scroll_range_changed(self, min_val: int, max_val: int) -> None:
        """Auto-scroll to maximum when content height expands if auto-scroll is enabled."""
        if self._auto_scroll_enabled:
            self._scroll_area.verticalScrollBar().setValue(max_val)
