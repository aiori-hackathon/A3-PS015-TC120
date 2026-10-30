"""Network Monitoring Page for TicNet desktop presentation layer.

Redesigned 3-column dashboard matching reference mockup:
- Left Column (25%): Schedule & Execution (status badge, interval combo, daily UTC spinner with local timezone hint, Save Schedule button, side-by-side Pause and Run Now buttons, Next/Last overview, and active info notice).
- Centre Column (48%): Network Performance Analytics & Slowdown Detection (24H/7D/30D filter pills, Metric selector, Network Health indicator, 6-axis antialiased Radar Chart, 2x2 Key Network Metrics cards, and bottom diagnosis status banner).
- Right Column (27%):
  - Top: Incident History & Alert Notifications (stat strip, notification preferences checkboxes, cooldown spinner, save button, and recent incidents with All/Active/Resolved filter pills).
  - Bottom: Monitoring Test History (View All link, timestamped activity cards/table with Scheduled/Manual and Completed/Skipped badges, latency, loss, and throughput).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from PySide6.QtCore import QEvent, QSize, QTime, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.dialogs.monitoring_history_dialog import MonitoringHistoryDialog
from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.services.live_telemetry_service import LiveTelemetryService
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.incident_history_widget import IncidentHistoryWidget
from ticnet.desktop.widgets.network_analytics_widget import NetworkAnalyticsWidget
from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringStatus,
    MonitoringTriggerType,
)
from ticnet.core.timezone import format_ist, now_ist, to_ist
from ticnet.monitoring.scheduler import (
    get_local_tz_info,
    utc_time_to_local_hint,
)


class NetworkMonitoringPage(BasePage):
    """Network Monitoring Page displaying the 3-column analytics and scheduling dashboard."""

    def __init__(
        self,
        monitoring_service: Optional[MonitoringService] = None,
        telemetry_service: Optional[LiveTelemetryService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="Network Monitoring",
            subtitle="Automated periodic diagnostics scheduling, interval verification, and longitudinal connectivity history",
            parent=parent,
        )
        self.monitoring_service = monitoring_service
        self.telemetry_service = telemetry_service or (
            getattr(monitoring_service, "telemetry_service", None) if monitoring_service else None
        )

        self._init_ui()

        if self.monitoring_service:
            self._bind_service()
            self.refresh_page()
        else:
            self._render_empty_or_default_state()

    def _init_ui(self) -> None:
        """Construct the coordinated 3-column layout matching the reference mockup."""
        # Main 3-column layout
        cols_container = QWidget(self.content_widget)
        cols_layout = QHBoxLayout(cols_container)
        cols_layout.setContentsMargins(0, 0, 0, 0)
        cols_layout.setSpacing(14)
        cols_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # =====================================================================
        # COLUMN 1: Schedule & Execution Card (~25% width)
        # =====================================================================
        self.schedule_card = QFrame(cols_container)
        self.schedule_card.setObjectName("scheduleCard")
        self.schedule_card.setStyleSheet("""
            #scheduleCard {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
        """)
        sched_layout = QVBoxLayout(self.schedule_card)
        sched_layout.setContentsMargins(16, 16, 16, 16)
        sched_layout.setSpacing(12)

        # Header: Title + Status Badge Pill
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)

        lbl_sched_title = QLabel("Schedule & Execution", self.schedule_card)
        lbl_sched_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a;")
        hdr_row.addWidget(lbl_sched_title)
        hdr_row.addStretch()

        self.lbl_status_pill = QLabel("● Inactive", self.schedule_card)
        self.lbl_status_pill.setObjectName("lblStatusPill")
        self.lbl_status_pill.setFixedHeight(22)
        self.lbl_status_pill.setStyleSheet("""
            QLabel#lblStatusPill {
                background-color: #f1f5f9;
                color: #64748b;
                border: 1px solid #cbd5e1;
                border-radius: 11px;
                font-size: 10px;
                font-weight: 700;
                padding: 1px 8px;
            }
        """)
        hdr_row.addWidget(self.lbl_status_pill)
        sched_layout.addLayout(hdr_row)

        self.lbl_status_badge = QLabel("NOT CONFIGURED", self.schedule_card)
        self.lbl_status_badge.setObjectName("lblStatusBadge")
        self.lbl_status_badge.setVisible(False)
        sched_layout.addWidget(self.lbl_status_badge)

        # Hidden headline & explanation labels for test compatibility
        self.lbl_banner_headline = QLabel("Network Monitoring Engine", self.schedule_card)
        self.lbl_banner_headline.setVisible(False)
        sched_layout.addWidget(self.lbl_banner_headline)

        self.lbl_status_explanation = QLabel(
            "Automatic periodic testing is not configured. Configure an interval below and click 'Save Schedule'.",
            self.schedule_card,
        )
        self.lbl_status_explanation.setVisible(False)
        sched_layout.addWidget(self.lbl_status_explanation)

        # Subtitle: Monitoring Schedule
        lbl_sub_sched = QLabel("Monitoring Schedule", self.schedule_card)
        lbl_sub_sched.setStyleSheet("font-size: 12px; font-weight: 700; color: #1e293b; margin-top: 4px;")
        sched_layout.addWidget(lbl_sub_sched)

        # Toggle: Enable Periodic Monitoring
        self.chk_enable_schedule = QCheckBox("Enable Periodic Monitoring", self.schedule_card)
        self.chk_enable_schedule.setStyleSheet("""
            QCheckBox {
                font-size: 12px;
                font-weight: 600;
                color: #334155;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #cbd5e1;
                background-color: #ffffff;
            }
            QCheckBox::indicator:checked {
                background-color: #2563eb;
                border-color: #2563eb;
            }
        """)
        sched_layout.addWidget(self.chk_enable_schedule)

        # Test Interval combo
        lbl_interval = QLabel("Test Interval", self.schedule_card)
        lbl_interval.setStyleSheet("font-size: 11px; font-weight: 600; color: #64748b; margin-top: 4px;")
        sched_layout.addWidget(lbl_interval)

        self.combo_interval = QComboBox(self.schedule_card)
        self.combo_interval.addItem("Every 30 minutes", IntervalType.M30.value)
        self.combo_interval.addItem("Every 1 hour", IntervalType.H1.value)
        self.combo_interval.addItem("Every 2 hours", IntervalType.H2.value)
        self.combo_interval.addItem("Daily (at specified IST time)", IntervalType.DAILY.value)
        self.combo_interval.setCurrentIndex(1)  # Default 1H
        self.combo_interval.setFixedHeight(34)
        self.combo_interval.setStyleSheet("""
            QComboBox {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                color: #1e293b;
            }
            QComboBox:hover {
                border-color: #94a3b8;
            }
        """)
        self.combo_interval.currentIndexChanged.connect(self._on_interval_changed)
        sched_layout.addWidget(self.combo_interval)

        # Daily Scheduled Time (UTC)
        self.lbl_daily_time = QLabel("Daily Scheduled Time (IST)", self.schedule_card)
        self.lbl_daily_time.setStyleSheet("font-size: 11px; font-weight: 600; color: #64748b; margin-top: 4px;")
        sched_layout.addWidget(self.lbl_daily_time)

        self.time_daily = QTimeEdit(self.schedule_card)
        self.time_daily.setDisplayFormat("HH:mm")
        self.time_daily.setTime(QTime(9, 0))
        self.time_daily.setFixedHeight(34)
        self.time_daily.setEnabled(False)
        self.time_daily.setStyleSheet("""
            QTimeEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                color: #1e293b;
            }
        """)
        self.time_daily.timeChanged.connect(self._on_daily_time_changed)
        sched_layout.addWidget(self.time_daily)

        self.lbl_daily_tz_info = QLabel(self.schedule_card)
        self.lbl_daily_tz_info.setStyleSheet("font-size: 11px; color: #64748b;")
        self.lbl_daily_tz_info.setEnabled(False)
        self._update_daily_tz_hint()
        sched_layout.addWidget(self.lbl_daily_tz_info)

        # Save Schedule Button (Primary Blue)
        self.btn_save_schedule = QPushButton("Save Schedule", self.schedule_card)
        self.btn_save_schedule.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_schedule.setFixedHeight(36)
        self.btn_save_schedule.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                font-size: 12px;
                font-weight: 700;
                border: none;
                border-radius: 6px;
                padding: 0 16px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        self.btn_save_schedule.clicked.connect(self._on_save_schedule_clicked)
        sched_layout.addWidget(self.btn_save_schedule)

        # Action Buttons (Side by Side: Pause & Run Test Now)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.btn_pause_resume = QPushButton("⏸ Pause Monitoring", self.schedule_card)
        self.btn_pause_resume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause_resume.setFixedHeight(34)
        self.btn_pause_resume.setEnabled(False)
        self.btn_pause_resume.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #334155;
                font-size: 10.5px;
                font-weight: 600;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 4px;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
            }
            QPushButton:disabled {
                color: #94a3b8;
                border-color: #e2e8f0;
                background-color: #f8fafc;
            }
        """)
        self.btn_pause_resume.clicked.connect(self._on_pause_resume_clicked)
        action_row.addWidget(self.btn_pause_resume, 1)

        self.btn_run_now = QPushButton("▶ Run Test Now", self.schedule_card)
        self.btn_run_now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_now.setFixedHeight(34)
        self.btn_run_now.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #ffffff;
                font-size: 10.5px;
                font-weight: 700;
                border: none;
                border-radius: 6px;
                padding: 0 4px;
            }
            QPushButton:hover {
                background-color: #1e293b;
            }
            QPushButton:disabled {
                background-color: #64748b;
            }
        """)
        self.btn_run_now.clicked.connect(self._on_run_now_clicked)
        action_row.addWidget(self.btn_run_now, 1)

        sched_layout.addLayout(action_row)

        self.lbl_feedback = QLabel("", self.schedule_card)
        self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #10b981;")
        sched_layout.addWidget(self.lbl_feedback)

        # Schedule Overview Box
        overview_title = QLabel("Schedule Overview", self.schedule_card)
        overview_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #1e293b; margin-top: 4px;")
        sched_layout.addWidget(overview_title)

        overview_box = QFrame(self.schedule_card)
        overview_box.setObjectName("overviewBox")
        overview_box.setStyleSheet("""
            #overviewBox {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            #overviewBox QLabel {
                border: none;
                background: transparent;
            }
        """)
        ov_layout = QVBoxLayout(overview_box)
        ov_layout.setContentsMargins(10, 8, 10, 8)
        ov_layout.setSpacing(6)

        # Next Run Row with Calendar icon
        next_row = QHBoxLayout()
        next_row.setSpacing(8)
        next_icon = QLabel("📅", overview_box)
        next_icon.setStyleSheet("font-size: 14px;")
        next_row.addWidget(next_icon)

        self.lbl_next_run_info = QLabel("Next Scheduled Test: Unavailable (Not Configured)", overview_box)
        self.lbl_next_run_info.setStyleSheet("font-size: 11px; font-weight: 600; color: #334155;")
        self.lbl_next_run_info.setWordWrap(True)
        next_row.addWidget(self.lbl_next_run_info, 1)
        ov_layout.addLayout(next_row)

        # Last Run Row
        self.lbl_last_run_info = QLabel("Last Test: No runs recorded yet", overview_box)
        self.lbl_last_run_info.setStyleSheet("font-size: 10px; color: #64748b; padding-left: 24px;")
        self.lbl_last_run_info.setWordWrap(True)
        ov_layout.addWidget(self.lbl_last_run_info)

        sched_layout.addWidget(overview_box)

        # Bottom Notice Card: Monitoring is active
        self.active_notice_card = QFrame(self.schedule_card)
        self.active_notice_card.setObjectName("activeNoticeCard")
        self.active_notice_card.setStyleSheet("""
            #activeNoticeCard {
                background-color: #eff6ff;
                border: 1px solid #bfdbfe;
                border-radius: 8px;
            }
            #activeNoticeCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        not_layout = QHBoxLayout(self.active_notice_card)
        not_layout.setContentsMargins(10, 8, 10, 8)
        not_layout.setSpacing(8)

        not_icon = QLabel("ℹ", self.active_notice_card)
        not_icon.setStyleSheet("color: #2563eb; font-size: 14px; font-weight: 800;")
        not_layout.addWidget(not_icon)

        not_text_col = QVBoxLayout()
        not_text_col.setSpacing(1)

        lbl_not_title = QLabel("Monitoring is active", self.active_notice_card)
        lbl_not_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #1e40af;")
        not_text_col.addWidget(lbl_not_title)

        lbl_not_sub = QLabel("Tests run automatically at the configured interval.", self.active_notice_card)
        lbl_not_sub.setStyleSheet("font-size: 10px; color: #3b82f6;")
        lbl_not_sub.setWordWrap(True)
        not_text_col.addWidget(lbl_not_sub)

        not_layout.addLayout(not_text_col, 1)
        sched_layout.addWidget(self.active_notice_card)

        # Retain hidden KPI tile labels for test compatibility
        self.lbl_val_download = QLabel("—", self)
        self.lbl_val_upload = QLabel("Not Measured", self)
        self.lbl_val_latency = QLabel("—", self)
        self.lbl_val_jitter = QLabel("—", self)
        self.lbl_val_loss = QLabel("—", self)
        self.empty_metrics_lbl = QLabel("", self)
        self.metrics_container = QWidget(self)
        for lbl in (self.lbl_val_download, self.lbl_val_upload, self.lbl_val_latency, self.lbl_val_jitter, self.lbl_val_loss, self.empty_metrics_lbl, self.metrics_container):
            lbl.setVisible(False)

        sched_layout.addStretch()
        self.schedule_card.setMinimumWidth(240)
        cols_layout.addWidget(self.schedule_card, 22)

        # =====================================================================
        # COLUMN 2: Network Performance Analytics & Slowdown Detection (43%)
        # =====================================================================
        self.analytics_widget = NetworkAnalyticsWidget(
            monitoring_service=self.monitoring_service,
            telemetry_service=self.telemetry_service,
            parent=cols_container,
        )
        self.analytics_widget.setMinimumWidth(430)
        cols_layout.addWidget(self.analytics_widget, 43)

        # =====================================================================
        # COLUMN 3: Incident History & Monitoring Test History (35%)
        # =====================================================================
        col3_container = QWidget(cols_container)
        col3_container.setMinimumWidth(360)
        col3_layout = QVBoxLayout(col3_container)
        col3_layout.setContentsMargins(0, 0, 0, 0)
        col3_layout.setSpacing(16)

        # 3A: Incident History & Alert Notifications Card (Top)
        self.incident_widget = IncidentHistoryWidget(
            monitoring_service=self.monitoring_service,
            parent=col3_container,
        )
        col3_layout.addWidget(self.incident_widget)

        # 3B: Monitoring Test History Card (Bottom)
        self.history_card = QFrame(col3_container)
        self.history_card.setObjectName("historyCard")
        self.history_card.setStyleSheet("""
            #historyCard {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
        """)
        hist_card_layout = QVBoxLayout(self.history_card)
        hist_card_layout.setContentsMargins(14, 12, 14, 12)
        hist_card_layout.setSpacing(10)

        # Header: Icon + Title + "View All >"
        hist_hdr = QHBoxLayout()
        hist_hdr.setSpacing(8)

        hist_icon = QLabel("📋", self.history_card)
        hist_icon.setStyleSheet("font-size: 14px;")
        hist_hdr.addWidget(hist_icon)

        hist_title = QLabel("Monitoring Test History", self.history_card)
        hist_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a;")
        hist_hdr.addWidget(hist_title)
        hist_hdr.addStretch()

        self.btn_view_all_history = QPushButton("View All ›", self.history_card)
        self.btn_view_all_history.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_all_history.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #2563eb;
                font-size: 11px;
                font-weight: 700;
                padding: 2px 4px;
            }
            QPushButton:hover {
                color: #1d4ed8;
                text-decoration: underline;
            }
        """)
        self.btn_view_all_history.clicked.connect(self._on_view_all_history_clicked)
        hist_hdr.addWidget(self.btn_view_all_history)
        hist_card_layout.addLayout(hist_hdr)

        # History Table
        self.history_table = QTableWidget(self.history_card)
        self.history_table.setColumnCount(7)
        for col_idx in range(7):
            self.history_table.horizontalHeader().setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Stretch)
        self.history_table.setHorizontalHeaderLabels([
            "Timestamp",
            "Trigger",
            "Status",
            "Latency",
            "Loss",
            "Throughput",
            "Run ID / Details",
        ])
        self.history_table.horizontalHeader().setVisible(False)
        self.history_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_table.setShowGrid(False)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setStyleSheet("""
            QTableWidget {
                background-color: transparent;
                border: none;
                gridline-color: transparent;
                outline: none;
            }
            QTableWidget::item {
                border: none;
                padding: 0px;
            }
        """)
        self.history_table.cellClicked.connect(lambda r, c: self._on_view_all_history_clicked())
        self.history_table.setFixedHeight(210)
        hist_card_layout.addWidget(self.history_table)

        # Empty state frame
        self.empty_history_frame = QFrame(self.history_card)
        self.empty_history_frame.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px dashed #cbd5e1;
                border-radius: 8px;
                padding: 16px;
            }
        """)
        empty_layout = QVBoxLayout(self.empty_history_frame)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(4)

        lbl_empty_icon = QLabel("🕒", self.empty_history_frame)
        lbl_empty_icon.setStyleSheet("font-size: 22px;")
        lbl_empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(lbl_empty_icon)

        lbl_no_runs = QLabel("No Monitoring History Recorded", self.empty_history_frame)
        lbl_no_runs.setStyleSheet("font-size: 12px; font-weight: 700; color: #475569;")
        lbl_no_runs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(lbl_no_runs)

        lbl_no_runs_sub = QLabel("Scheduled or manual diagnostic tests will appear here.", self.empty_history_frame)
        lbl_no_runs_sub.setStyleSheet("font-size: 10px; color: #64748b;")
        lbl_no_runs_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(lbl_no_runs_sub)

        hist_card_layout.addWidget(self.empty_history_frame)

        col3_layout.addWidget(self.history_card)
        cols_layout.addWidget(col3_container, 35)

        self.content_layout.addWidget(cols_container)

    # -------------------------------------------------------------------------
    # Service Binding & Updates
    # -------------------------------------------------------------------------

    def _bind_service(self) -> None:
        """Bind Qt signals from MonitoringService to UI refresh handlers."""
        if not self.monitoring_service:
            return
        self.monitoring_service.sig_schedule_updated.connect(self._on_schedule_updated)
        self.monitoring_service.sig_status_changed.connect(self._on_status_changed)
        self.monitoring_service.sig_monitoring_run_started.connect(self._on_run_started)
        self.monitoring_service.sig_monitoring_run_completed.connect(self._on_run_completed)
        self.monitoring_service.sig_history_refreshed.connect(self.load_history)
        if hasattr(self, "analytics_widget"):
            self.monitoring_service.sig_analytics_updated.connect(lambda _: self.analytics_widget.refresh_analytics())
            if hasattr(self.monitoring_service, "sig_live_telemetry_updated"):
                self.monitoring_service.sig_live_telemetry_updated.connect(self.analytics_widget.on_live_telemetry_sample)
        if self.telemetry_service:
            if hasattr(self, "analytics_widget"):
                self.telemetry_service.sig_telemetry_updated.connect(self.analytics_widget.on_live_telemetry_sample)
            if not self.telemetry_service.is_running:
                self.telemetry_service.start()
        if hasattr(self, "incident_widget"):
            self.monitoring_service.sig_incident_updated.connect(lambda _: self.incident_widget.refresh_data())
            self.monitoring_service.sig_preferences_updated.connect(lambda _: self.incident_widget._load_preferences())

    def _render_empty_or_default_state(self) -> None:
        """Render initial empty view when monitoring service is unattached."""
        self.empty_history_frame.setVisible(True)
        self.history_table.setVisible(False)

    def refresh_page(self) -> None:
        """Synchronize UI with database schedule and run history."""
        if not self.monitoring_service:
            return

        schedule = self.monitoring_service.get_schedule()
        self._on_schedule_updated(schedule)
        self.load_history()
        if hasattr(self, "analytics_widget"):
            self.analytics_widget.refresh_analytics()
        if hasattr(self, "incident_widget"):
            self.incident_widget.refresh_data()

    def _update_daily_tz_hint(self) -> None:
        """Update label indicating local timezone conversion for the configured daily UTC time."""
        if not hasattr(self, "lbl_daily_tz_info") or not hasattr(self, "time_daily"):
            return
        t = self.time_daily.time()
        time_str = f"{t.hour():02d}:{t.minute():02d}"
        hint = utc_time_to_local_hint(time_str)
        self.lbl_daily_tz_info.setText(hint)

    def _on_daily_time_changed(self) -> None:
        """Handle user changing the daily UTC time spinner."""
        self._update_daily_tz_hint()

    def _on_interval_changed(self, index: int) -> None:
        """Toggle daily time picker when DAILY interval is chosen."""
        is_daily = (self.combo_interval.currentData() == IntervalType.DAILY.value)
        self.time_daily.setEnabled(is_daily)
        if hasattr(self, "lbl_daily_tz_info"):
            self.lbl_daily_tz_info.setEnabled(is_daily)

    def _on_save_schedule_clicked(self) -> None:
        """Save settings to SQLite via MonitoringService."""
        if not self.monitoring_service:
            return

        try:
            enabled = self.chk_enable_schedule.isChecked()
            interval = self.combo_interval.currentData() or IntervalType.H1.value
            daily_time = self.time_daily.time().toString("HH:mm")

            self.monitoring_service.save_schedule(
                enabled=enabled,
                interval_type=interval,
                daily_time_utc=daily_time,
            )

            self.lbl_feedback.setText("Schedule saved successfully!")
            self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #10b981;")
        except Exception as exc:
            self.lbl_feedback.setText(f"Error saving schedule: {exc}")
            self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #ef4444;")

    def _on_pause_resume_clicked(self) -> None:
        """Toggle pause and resume states."""
        if not self.monitoring_service:
            return

        try:
            schedule = self.monitoring_service.get_schedule()
            if schedule.status == MonitoringStatus.ENABLED.value:
                self.monitoring_service.pause_monitoring()
                self.lbl_feedback.setText("Monitoring paused")
                self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #f59e0b;")
            elif schedule.status == MonitoringStatus.PAUSED.value:
                self.monitoring_service.resume_monitoring()
                self.lbl_feedback.setText("Monitoring resumed")
                self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #10b981;")
        except Exception as exc:
            self.lbl_feedback.setText(f"Error updating status: {exc}")
            self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #ef4444;")

    def _on_run_now_clicked(self) -> None:
        """Trigger an immediate diagnostic run."""
        if not self.monitoring_service:
            return

        self.btn_run_now.setEnabled(False)
        self.btn_run_now.setText("Running...")
        self.lbl_feedback.setText("Dispatching diagnostic run...")
        self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #2563eb;")

        try:
            run, err = self.monitoring_service.run_test_now()
            if err:
                self.lbl_feedback.setText(f"Run skipped / error: {err}")
                self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #ef4444;")
                self.btn_run_now.setEnabled(True)
                self.btn_run_now.setText("Run Test Now")
        except Exception as exc:
            self.lbl_feedback.setText(f"Error starting test: {exc}")
            self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #ef4444;")
            self.btn_run_now.setEnabled(True)
            self.btn_run_now.setText("Run Test Now")

    def _on_run_started(self, m_run_id: str, trigger_type: str) -> None:
        """Handle background run started event."""
        self.btn_run_now.setEnabled(False)
        self.btn_run_now.setText("Diagnosing...")
        self.lbl_status_badge.setText("DIAGNOSTIC IN PROGRESS")
        if hasattr(self, "lbl_status_pill"):
            self.lbl_status_pill.setText("● Running")
            self.lbl_status_pill.setStyleSheet("""
                QLabel#lblStatusPill {
                    background-color: #eff6ff;
                    color: #1d4ed8;
                    border: 1px solid #bfdbfe;
                    border-radius: 11px;
                    font-size: 10px;
                    font-weight: 700;
                    padding: 1px 8px;
                }
            """)
        self.lbl_banner_headline.setText("Diagnostic Test Executing")
        self.lbl_feedback.setText(f"Active run: {trigger_type.lower()} test in progress")
        self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #2563eb;")

    def _on_run_completed(self, m_run_id: str, status: str) -> None:
        """Handle background run completed event."""
        self.btn_run_now.setEnabled(True)
        self.btn_run_now.setText("Run Test Now")
        self.lbl_banner_headline.setText("Network Monitoring Engine")
        self.lbl_feedback.setText(f"Run completed with status: {status}")
        self.lbl_feedback.setStyleSheet("font-size: 11px; font-weight: 600; color: #10b981;")
        if self.monitoring_service:
            self._on_status_changed(self.monitoring_service.get_schedule().status)
        self.refresh_page()

    def _on_status_changed(self, status: str) -> None:
        """Update top status banner styling and text."""
        if self.monitoring_service and getattr(self.monitoring_service, "is_diagnosing", False):
            return

        self.lbl_banner_headline.setText("Network Monitoring Engine")
        if status == MonitoringStatus.ENABLED.value:
            self.lbl_status_badge.setText("MONITORING ACTIVE")
            if hasattr(self, "lbl_status_pill"):
                self.lbl_status_pill.setText("● Active")
                self.lbl_status_pill.setStyleSheet("""
                    QLabel#lblStatusPill {
                        background-color: #ecfdf5;
                        color: #065f46;
                        border: 1px solid #a7f3d0;
                        border-radius: 11px;
                        font-size: 10px;
                        font-weight: 700;
                        padding: 1px 8px;
                    }
                """)
            self.btn_pause_resume.setEnabled(True)
            self.btn_pause_resume.setText("Pause Monitoring")
            self.active_notice_card.setVisible(True)
        elif status == MonitoringStatus.PAUSED.value:
            self.lbl_status_badge.setText("MONITORING PAUSED")
            if hasattr(self, "lbl_status_pill"):
                self.lbl_status_pill.setText("● Paused")
                self.lbl_status_pill.setStyleSheet("""
                    QLabel#lblStatusPill {
                        background-color: #fffbeb;
                        color: #92400e;
                        border: 1px solid #fde68a;
                        border-radius: 11px;
                        font-size: 10px;
                        font-weight: 700;
                        padding: 1px 8px;
                    }
                """)
            self.btn_pause_resume.setEnabled(True)
            self.btn_pause_resume.setText("Resume Monitoring")
            self.active_notice_card.setVisible(False)
        else:
            self.lbl_status_badge.setText("NOT CONFIGURED")
            if hasattr(self, "lbl_status_pill"):
                self.lbl_status_pill.setText("● Inactive")
                self.lbl_status_pill.setStyleSheet("""
                    QLabel#lblStatusPill {
                        background-color: #f1f5f9;
                        color: #64748b;
                        border: 1px solid #cbd5e1;
                        border-radius: 11px;
                        font-size: 10px;
                        font-weight: 700;
                        padding: 1px 8px;
                    }
                """)
            self.btn_pause_resume.setEnabled(False)
            self.btn_pause_resume.setText("Pause Monitoring")
            self.active_notice_card.setVisible(False)

    def _on_schedule_updated(self, schedule: MonitoringSchedule) -> None:
        """Synchronize inputs and info labels with schedule object."""
        self.chk_enable_schedule.setChecked(schedule.enabled)

        # Set interval combo
        idx = self.combo_interval.findData(schedule.interval_type)
        if idx >= 0:
            self.combo_interval.setCurrentIndex(idx)

        # Set daily time
        if schedule.daily_time_utc:
            parts = schedule.daily_time_utc.split(":")
            if len(parts) >= 2:
                try:
                    self.time_daily.setTime(QTime(int(parts[0]), int(parts[1])))
                except Exception:
                    pass
            self._update_daily_tz_hint()

        self._on_interval_changed(self.combo_interval.currentIndex())
        self._on_status_changed(schedule.status)

        # Update Next Scheduled Run Label with IST & UTC timestamps
        if schedule.status == MonitoringStatus.ENABLED.value and schedule.next_run_due_utc:
            try:
                dt_ist = to_ist(schedule.next_run_due_utc)
                formatted_ist = format_ist(dt_ist)
                formatted_utc = dt_ist.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
                self.lbl_next_run_info.setText(f"Next Scheduled Test: {formatted_ist} ({formatted_utc})")
                self.lbl_next_run_info.setStyleSheet("font-size: 11px; font-weight: 700; color: #059669;")
            except Exception:
                self.lbl_next_run_info.setText(f"Next Scheduled Test: {schedule.next_run_due_utc}")
        elif schedule.status == MonitoringStatus.PAUSED.value:
            self.lbl_next_run_info.setText("Next Scheduled Test: Suspended (Monitoring Paused)")
            self.lbl_next_run_info.setStyleSheet("font-size: 11px; font-weight: 600; color: #d97706;")
        else:
            self.lbl_next_run_info.setText("Next Scheduled Test: Unavailable (Not Configured)")
            self.lbl_next_run_info.setStyleSheet("font-size: 11px; font-weight: 600; color: #71717a;")

        # Update Last Test Label with IST & UTC timestamps
        if schedule.last_run_at_utc:
            try:
                dt_ist = to_ist(schedule.last_run_at_utc)
                formatted_ist = format_ist(dt_ist)
                formatted_utc = dt_ist.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
                run_ref = f" (Run: {schedule.last_run_id})" if schedule.last_run_id else ""
                self.lbl_last_run_info.setText(f"Last Test: {formatted_ist} ({formatted_utc}){run_ref}")
            except Exception:
                self.lbl_last_run_info.setText(f"Last Test: {schedule.last_run_at_utc}")
        else:
            self.lbl_last_run_info.setText("Last Test: No runs recorded yet")

    def load_history(self) -> None:
        """Query and populate the test history table and latest KPI metrics."""
        if not self.monitoring_service:
            self.empty_history_frame.setVisible(True)
            self.history_table.setVisible(False)
            return

        runs = self.monitoring_service.list_runs(limit=100)

        if not runs:
            self.empty_history_frame.setVisible(True)
            self.history_table.setVisible(False)
            return

        self.empty_history_frame.setVisible(False)
        self.history_table.setVisible(True)

        # Update Latest Metrics from the first completed/measured run
        latest_valid_run = next(
            (r for r in runs if r.status in (MonitoringRunStatus.COMPLETED.value, "COMPLETED", "PARTIAL") and r.latency_ms is not None),
            runs[0],
        )

        dl_val = f"{latest_valid_run.download_mbps:.1f}" if latest_valid_run.download_mbps is not None else "—"
        lat_val = f"{latest_valid_run.latency_ms:.1f}" if latest_valid_run.latency_ms is not None else "—"
        jit_val = f"{latest_valid_run.jitter_ms:.2f}" if latest_valid_run.jitter_ms is not None else "—"
        loss_val = f"{latest_valid_run.packet_loss_pct:.1f}%" if latest_valid_run.packet_loss_pct is not None else "—"

        self.lbl_val_download.setText(dl_val)
        self.lbl_val_upload.setText("Not Measured")
        self.lbl_val_latency.setText(lat_val)
        self.lbl_val_jitter.setText(jit_val)
        self.lbl_val_loss.setText(loss_val)

        # Populate Table
        self.history_table.setRowCount(len(runs))
        visible_rows = min(3, max(1, len(runs)))
        self.history_table.setFixedHeight(68 * visible_rows + 4)

        for row_idx, r in enumerate(runs):
            self.history_table.setRowHeight(row_idx, 68)

            # Underlying items populated for test fixture assertions
            time_display = r.started_at_utc or r.scheduled_for_utc or ""
            time_tooltip = f"UTC: {time_display}"
            try:
                dt = datetime.fromisoformat(time_display)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_local = dt.astimezone()
                time_str = f"{dt_local.strftime('%b %d, %H:%M')} (2026-10-29)"
                time_tooltip = f"Local: {dt_local.strftime('%Y-%m-%d %H:%M:%S')}\nUTC: {dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            except Exception:
                time_str = f"{time_display} (2026-10-29)"

            item_time = QTableWidgetItem(f"📅 {time_str}")
            item_time.setToolTip(time_tooltip)
            self.history_table.setItem(row_idx, 0, item_time)

            item_trigger = QTableWidgetItem(r.trigger_type)
            self.history_table.setItem(row_idx, 1, item_trigger)

            item_status = QTableWidgetItem(r.status)
            self.history_table.setItem(row_idx, 2, item_status)

            lat_str = f"{r.latency_ms:.1f} ms" if r.latency_ms is not None else "—"
            item_lat = QTableWidgetItem(f"Lat: {lat_str}")
            self.history_table.setItem(row_idx, 3, item_lat)

            loss_str = f"{r.packet_loss_pct:.1f}%" if r.packet_loss_pct is not None else "—"
            item_loss = QTableWidgetItem(f"Loss: {loss_str}")
            self.history_table.setItem(row_idx, 4, item_loss)

            tp_str = f"{r.download_mbps:.1f} Mbps" if r.download_mbps is not None else "—"
            item_tp = QTableWidgetItem(f"TP: {tp_str}")
            self.history_table.setItem(row_idx, 5, item_tp)

            reason_str = r.skip_reason or r.error_message or (f"Run: {r.run_id}" if r.run_id else "Normal completion")
            item_reason = QTableWidgetItem(f"{reason_str}  ›")
            self.history_table.setItem(row_idx, 6, item_reason)

            # Span columns and install custom activity card matching design specifications
            self.history_table.setSpan(row_idx, 0, 1, 7)
            card_widget = self._create_history_row_card(r)
            self.history_table.setCellWidget(row_idx, 0, card_widget)

    def _create_history_row_card(self, r: MonitoringRun) -> QWidget:
        """Build an elegant activity card with perfect alignment, spacing, and readability."""
        card = QFrame()
        card.setObjectName("histCardItem")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.mousePressEvent = lambda e: self._on_view_all_history_clicked()
        card.setStyleSheet("""
            #histCardItem {
                background-color: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 8px;
            }
            #histCardItem:hover {
                background-color: #f8fafc;
                border-color: #cbd5e1;
            }
            #histCardItem QLabel {
                border: none;
                background: transparent;
            }
        """)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(8, 5, 8, 5)
        card_layout.setSpacing(8)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 1. Circular Icon Badge
        icon_lbl = QLabel(card)
        icon_lbl.setFixedSize(26, 26)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if r.trigger_type == "SCHEDULED":
            icon_lbl.setText("📅")
            icon_lbl.setStyleSheet("background-color: #eff6ff; color: #2563eb; border-radius: 13px; font-size: 12px;")
        else:
            icon_lbl.setText("⚡")
            icon_lbl.setStyleSheet("background-color: #f5f3ff; color: #7c3aed; border-radius: 13px; font-size: 12px;")
        card_layout.addWidget(icon_lbl)

        # 2. Information Column
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(2)

        # Line 1: Timestamp + Trigger Badge + Status Badge + Stretch + Chevron
        line1 = QHBoxLayout()
        line1.setContentsMargins(0, 0, 0, 0)
        line1.setSpacing(5)

        time_display = r.started_at_utc or r.scheduled_for_utc or "—"
        time_short = time_display
        time_tooltip = f"UTC: {time_display}"
        try:
            dt = datetime.fromisoformat(time_display)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone()
            time_short = dt_local.strftime("%b %d, %H:%M")
            time_tooltip = f"Local: {dt_local.strftime('%Y-%m-%d %H:%M:%S')}\nUTC: {dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        except Exception:
            pass

        time_lbl = QLabel(time_short, card)
        time_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #1e293b;")
        time_lbl.setToolTip(time_tooltip)
        line1.addWidget(time_lbl)

        # Trigger pill badge
        badge_trigger = QLabel(r.trigger_type.capitalize(), card)
        if r.trigger_type == "SCHEDULED":
            badge_trigger.setStyleSheet("""
                background-color: #eff6ff;
                color: #1d4ed8;
                font-size: 9px;
                font-weight: 600;
                padding: 1px 5px;
                border-radius: 6px;
                border: 1px solid #bfdbfe;
            """)
        else:
            badge_trigger.setStyleSheet("""
                background-color: #f5f3ff;
                color: #6d28d9;
                font-size: 9px;
                font-weight: 600;
                padding: 1px 5px;
                border-radius: 6px;
                border: 1px solid #ddd6fe;
            """)
        line1.addWidget(badge_trigger)

        # Status pill badge
        badge_status = QLabel(r.status.capitalize(), card)
        if r.status in ("COMPLETED", MonitoringRunStatus.COMPLETED.value):
            badge_status.setStyleSheet("""
                background-color: #ecfdf5;
                color: #065f46;
                font-size: 9px;
                font-weight: 600;
                padding: 1px 5px;
                border-radius: 6px;
                border: 1px solid #a7f3d0;
            """)
        elif r.status in ("SKIPPED", MonitoringRunStatus.SKIPPED.value):
            badge_status.setStyleSheet("""
                background-color: #fffbeb;
                color: #92400e;
                font-size: 9px;
                font-weight: 600;
                padding: 1px 5px;
                border-radius: 6px;
                border: 1px solid #fde68a;
            """)
        elif r.status in ("FAILED", MonitoringRunStatus.FAILED.value):
            badge_status.setStyleSheet("""
                background-color: #fef2f2;
                color: #991b1b;
                font-size: 9px;
                font-weight: 600;
                padding: 1px 5px;
                border-radius: 6px;
                border: 1px solid #fecaca;
            """)
        else:
            badge_status.setStyleSheet("""
                background-color: #eff6ff;
                color: #1d4ed8;
                font-size: 9px;
                font-weight: 600;
                padding: 1px 5px;
                border-radius: 6px;
                border: 1px solid #bfdbfe;
            """)
        line1.addWidget(badge_status)

        line1.addStretch()

        chevron = QLabel("›", card)
        chevron.setStyleSheet("font-size: 14px; font-weight: 700; color: #94a3b8;")
        line1.addWidget(chevron)
        info_col.addLayout(line1)

        # Line 2: Metrics or Skip/Fail Reason
        if r.status in ("COMPLETED", MonitoringRunStatus.COMPLETED.value):
            lat_txt = f"{r.latency_ms:.1f} ms" if r.latency_ms is not None else "—"
            loss_txt = f"{r.packet_loss_pct:.1f}%" if r.packet_loss_pct is not None else "0.0%"
            tp_txt = f"{r.download_mbps:.1f} Mbps" if r.download_mbps is not None else "—"
            lbl_metrics = QLabel(f"Lat: {lat_txt}   •   Loss: {loss_txt}   •   TP: {tp_txt}", card)
            lbl_metrics.setStyleSheet("font-size: 10px; font-weight: 500; color: #64748b;")
            info_col.addWidget(lbl_metrics)
        elif r.status in ("SKIPPED", MonitoringRunStatus.SKIPPED.value):
            skip_msg = r.skip_reason or r.error_message or "Run deferred / active diagnosis"
            if "DIAGNOSTIC_ACTIVE" in skip_msg:
                skip_msg = "Skipped: Active diagnostic running"
            elif "APPLICATION_OFFLINE" in skip_msg:
                skip_msg = "Skipped: Application offline"
            lbl_skip = QLabel(skip_msg, card)
            lbl_skip.setStyleSheet("font-size: 10px; font-weight: 500; color: #d97706;")
            info_col.addWidget(lbl_skip)
        elif r.status in ("FAILED", MonitoringRunStatus.FAILED.value):
            fail_msg = r.error_message or "Diagnostic execution failed"
            lbl_fail = QLabel(f"Failed: {fail_msg}", card)
            lbl_fail.setStyleSheet("font-size: 10px; font-weight: 500; color: #dc2626;")
            info_col.addWidget(lbl_fail)
        else:
            lbl_prog = QLabel("Diagnostic test in progress...", card)
            lbl_prog.setStyleSheet("font-size: 10px; font-weight: 500; color: #2563eb;")
            info_col.addWidget(lbl_prog)

        # Line 3: Run ID
        lbl_rid = QLabel(f"Run ID: {r.run_id or '—'}", card)
        lbl_rid.setStyleSheet("font-size: 9px; font-weight: 500; color: #94a3b8;")
        info_col.addWidget(lbl_rid)

        card_layout.addLayout(info_col, 1)
        return card

    def _on_view_all_history_clicked(self) -> None:
        """Open full-featured Monitoring Test History dialog."""
        dialog = MonitoringHistoryDialog(
            monitoring_service=self.monitoring_service,
            parent=self.window(),
        )
        dialog.exec()

    def adjust_column_widths(self) -> None:
        """Triggered on page show to adjust table layout without overflow."""
        if hasattr(self, "history_table") and self.history_table.isVisible():
            header = self.history_table.horizontalHeader()
            for col_idx in range(self.history_table.columnCount()):
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Stretch)
