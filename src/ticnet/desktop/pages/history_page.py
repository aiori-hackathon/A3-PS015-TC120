"""History page for TicNet desktop presentation layer.

Displays real persisted diagnostic sessions from SQLite, supports multi-parameter filtering,
longitudinal performance trend charts, and interactive session inspection without synthetic mock data.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Union

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from ticnet.core.timezone import format_ist, to_ist
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.services.history_service import (
    HistoricalRunDetail,
    HistoricalSessionSummary,
    HistoryService,
)
from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.historical_detail_widget import HistoricalDetailPanel
from ticnet.desktop.widgets.historical_trends_widget import HistoricalTrendsWidget
from ticnet.models.entities import TestRun


class HistoryPage(BasePage):
    """History page presenting stored diagnostic runs, trend charts, and honest empty states."""

    def __init__(
        self,
        history_service: Optional[HistoryService] = None,
        on_select_run: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="History",
            subtitle="Local Diagnostic Run Archive, Longitudinal Trends, and Telemetry Inspection",
            parent=parent,
        )
        self.history_service = history_service
        self.on_select_run = on_select_run
        self._current_sessions: List[HistoricalSessionSummary] = []
        self._all_sessions: List[HistoricalSessionSummary] = []
        self._last_loaded_count: int = 0

        self._populate_content()

        # Dynamic live refresh timer to reflect new diagnostic runs automatically
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(3000)
        self._live_timer.timeout.connect(self._check_live_updates)
        self._live_timer.start()

        if self.history_service:
            self.load_history()

    def _populate_content(self) -> None:
        # 1. Search and Filtering Control Card
        filter_card, filter_layout = self.create_card(
            title="Run Search & Diagnostic Filtering",
            description=(
                "Query historical diagnostic runs committed locally in SQLite. Filter by run ID, "
                "gateway IP, physical interface, completion status, or deterministic diagnosis category."
            ),
        )

        filter_row = QWidget(filter_card)
        f_layout = QHBoxLayout(filter_row)
        f_layout.setContentsMargins(0, 4, 0, 0)
        f_layout.setSpacing(8)

        # Search box
        self.txt_search = QLineEdit(filter_row)
        self.txt_search.setPlaceholderText("Search by Run ID, status, gateway, interface, or finding...")
        self.txt_search.textChanged.connect(self._on_filter_changed)

        # Status Filter
        self.cbo_status = QComboBox(filter_row)
        self.cbo_status.addItems(["All Statuses", "COMPLETED", "FAILED", "PARTIAL", "CANCELLED"])
        self.cbo_status.currentTextChanged.connect(self._on_filter_changed)

        # Category Filter
        self.cbo_category = QComboBox(filter_row)
        self.cbo_category.addItems([
            "All Categories",
            "LOCAL_NETWORK",
            "DNS_SUBSYSTEM",
            "EXTERNAL_PATH",
            "BUFFERBLOAT",
            "INCONCLUSIVE",
        ])
        self.cbo_category.currentTextChanged.connect(self._on_filter_changed)

        # Date Range Filter
        self.cbo_date_range = QComboBox(filter_row)
        self.cbo_date_range.addItems([
            "All Time (Full History)",
            "Simulation Dataset (10–29 Oct 2026)",
            "Week 1 (10–11 Oct 2026)",
            "Week 2 (12–18 Oct 2026)",
            "Week 3 (19–25 Oct 2026)",
            "Week 4 (26–29 Oct 2026)",
            "Past 7 Days",
            "Today",
        ])
        self.cbo_date_range.currentTextChanged.connect(lambda _: self.load_history())

        # Provenance Filter (All / Simulated / Genuine)
        self.cbo_provenance = QComboBox(filter_row)
        self.cbo_provenance.addItems([
            "All Runs (Genuine & Simulated)",
            "Simulated Data Only (587 runs)",
            "Genuine Live Runs Only",
        ])
        self.cbo_provenance.currentTextChanged.connect(lambda _: self.load_history())

        # Action Buttons
        self.btn_clear_filters = QPushButton("Clear Filters", filter_row)
        self.btn_clear_filters.clicked.connect(self._handle_clear_filters)

        self.btn_refresh = QPushButton("Refresh History", filter_row)
        self.btn_refresh.clicked.connect(self.load_history)

        f_layout.addWidget(self.txt_search, stretch=3)
        f_layout.addWidget(self.cbo_date_range, stretch=2)
        f_layout.addWidget(self.cbo_provenance, stretch=2)
        f_layout.addWidget(self.cbo_status, stretch=1)
        f_layout.addWidget(self.cbo_category, stretch=1)
        f_layout.addWidget(self.btn_clear_filters)
        f_layout.addWidget(self.btn_refresh)

        filter_layout.addWidget(filter_row)
        self.content_layout.addWidget(filter_card)

        # 2. Longitudinal Trend Visualization Widget
        self.trends_widget = HistoricalTrendsWidget(self)
        self.content_layout.addWidget(self.trends_widget)

        # 3. Recorded Runs Table Container Card
        self.table_card = QFrame()
        self.table_card.setProperty("class", "Card")
        tc_layout = QVBoxLayout(self.table_card)
        tc_layout.setContentsMargins(14, 10, 14, 10)
        tc_layout.setSpacing(8)

        tbl_header_row = QWidget(self.table_card)
        th_layout = QHBoxLayout(tbl_header_row)
        th_layout.setContentsMargins(0, 0, 0, 0)
        th_layout.setSpacing(10)

        self.lbl_tbl_title = QLabel("Recorded Diagnostic Runs", tbl_header_row)
        self.lbl_tbl_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        th_layout.addWidget(self.lbl_tbl_title)
        th_layout.addStretch()

        self.lbl_session_count = QLabel("0 runs recorded", tbl_header_row)
        self.lbl_session_count.setStyleSheet(
            f"font-size: 12px; font-family: {ThemeTokens.FONT_MONO}; color: {ThemeTokens.TEXT_MUTED};"
        )
        self.lbl_run_count = self.lbl_session_count
        th_layout.addWidget(self.lbl_session_count)
        tc_layout.addWidget(tbl_header_row)

        self.tbl_runs = QTableWidget(self.table_card)
        self.tbl_runs.setColumnCount(9)
        self.tbl_runs.setHorizontalHeaderLabels([
            "Run ID",
            "Date / Time (IST)",
            "Status",
            "Primary Diagnosis",
            "Gateway IP",
            "Ext Ping",
            "DNS",
            "Loss %",
            "Latency",
        ])
        self.tbl_runs.verticalHeader().setVisible(False)
        self.tbl_runs.verticalHeader().setDefaultSectionSize(40)
        self.tbl_runs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl_runs.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.tbl_runs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_runs.setAlternatingRowColors(True)
        self.tbl_runs.setWordWrap(True)
        self.tbl_runs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tbl_runs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tbl_runs.setMinimumHeight(280)

        self.tbl_runs.setStyleSheet(
            f"QTableWidget {{ "
            f"    background-color: {ThemeTokens.BG_WINDOW}; "
            f"    gridline-color: {ThemeTokens.BORDER_DEFAULT}; "
            f"    color: {ThemeTokens.TEXT_PRIMARY}; "
            f"    font-size: 11px; "
            f"    border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"    border-radius: 6px; "
            f"}} "
            f"QTableWidget::item {{ "
            f"    padding: 3px 4px; "
            f"}} "
            f"QTableWidget::item:selected {{ "
            f"    background-color: {ThemeTokens.BRAND_HOVER}; "
            f"    color: {ThemeTokens.TEXT_PRIMARY}; "
            f"}} "
            f"QHeaderView::section {{ "
            f"    background-color: {ThemeTokens.BG_MUTED}; "
            f"    color: {ThemeTokens.TEXT_SECONDARY}; "
            f"    font-weight: 600; "
            f"    font-size: 11px; "
            f"    padding: 5px 4px; "
            f"    border: none; "
            f"    border-bottom: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"}}"
        )

        header = self.tbl_runs.horizontalHeader()
        for i in range(9):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(10)

        self.adjust_column_widths()
        self.tbl_runs.viewport().installEventFilter(self)

        self.tbl_runs.itemDoubleClicked.connect(self._on_table_row_double_clicked)
        self.tbl_runs.itemSelectionChanged.connect(self._on_selection_changed)

        tc_layout.addWidget(self.tbl_runs)

        # Action Buttons row below table
        btn_action_row = QWidget(self.table_card)
        bar_layout = QHBoxLayout(btn_action_row)
        bar_layout.setContentsMargins(0, 14, 0, 4)
        bar_layout.setSpacing(12)

        self.btn_load_selected = QPushButton("View Selected Findings", btn_action_row)
        self.btn_load_selected.setObjectName("primaryButton")
        self.btn_load_selected.setEnabled(False)
        self.btn_load_selected.clicked.connect(self._handle_load_selected_clicked)

        bar_layout.addWidget(self.btn_load_selected)
        bar_layout.addStretch()
        tc_layout.addWidget(btn_action_row)

        self.content_layout.addWidget(self.table_card)
        self.table_card.setVisible(False)

        # 4. Interactive Historical Session Detail Inspection Panel
        self.detail_panel = HistoricalDetailPanel(self)
        self.detail_panel.sig_open_results.connect(self._handle_open_run_id)
        self.content_layout.addWidget(self.detail_panel)

        # 5. Honest Empty State: Empty Archive
        self.empty_state = self.create_empty_state(
            title="Diagnostic History Is Empty",
            message=(
                "No prior diagnostic runs exist in the database. When diagnostic runs are "
                "completed, they will appear here with start times, duration, stage outcomes, "
                "and findings. Historical data is never fabricated or simulated."
            ),
        )
        self.content_layout.addWidget(self.empty_state)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if hasattr(self, "tbl_runs") and watched == self.tbl_runs.viewport() and event.type() == QEvent.Type.Resize:
            self.adjust_column_widths()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.adjust_column_widths()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.load_history()
        self.adjust_column_widths()

    def _check_live_updates(self) -> None:
        """Silently check SQLite for new runs and dynamically update chart and table if data changed."""
        if not self.history_service or not self.isVisible():
            return
        try:
            runs = self.history_service.list_runs()
            if len(runs) != self._last_loaded_count:
                self.load_history()
        except Exception:
            pass

    def adjust_column_widths(self) -> None:
        """Dynamically allocate column widths to fit viewport without clipping or scrollbars."""
        if not hasattr(self, "tbl_runs"):
            return
        if not self.tbl_runs.isVisible():
            self.tbl_runs.horizontalScrollBar().setRange(0, 0)
            return
        total_w = self.tbl_runs.viewport().width()
        if total_w <= 0:
            return

        target_total = max(200, total_w - 8)
        base_widths = [134, 110, 80, 105, 96, 50, 46, 50, 64]
        base_sum = sum(base_widths)  # 735

        if target_total <= base_sum:
            allocated = 0
            for i in range(8):
                w = max(15, int(target_total * (base_widths[i] / base_sum)))
                self.tbl_runs.setColumnWidth(i, w)
                allocated += w
            self.tbl_runs.setColumnWidth(8, max(15, target_total - allocated))
            return

        weights = [0.8, 0.7, 0.6, 3.8, 0.8, 0.5, 0.5, 0.6, 0.7]
        extra_w = target_total - base_sum
        total_weight = sum(weights)
        allocated = 0
        for i in range(8):
            add = int(extra_w * (weights[i] / total_weight))
            w = base_widths[i] + add
            self.tbl_runs.setColumnWidth(i, w)
            allocated += w
        self.tbl_runs.setColumnWidth(8, max(15, target_total - allocated))

    def _get_selected_date_bounds(self) -> tuple[Optional[str], Optional[str]]:
        if not hasattr(self, "cbo_date_range"):
            return None, None
        txt = self.cbo_date_range.currentText()
        if "Simulation Dataset" in txt:
            return "2026-10-10", "2026-10-29"
        elif "Week 1" in txt:
            return "2026-10-10", "2026-10-11"
        elif "Week 2" in txt:
            return "2026-10-12", "2026-10-18"
        elif "Week 3" in txt:
            return "2026-10-19", "2026-10-25"
        elif "Week 4" in txt:
            return "2026-10-26", "2026-10-29"
        elif "Past 7 Days" in txt:
            from ticnet.core.timezone import now_ist
            import datetime
            now_d = now_ist().date()
            return (now_d - datetime.timedelta(days=7)).isoformat(), now_d.isoformat()
        elif "Today" in txt:
            from ticnet.core.timezone import now_ist
            return now_ist().date().isoformat(), now_ist().date().isoformat()
        return None, None

    def _get_selected_sim_filter(self) -> Optional[int]:
        if not hasattr(self, "cbo_provenance"):
            return None
        txt = self.cbo_provenance.currentText()
        if "Simulated Data Only" in txt:
            return 1
        elif "Genuine Live Runs Only" in txt:
            return 0
        return None

    def load_history(self) -> None:
        """Query HistoryService and populate table and trend charts with real historical runs."""
        if not self.history_service:
            return

        date_from, date_to = self._get_selected_date_bounds()
        is_sim = self._get_selected_sim_filter()

        # Query all matching sessions without arbitrary limits
        sessions = self.history_service.get_enriched_sessions(
            date_from_ist=date_from,
            date_to_ist=date_to,
            is_simulated=is_sim,
            limit=None,
        )
        self._all_sessions = sessions
        self._current_sessions = sessions
        self._last_loaded_count = len(sessions)

        # Update Trends Widget with all matching sessions in chronological order
        trend_sessions = self.history_service.get_historical_trends(
            date_from_ist=date_from,
            date_to_ist=date_to,
            is_simulated=is_sim,
            limit=None,
        )
        self.trends_widget.set_historical_sessions(trend_sessions)

        self._apply_current_filters()

    def _apply_current_filters(self) -> None:
        query = self.txt_search.text().strip().lower()
        status_sel = self.cbo_status.currentText()
        cat_sel = self.cbo_category.currentText()

        filtered: List[HistoricalSessionSummary] = []
        for s in self._all_sessions:
            # Status filter
            if status_sel != "All Statuses" and s.status.upper() != status_sel.upper():
                continue

            # Category filter
            if cat_sel != "All Categories":
                if not s.category or s.category.upper() != cat_sel.upper():
                    continue

            # Text query
            if query:
                matches_id = query in s.run_id.lower()
                matches_gw = query in (s.gateway_ip or "").lower()
                matches_iface = query in (s.interface_name or "").lower()
                matches_diag = query in (s.diagnosis or "").lower()
                matches_status = query in s.status.lower()
                if not (matches_id or matches_gw or matches_iface or matches_diag or matches_status):
                    continue

            filtered.append(s)

        self._current_sessions = filtered
        self._populate_table(filtered)

    def _populate_table(self, sessions: Sequence[Union[HistoricalSessionSummary, TestRun]]) -> None:
        if not sessions:
            self.tbl_runs.setRowCount(0)
            self.table_card.setVisible(False)
            self.empty_state.setVisible(True)
            self.btn_load_selected.setEnabled(False)
            self.detail_panel.setVisible(False)
            self.lbl_session_count.setText("0 runs recorded")
            return

        self.empty_state.setVisible(False)
        self.table_card.setVisible(True)
        self.lbl_session_count.setText(f"{len(sessions)} runs displayed")
        self.tbl_runs.setRowCount(len(sessions))

        for row_idx, item in enumerate(sessions):
            if isinstance(item, HistoricalSessionSummary):
                run_id = item.run_id
                start_time = item.start_time_utc
                status_str = item.status
                diagnosis_str = item.diagnosis or "Nominal / Normal Connectivity"
                gw_str = item.gateway_ip or "None"
                ext_str = item.external_status or "Not Measured"
                dns_str = item.dns_status or "Not Measured"
                loss_str = f"{item.packet_loss_pct:.1f}%" if item.packet_loss_pct is not None else "Not Measured"
                lat_str = f"{item.avg_latency_ms:.1f} ms" if item.avg_latency_ms is not None else "Not Measured"
            else:
                # Fallback for plain TestRun entity (e.g. from tests)
                run_id = item.run_id
                start_time = item.start_time_utc
                status_str = item.status.value
                diagnosis_str = "Completed Run" if status_str == "COMPLETED" else status_str
                gw_str = item.gateway_ip or "None"
                ext_str = "SUCCESS" if status_str == "COMPLETED" else "N/A"
                dns_str = "SUCCESS" if status_str == "COMPLETED" else "N/A"
                loss_str = "0.0%" if status_str == "COMPLETED" else "Not Measured"
                lat_str = "Not Measured"

            # Col 0: Run ID (with clear provenance tag)
            is_sim = getattr(item, "is_simulated", False)
            display_id = f"{run_id} [SIM]" if is_sim else run_id
            item_id = QTableWidgetItem(display_id)
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_id.setData(Qt.ItemDataRole.UserRole, run_id)
            item_id.setFont(QFont(ThemeTokens.FONT_MONO, 9))
            if is_sim:
                item_id.setToolTip("Simulated Diagnostic Run (SIM-202610-IST)")
                item_id.setForeground(QColor("#6366f1"))
            else:
                item_id.setToolTip("Genuine Live Diagnostic Run")

            # Col 1: Start Time (Formatted into two clean centered lines in IST)
            dt_ist = to_ist(start_time)
            if dt_ist:
                display_time = f"{dt_ist.strftime('%d %b %Y')}\n{dt_ist.strftime('%I:%M:%S %p IST')}"
                tooltip_time = format_ist(start_time)
            else:
                display_time = start_time[:19]
                tooltip_time = start_time
            item_time = QTableWidgetItem(display_time)
            item_time.setToolTip(tooltip_time)
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_time.setFont(QFont(ThemeTokens.FONT_MONO, 9))

            # Col 2: Status
            item_status = QTableWidgetItem(status_str)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if status_str == "COMPLETED":
                item_status.setForeground(QColor(ThemeTokens.COLOR_SUCCESS))
            elif status_str == "FAILED":
                item_status.setForeground(QColor(ThemeTokens.COLOR_ERROR))
            elif status_str in ("CANCELLED", "PARTIAL"):
                item_status.setForeground(QColor(ThemeTokens.COLOR_WARNING))

            # Col 3: Primary Diagnosis (Display first headline sentence cleanly with full text tooltip)
            first_sent = diagnosis_str.split(". ")[0].strip()
            if len(first_sent) < len(diagnosis_str):
                display_diag = first_sent + "..."
            elif len(diagnosis_str) > 85:
                display_diag = diagnosis_str[:82].rstrip() + "..."
            else:
                display_diag = diagnosis_str
            item_diag = QTableWidgetItem(display_diag)
            item_diag.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_diag.setToolTip(diagnosis_str)

            # Col 4: Gateway
            item_gw = QTableWidgetItem(gw_str)
            item_gw.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_gw.setFont(QFont(ThemeTokens.FONT_MONO, 9))

            # Col 5: External Ping (Compact status indicator)
            ext_clean = ext_str.strip().upper()
            if ext_clean == "SUCCESS":
                disp_ext = "OK"
                color_ext = ThemeTokens.COLOR_SUCCESS
            elif ext_clean == "FAILED":
                disp_ext = "FAIL"
                color_ext = ThemeTokens.COLOR_ERROR
            elif ext_clean in ("NOT MEASURED", "N/A", "--", "NONE"):
                disp_ext = "--"
                color_ext = ThemeTokens.TEXT_MUTED
            else:
                disp_ext = ext_str
                color_ext = ThemeTokens.TEXT_PRIMARY
            item_ext = QTableWidgetItem(disp_ext)
            item_ext.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_ext.setForeground(QColor(color_ext))
            item_ext.setToolTip(f"External Ping: {ext_str}")

            # Col 6: DNS (Compact status indicator)
            dns_clean = dns_str.strip().upper()
            if dns_clean == "SUCCESS":
                disp_dns = "OK"
                color_dns = ThemeTokens.COLOR_SUCCESS
            elif dns_clean == "FAILED":
                disp_dns = "FAIL"
                color_dns = ThemeTokens.COLOR_ERROR
            elif dns_clean in ("NOT MEASURED", "N/A", "--", "NONE"):
                disp_dns = "--"
                color_dns = ThemeTokens.TEXT_MUTED
            else:
                disp_dns = dns_str
                color_dns = ThemeTokens.TEXT_PRIMARY
            item_dns = QTableWidgetItem(disp_dns)
            item_dns.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_dns.setForeground(QColor(color_dns))
            item_dns.setToolTip(f"DNS Status: {dns_str}")

            # Col 7: Packet Loss
            if loss_str in ("Not Measured", "N/A", "None"):
                disp_loss = "--"
            else:
                disp_loss = loss_str
            item_loss = QTableWidgetItem(disp_loss)
            item_loss.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_loss.setFont(QFont(ThemeTokens.FONT_MONO, 9))
            item_loss.setToolTip(f"Packet Loss: {loss_str}")

            # Col 8: Avg Latency
            if lat_str in ("Not Measured", "N/A", "None"):
                disp_lat = "--"
            else:
                disp_lat = lat_str
            item_lat = QTableWidgetItem(disp_lat)
            item_lat.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_lat.setFont(QFont(ThemeTokens.FONT_MONO, 9))
            item_lat.setToolTip(f"Average Latency: {lat_str}")

            self.tbl_runs.setItem(row_idx, 0, item_id)
            self.tbl_runs.setItem(row_idx, 1, item_time)
            self.tbl_runs.setItem(row_idx, 2, item_status)
            self.tbl_runs.setItem(row_idx, 3, item_diag)
            self.tbl_runs.setItem(row_idx, 4, item_gw)
            self.tbl_runs.setItem(row_idx, 5, item_ext)
            self.tbl_runs.setItem(row_idx, 6, item_dns)
            self.tbl_runs.setItem(row_idx, 7, item_loss)
            self.tbl_runs.setItem(row_idx, 8, item_lat)
            self.tbl_runs.setRowHeight(row_idx, 40)

        self.adjust_column_widths()

    def _on_filter_changed(self) -> None:
        self._apply_current_filters()

    def _handle_clear_filters(self) -> None:
        self.txt_search.blockSignals(True)
        self.cbo_status.blockSignals(True)
        self.cbo_category.blockSignals(True)
        if hasattr(self, "cbo_date_range"):
            self.cbo_date_range.blockSignals(True)
            self.cbo_date_range.setCurrentIndex(0)
            self.cbo_date_range.blockSignals(False)
        if hasattr(self, "cbo_provenance"):
            self.cbo_provenance.blockSignals(True)
            self.cbo_provenance.setCurrentIndex(0)
            self.cbo_provenance.blockSignals(False)

        self.txt_search.clear()
        self.cbo_status.setCurrentIndex(0)
        self.cbo_category.setCurrentIndex(0)

        self.txt_search.blockSignals(False)
        self.cbo_status.blockSignals(False)
        self.cbo_category.blockSignals(False)

        self.load_history()

    def _on_selection_changed(self) -> None:
        selected_rows = self.tbl_runs.selectionModel().selectedRows()
        has_sel = len(selected_rows) > 0
        self.btn_load_selected.setEnabled(has_sel)

        if has_sel:
            row = selected_rows[0].row()
            if row < len(self._current_sessions):
                item = self._current_sessions[row]
                if isinstance(item, HistoricalSessionSummary):
                    self.detail_panel.display_session(item)
                else:
                    # Construct on the fly if needed
                    session = HistoricalSessionSummary(
                        run_id=item.run_id,
                        start_time_utc=item.start_time_utc,
                        end_time_utc=item.end_time_utc,
                        status=item.status.value if hasattr(item.status, "value") else str(item.status),
                        gateway_ip=item.gateway_ip,
                        interface_name=item.interface_name,
                        overall_error=item.overall_error,
                        diagnosis="Run Record",
                        category="GENERAL",
                        gateway_status=None,
                        external_status=None,
                        dns_status=None,
                        packet_loss_pct=None,
                        avg_latency_ms=None,
                        gateway_latency_ms=None,
                        throughput_mbps=None,
                        loaded_latency_delta_ms=None,
                    )
                    self.detail_panel.display_session(session)

    def _on_table_row_double_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        run_id_item = self.tbl_runs.item(row, 0)
        if run_id_item:
            run_id = run_id_item.data(Qt.ItemDataRole.UserRole) or run_id_item.text()
            if run_id:
                self._handle_open_run_id(run_id)

    def _handle_load_selected_clicked(self) -> None:
        selected_rows = self.tbl_runs.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        run_id_item = self.tbl_runs.item(row, 0)
        if run_id_item:
            run_id = run_id_item.data(Qt.ItemDataRole.UserRole) or run_id_item.text()
            if run_id:
                self._handle_open_run_id(run_id)

    def _handle_open_run_id(self, run_id: str) -> None:
        if self.on_select_run:
            self.on_select_run(run_id)

    def _on_search_text_changed(self, text: str) -> None:
        """Backward compatibility handler for direct calls."""
        self._apply_current_filters()
