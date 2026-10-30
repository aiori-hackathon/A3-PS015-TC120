"""Monitoring Test History Dialog for TicNet desktop presentation layer.

Displays a full-featured, searchable, and filterable dialog showing all historical
monitoring runs with complete metrics (Timestamp, Trigger, Status, Latency, Jitter,
Packet Loss, Download Speed, and Run ID / Execution Details).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from ticnet.core.timezone import format_ist, to_ist
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.models.monitoring import (
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringTriggerType,
)


class MonitoringHistoryDialog(QDialog):
    """Full modal dialog displaying comprehensive historical monitoring test records."""

    def __init__(
        self,
        monitoring_service: Optional[MonitoringService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.monitoring_service = monitoring_service
        self.setWindowTitle("TicNet — Monitoring Test History")
        self.setModal(True)
        self.resize(960, 600)
        self.setMinimumSize(800, 480)

        self._all_runs: List[MonitoringRun] = []
        self._filter_mode: str = "ALL"

        self._init_ui()
        self.refresh_runs()

    def _init_ui(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #f8fafc;
                font-family: 'Segoe UI', -apple-system, sans-serif;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # ---------------------------------------------------------------------
        # 1. Header Row: Title, Subtitle, Close Button
        # ---------------------------------------------------------------------
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title_lbl = QLabel("Monitoring Test History", self)
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #0f172a;")
        title_col.addWidget(title_lbl)

        sub_lbl = QLabel(
            "Complete chronological log of automated scheduled and manual diagnostic executions.",
            self,
        )
        sub_lbl.setStyleSheet("font-size: 12px; color: #64748b;")
        title_col.addWidget(sub_lbl)
        hdr_row.addLayout(title_col)
        hdr_row.addStretch()

        self.lbl_run_count_badge = QLabel("0 runs", self)
        self.lbl_run_count_badge.setStyleSheet("""
            background-color: #eff6ff;
            color: #1d4ed8;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 12px;
            border-radius: 12px;
            border: 1px solid #bfdbfe;
        """)
        hdr_row.addWidget(self.lbl_run_count_badge)

        layout.addLayout(hdr_row)

        # ---------------------------------------------------------------------
        # 2. Controls Row: Filter Chips & Refresh
        # ---------------------------------------------------------------------
        ctrl_card = QFrame(self)
        ctrl_card.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
        """)
        ctrl_layout = QHBoxLayout(ctrl_card)
        ctrl_layout.setContentsMargins(12, 8, 12, 8)
        ctrl_layout.setSpacing(8)

        lbl_filter = QLabel("Filter:", ctrl_card)
        lbl_filter.setStyleSheet("font-size: 12px; font-weight: 600; color: #475569;")
        ctrl_layout.addWidget(lbl_filter)

        chip_style = """
            QPushButton {
                background-color: #f1f5f9;
                color: #475569;
                border: 1px solid #cbd5e1;
                border-radius: 12px;
                padding: 3px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:checked {
                background-color: #2563eb;
                color: #ffffff;
                border-color: #2563eb;
            }
            QPushButton:hover:!checked {
                background-color: #e2e8f0;
            }
        """

        self.btn_all = QPushButton("All Runs", ctrl_card)
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        self.btn_all.setStyleSheet(chip_style)
        self.btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_all.clicked.connect(lambda: self._set_filter("ALL"))

        self.btn_completed = QPushButton("Completed", ctrl_card)
        self.btn_completed.setCheckable(True)
        self.btn_completed.setStyleSheet(chip_style)
        self.btn_completed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_completed.clicked.connect(lambda: self._set_filter("COMPLETED"))

        self.btn_scheduled = QPushButton("Scheduled", ctrl_card)
        self.btn_scheduled.setCheckable(True)
        self.btn_scheduled.setStyleSheet(chip_style)
        self.btn_scheduled.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scheduled.clicked.connect(lambda: self._set_filter("SCHEDULED"))

        self.btn_manual = QPushButton("Manual", ctrl_card)
        self.btn_manual.setCheckable(True)
        self.btn_manual.setStyleSheet(chip_style)
        self.btn_manual.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual.clicked.connect(lambda: self._set_filter("MANUAL"))

        self.btn_skipped = QPushButton("Skipped / Failed", ctrl_card)
        self.btn_skipped.setCheckable(True)
        self.btn_skipped.setStyleSheet(chip_style)
        self.btn_skipped.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_skipped.clicked.connect(lambda: self._set_filter("SKIPPED"))

        self._filter_grp = QButtonGroup(self)
        self._filter_grp.setExclusive(True)
        self._filter_grp.addButton(self.btn_all)
        self._filter_grp.addButton(self.btn_completed)
        self._filter_grp.addButton(self.btn_scheduled)
        self._filter_grp.addButton(self.btn_manual)
        self._filter_grp.addButton(self.btn_skipped)

        ctrl_layout.addWidget(self.btn_all)
        ctrl_layout.addWidget(self.btn_completed)
        ctrl_layout.addWidget(self.btn_scheduled)
        ctrl_layout.addWidget(self.btn_manual)
        ctrl_layout.addWidget(self.btn_skipped)
        ctrl_layout.addStretch()

        btn_refresh = QPushButton("Refresh", ctrl_card)
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #f8fafc;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
            }
        """)
        btn_refresh.clicked.connect(self.refresh_runs)
        ctrl_layout.addWidget(btn_refresh)

        layout.addWidget(ctrl_card)

        # ---------------------------------------------------------------------
        # 3. Comprehensive Data Table
        # ---------------------------------------------------------------------
        self.table = QTableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Timestamp (IST)",
            "Trigger",
            "Status",
            "Latency",
            "Jitter",
            "Loss",
            "Throughput",
            "Run ID / Details",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                gridline-color: transparent;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 8px 10px;
                border-bottom: 1px solid #f1f5f9;
            }
            QTableWidget::item:selected {
                background-color: #eff6ff;
                color: #1e3a8a;
            }
            QHeaderView::section {
                background-color: #f8fafc;
                color: #475569;
                font-size: 11px;
                font-weight: 700;
                padding: 8px 10px;
                border: none;
                border-bottom: 2px solid #e2e8f0;
            }
        """)

        # Column Sizing
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.table)

        # ---------------------------------------------------------------------
        # 4. Footer Row: Close Button
        # ---------------------------------------------------------------------
        footer_row = QHBoxLayout()
        footer_row.addStretch()

        btn_close = QPushButton("Close", self)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setFixedHeight(34)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                font-size: 12px;
                font-weight: 600;
                border: none;
                border-radius: 6px;
                padding: 0 20px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        btn_close.clicked.connect(self.accept)
        footer_row.addWidget(btn_close)

        layout.addLayout(footer_row)

    def _set_filter(self, mode: str) -> None:
        self._filter_mode = mode
        self._populate_table()

    def refresh_runs(self) -> None:
        """Query monitoring service for historical runs."""
        if not self.monitoring_service:
            self._all_runs = []
        else:
            self._all_runs = self.monitoring_service.list_runs(limit=300)
        self._populate_table()

    def _populate_table(self) -> None:
        # Apply active filter
        runs = self._all_runs
        if self._filter_mode == "COMPLETED":
            runs = [r for r in runs if r.status in ("COMPLETED", MonitoringRunStatus.COMPLETED.value)]
        elif self._filter_mode == "SCHEDULED":
            runs = [r for r in runs if r.trigger_type == "SCHEDULED"]
        elif self._filter_mode == "MANUAL":
            runs = [r for r in runs if r.trigger_type == "MANUAL"]
        elif self._filter_mode == "SKIPPED":
            runs = [r for r in runs if r.status in ("SKIPPED", "FAILED", MonitoringRunStatus.SKIPPED.value, MonitoringRunStatus.FAILED.value)]

        self.lbl_run_count_badge.setText(f"{len(runs)} run{'s' if len(runs) != 1 else ''}")
        self.table.setRowCount(len(runs))

        for row, r in enumerate(runs):
            self.table.setRowHeight(row, 36)

            # 0. Timestamp (formatted in Indian Standard Time)
            time_display = r.started_at_utc or r.scheduled_for_utc or "—"
            formatted_time = format_ist(time_display)
            time_tooltip = f"Indian Standard Time: {formatted_time}" 

            item_time = QTableWidgetItem(formatted_time)
            item_time.setToolTip(time_tooltip)
            item_time.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
            self.table.setItem(row, 0, item_time)

            # 1. Trigger
            item_trigger = QTableWidgetItem(r.trigger_type)
            item_trigger.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_trigger.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            if r.trigger_type == "SCHEDULED":
                item_trigger.setForeground(QColor("#2563eb"))
            else:
                item_trigger.setForeground(QColor("#7c3aed"))
            self.table.setItem(row, 1, item_trigger)

            # 2. Status
            item_status = QTableWidgetItem(r.status)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_status.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            if r.status in ("COMPLETED", MonitoringRunStatus.COMPLETED.value):
                item_status.setForeground(QColor("#059669"))
            elif r.status in ("SKIPPED", MonitoringRunStatus.SKIPPED.value):
                item_status.setForeground(QColor("#d97706"))
            elif r.status in ("FAILED", MonitoringRunStatus.FAILED.value):
                item_status.setForeground(QColor("#dc2626"))
            elif r.status in ("RUNNING", MonitoringRunStatus.RUNNING.value):
                item_status.setForeground(QColor("#2563eb"))
            self.table.setItem(row, 2, item_status)

            # 3. Latency
            lat_str = f"{r.latency_ms:.1f} ms" if r.latency_ms is not None else "—"
            item_lat = QTableWidgetItem(lat_str)
            item_lat.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, item_lat)

            # 4. Jitter
            jit_str = f"{r.jitter_ms:.2f} ms" if r.jitter_ms is not None else "—"
            item_jit = QTableWidgetItem(jit_str)
            item_jit.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, item_jit)

            # 5. Loss
            loss_str = f"{r.packet_loss_pct:.1f}%" if r.packet_loss_pct is not None else "—"
            item_loss = QTableWidgetItem(loss_str)
            item_loss.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 5, item_loss)

            # 6. Throughput
            tp_str = f"{r.download_mbps:.1f} Mbps" if r.download_mbps is not None else "—"
            item_tp = QTableWidgetItem(tp_str)
            item_tp.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, item_tp)

            # 7. Details / Reason / Run ID
            details_str = r.skip_reason or r.error_message or (f"Run ID: {r.run_id}" if r.run_id else "Successful execution")
            item_details = QTableWidgetItem(details_str)
            item_details.setForeground(QColor("#64748b"))
            self.table.setItem(row, 7, item_details)

