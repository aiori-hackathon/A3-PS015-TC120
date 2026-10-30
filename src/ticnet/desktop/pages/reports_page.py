"""Reports page for TicNet desktop presentation layer.

Invokes Phase 9 ReportGenerator to export serialized JSON, CSV, and Markdown
artifacts with native file dialogs, privacy masking, and atomic rollback protections.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QByteArray, QThreadPool, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_SAFETY_SVGS = {
    "shield": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'/>
        <path d='M9 12l2 2 4-4'/>
    </svg>""",
    "file_code": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/>
        <polyline points='14 2 14 8 20 8'/>
        <line x1='8' y1='13' x2='16' y2='13'/>
        <line x1='8' y1='17' x2='12' y2='17'/>
    </svg>""",
    "check_circle": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='9'/>
        <polyline points='9 12 11.5 14.5 15.5 9.5'/>
    </svg>""",
    "database": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <ellipse cx='12' cy='5' rx='9' ry='3'/>
        <path d='M21 12c0 1.66-4 3-9 3s-9-1.34-9-3'/>
        <path d='M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5'/>
    </svg>""",
}


def _render_safety_icon(name: str, color: str = "#2563eb", size: int = 16) -> QPixmap:
    """Render crisp SVG vector icon into QPixmap avoiding platform font quirks."""
    svg_template = _SAFETY_SVGS.get(name, "")
    svg_data = svg_template.replace("COLOR", color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_data))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap

from ticnet.core.timezone import format_ist
from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.services.export_worker import ExportWorker
from ticnet.desktop.services.history_service import HistoryService
from ticnet.desktop.services.reporting_service import ReportingService
from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.run_id_badge import RunIdBadge
from ticnet.models.entities import ReportExport, TestRun
from ticnet.models.enums import ExportFormat, ExportStatus
from ticnet.reporting.weekly_report_generator import WeeklyReportGenerator, WEEKLY_PERIODS


class ReportsPage(BasePage):
    """Reports page presenting export format controls, privacy options, and preview."""

    def __init__(
        self,
        reporting_service: Optional[ReportingService] = None,
        history_service: Optional[HistoryService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="Reports & Export",
            subtitle="Multi-Format Diagnostic Artifact Serialization and Privacy Controls",
            parent=parent,
        )
        self.reporting_service = reporting_service
        self.history_service = history_service
        self.current_run_id: Optional[str] = None
        self._is_exporting: bool = False
        self._is_shutting_down: bool = False
        self._active_export_worker: Optional[ExportWorker] = None

        self._populate_content()

    @property
    def is_exporting(self) -> bool:
        """Return True if a background export operation is currently in flight."""
        return self._is_exporting

    def _populate_content(self) -> None:
        # Export Capabilities Card
        export_card, export_layout = self.create_card(
            title="Serialized Report Formats",
            description=(
                "TicNet provides three portable export formats adhering to strict data integrity standards: "
                "JSON for programmatic data analysis, CSV for spreadsheet import (with formula-injection defenses), "
                "and Markdown for technical ISP escalation tickets."
            ),
        )

        # Run Selection & Run Row
        sel_row = QWidget(export_card)
        sel_layout = QHBoxLayout(sel_row)
        sel_layout.setContentsMargins(0, 4, 0, 8)
        sel_layout.setSpacing(10)

        lbl_sel = QLabel("Diagnostic Run:", sel_row)
        lbl_sel.setStyleSheet("font-weight: 600;")
        self.combo_runs = QComboBox(sel_row)
        self.combo_runs.addItem("— Select a Diagnostic Run —", None)
        self.combo_runs.currentIndexChanged.connect(self._on_combo_run_selected)

        self.run_id_badge = RunIdBadge(parent=sel_row, compact=True)
        self.run_id_badge.setVisible(False)

        self.btn_refresh_runs = QPushButton("Refresh Runs", sel_row)
        self.btn_refresh_runs.clicked.connect(self.refresh_runs_list)

        sel_layout.addWidget(lbl_sel)
        sel_layout.addWidget(self.combo_runs, 1)
        sel_layout.addWidget(self.run_id_badge)
        sel_layout.addWidget(self.btn_refresh_runs)
        export_layout.addWidget(sel_row)

        # Export Controls Row
        ctrl_widget = QWidget(export_card)
        ctrl_layout = QHBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(0, 6, 0, 0)
        ctrl_layout.setSpacing(12)

        lbl_format = QLabel("Export Format:", ctrl_widget)
        self.combo_format = QComboBox(ctrl_widget)
        self.combo_format.addItems([
            "Markdown Summary (.md)",
            "Structured Diagnostic Data (.json)",
            "Comprehensive Diagnostic Report (.csv)",
        ])
        self.combo_format.currentIndexChanged.connect(self._update_preview)

        self.chk_privacy = QCheckBox("Enable Privacy Redaction (Mask Public IPs & MACs)", ctrl_widget)
        self.chk_privacy.setChecked(True)
        self.chk_privacy.stateChanged.connect(self._update_preview)

        self.btn_export = QPushButton("Export Report...", ctrl_widget)
        self.btn_export.setObjectName("primaryButton")
        self.btn_export.setEnabled(False)  # Disabled until a run is selected
        self.btn_export.clicked.connect(self._handle_export_clicked)

        ctrl_layout.addWidget(lbl_format)
        ctrl_layout.addWidget(self.combo_format)
        ctrl_layout.addWidget(self.chk_privacy)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_export)

        export_layout.addWidget(ctrl_widget)
        self.content_layout.addWidget(export_card)

        # Preview Container Card
        self.preview_card = QFrame()
        self.preview_card.setProperty("class", "Card")
        prev_layout = QVBoxLayout(self.preview_card)
        prev_layout.setContentsMargins(16, 14, 16, 14)
        prev_layout.setSpacing(8)

        lbl_prev_title = QLabel("Report Preview (Pre-Export Validation)", self.preview_card)
        lbl_prev_title.setStyleSheet("font-size: 13px; font-weight: 700;")
        prev_layout.addWidget(lbl_prev_title)

        self.txt_preview = QTextEdit(self.preview_card)
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.txt_preview.setMinimumHeight(180)
        self.txt_preview.setStyleSheet(
            f"font-family: {ThemeTokens.FONT_MONO}; font-size: 12px; background-color: {ThemeTokens.BG_MUTED};"
        )
        prev_layout.addWidget(self.txt_preview)

        self.content_layout.addWidget(self.preview_card)
        self.preview_card.setVisible(False)

        # Weekly Historical Reports Card
        weekly_card, weekly_layout = self.create_card(
            title="Weekly Historical Performance Reports (PDF & CSV)",
            description=(
                "Generate executive SLA summaries, latency percentiles, and incident digests for the "
                "10–29 October 2026 IST historical dataset in high-resolution PDF or sanitized CSV format."
            ),
        )

        w_ctrl = QWidget(weekly_card)
        w_layout = QHBoxLayout(w_ctrl)
        w_layout.setContentsMargins(0, 6, 0, 0)
        w_layout.setSpacing(12)

        lbl_period = QLabel("Reporting Period:", w_ctrl)
        lbl_period.setStyleSheet("font-weight: 600;")
        self.combo_weekly_period = QComboBox(w_ctrl)
        self.combo_weekly_period.addItem("Full Simulation Period (10–29 Oct 2026) — 587 runs", "full_sim")
        self.combo_weekly_period.addItem("Week 1 (10–11 Oct 2026) — 57 runs (Weekend Baseline)", "week_1")
        self.combo_weekly_period.addItem("Week 2 (12–18 Oct 2026) — 207 runs (Faults A & B)", "week_2")
        self.combo_weekly_period.addItem("Week 3 (19–25 Oct 2026) — 205 runs (Faults C & D)", "week_3")
        self.combo_weekly_period.addItem("Week 4 (26–29 Oct 2026) — 118 runs (Fault E & Recovery)", "week_4")

        lbl_wformat = QLabel("Format:", w_ctrl)
        lbl_wformat.setStyleSheet("font-weight: 600;")
        self.combo_weekly_format = QComboBox(w_ctrl)
        self.combo_weekly_format.addItem("Executive Document (.pdf)", "pdf")
        self.combo_weekly_format.addItem("Sanitized Dataset (.csv)", "csv")

        self.chk_weekly_sim_only = QCheckBox("Simulated Data Only (SIM-202610-IST)", w_ctrl)
        self.chk_weekly_sim_only.setChecked(True)

        self.btn_export_weekly = QPushButton("Export Weekly Report...", w_ctrl)
        self.btn_export_weekly.setObjectName("primaryButton")
        self.btn_export_weekly.clicked.connect(self._handle_weekly_export_clicked)

        w_layout.addWidget(lbl_period)
        w_layout.addWidget(self.combo_weekly_period)
        w_layout.addWidget(lbl_wformat)
        w_layout.addWidget(self.combo_weekly_format)
        w_layout.addWidget(self.chk_weekly_sim_only)
        w_layout.addStretch()
        w_layout.addWidget(self.btn_export_weekly)

        weekly_layout.addWidget(w_ctrl)
        self.content_layout.addWidget(weekly_card)

        # Data Safety & Privacy Guarantees Card
        safe_card, safe_layout = self.create_card(
            title="Data Safety & Privacy Guarantees",
            description="Enterprise-grade redaction, formula injection defenses, and atomic filesystem protections applied to every generated report.",
        )

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setContentsMargins(0, 6, 0, 4)

        items = [
            (
                "shield",
                "#059669",
                "#ecfdf5",
                "Automatic Privacy Redaction",
                "ACTIVE BY DEFAULT",
                "#047857",
                "#d1fae5",
                "Public WAN IP addresses (masked to X.Y.Z.***) and physical hardware MAC addresses are automatically sanitized before export to safeguard your network identity.",
                0, 0,
            ),
            (
                "file_code",
                "#2563eb",
                "#eff6ff",
                "Spreadsheet Formula Sanitization",
                "CWE-1236 COMPLIANT",
                "#1d4ed8",
                "#dbeafe",
                "Tabular CSV cells beginning with '=', '+', '-', or '@' are safely escaped to neutralize formula injection vectors and malicious spreadsheet macro execution.",
                0, 1,
            ),
            (
                "check_circle",
                "#0284c7",
                "#f0f9ff",
                "Atomic Filesystem Pipeline",
                "FSYNC VERIFIED",
                "#0369a1",
                "#e0f2fe",
                "Reports are written to isolated sibling buffers, verified via filesystem sync (fsync), and atomically committed to prevent incomplete or corrupt artifacts.",
                1, 0,
            ),
            (
                "database",
                "#4f46e5",
                "#eef2ff",
                "Local Audit Trail & Governance",
                "100% LOCAL",
                "#4338ca",
                "#e0e7ff",
                "Export transactions and metadata hashes are recorded directly in your local SQLite store. No diagnostic data, telemetry, or report content ever leaves this machine.",
                1, 1,
            ),
        ]

        for icon_name, icon_col, icon_bg, title, badge_text, badge_col, badge_bg, desc, row, col in items:
            feature_card = QFrame(safe_card)
            feature_card.setStyleSheet(f"""
                QFrame {{
                    background-color: {ThemeTokens.BG_MUTED};
                    border: 1px solid {ThemeTokens.BORDER_DEFAULT};
                    border-radius: 8px;
                }}
            """)
            fc_layout = QVBoxLayout(feature_card)
            fc_layout.setContentsMargins(14, 12, 14, 12)
            fc_layout.setSpacing(8)

            top_row = QHBoxLayout()
            top_row.setSpacing(10)

            icon_box = QLabel(feature_card)
            icon_box.setFixedSize(28, 28)
            icon_box.setStyleSheet(f"""
                background-color: {icon_bg};
                border-radius: 6px;
                border: 1px solid {icon_col}22;
            """)
            icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_box.setPixmap(_render_safety_icon(icon_name, icon_col, 16))
            top_row.addWidget(icon_box)

            t_lbl = QLabel(title, feature_card)
            t_lbl.setStyleSheet(f"font-size: {ThemeTokens.FONT_SIZE_BODY}; font-weight: 600; color: {ThemeTokens.TEXT_PRIMARY};")
            top_row.addWidget(t_lbl, 1)

            b_lbl = QLabel(badge_text, feature_card)
            b_lbl.setStyleSheet(f"""
                background-color: {badge_bg};
                color: {badge_col};
                font-size: 10px;
                font-weight: 700;
                padding: 3px 7px;
                border-radius: 4px;
            """)
            top_row.addWidget(b_lbl)

            fc_layout.addLayout(top_row)

            d_lbl = QLabel(desc, feature_card)
            d_lbl.setStyleSheet(f"font-size: {ThemeTokens.FONT_SIZE_SECONDARY}; line-height: 1.4; color: {ThemeTokens.TEXT_SECONDARY};")
            d_lbl.setWordWrap(True)
            fc_layout.addWidget(d_lbl)

            grid.addWidget(feature_card, row, col)

        safe_layout.addLayout(grid)
        self.content_layout.addWidget(safe_card)

    def refresh_runs_list(self) -> None:
        """Populate the runs dropdown from HistoryService."""
        if not self.history_service:
            return

        runs = self.history_service.list_runs(limit=1000)
        self.combo_runs.blockSignals(True)
        self.combo_runs.clear()
        self.combo_runs.addItem("— Select a Diagnostic Run —", None)

        for r in runs:
            ist_str = format_ist(r.start_time_utc, "%d %b %Y, %I:%M %p IST") if r.start_time_utc else "Unknown Time"
            label = f"{r.run_id} ({r.status.value}) — {ist_str}"
            self.combo_runs.addItem(label, r.run_id)

        self.combo_runs.blockSignals(False)

    def _handle_weekly_export_clicked(self) -> None:
        """Handle weekly historical report export in PDF or CSV."""
        period_key = self.combo_weekly_period.currentData()
        fmt = self.combo_weekly_format.currentData()
        sim_only = self.chk_weekly_sim_only.isChecked()

        ext = ".pdf" if fmt == "pdf" else ".csv"
        default_filename = f"TicNet_Historical_Report_{period_key}{ext}"
        filter_str = "PDF Document (*.pdf)" if fmt == "pdf" else "CSV Spreadsheet (*.csv)"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Weekly Historical Report",
            default_filename,
            filter_str,
        )
        if not file_path:
            return

        try:
            # Resolve database manager
            db_manager = None
            if self.reporting_service and hasattr(self.reporting_service, "db"):
                db_manager = self.reporting_service.db
            elif self.history_service and hasattr(self.history_service, "db"):
                db_manager = self.history_service.db
            else:
                from ticnet.desktop.paths import get_default_database_path
                from ticnet.persistence.database import DatabaseManager
                db_manager = DatabaseManager(get_default_database_path())

            gen = WeeklyReportGenerator(db_manager)
            out_p = Path(file_path)
            if fmt == "pdf":
                gen.generate_pdf(period_key, out_p, simulated_only=sim_only)
            else:
                gen.generate_csv(period_key, out_p, simulated_only=sim_only)

            QMessageBox.information(
                self,
                "Report Export Succeeded",
                f"Weekly report for '{WEEKLY_PERIODS[period_key].name}' generated successfully!\n\n"
                f"Format: {fmt.upper()}\n"
                f"Path: {file_path}",
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Weekly Report Export Failed",
                f"Failed to generate weekly historical report:\n{exc}",
            )

    def set_selected_run(self, run_id: str) -> None:
        """Programmatically select a run and populate report preview.

        Args:
            run_id: Unique test run identifier.
        """
        self.refresh_runs_list()
        self.current_run_id = run_id

        # Match in combo box
        for idx in range(self.combo_runs.count()):
            if self.combo_runs.itemData(idx) == run_id:
                self.combo_runs.setCurrentIndex(idx)
                break

        self.run_id_badge.set_run_id(run_id)
        self.run_id_badge.setVisible(True)
        self.btn_export.setEnabled(True)
        self.preview_card.setVisible(True)
        self._update_preview()

    def _on_combo_run_selected(self, index: int) -> None:
        run_id = self.combo_runs.itemData(index)
        if run_id:
            self.current_run_id = run_id
            self.run_id_badge.set_run_id(run_id)
            self.run_id_badge.setVisible(True)
            self.btn_export.setEnabled(True)
            self.preview_card.setVisible(True)
            self._update_preview()
        else:
            self.current_run_id = None
            self.run_id_badge.set_run_id(None)
            self.run_id_badge.setVisible(False)
            self.btn_export.setEnabled(False)
            self.preview_card.setVisible(False)

    def _get_current_export_format(self) -> ExportFormat:
        text = self.combo_format.currentText()
        if ".json" in text:
            return ExportFormat.JSON
        elif ".csv" in text:
            return ExportFormat.CSV
        return ExportFormat.MARKDOWN

    def _update_preview(self) -> None:
        if not self.current_run_id or not self.reporting_service:
            return

        fmt = self._get_current_export_format()
        privacy = self.chk_privacy.isChecked()

        try:
            preview_text = self.reporting_service.get_export_preview(
                run_id=self.current_run_id,
                export_format=fmt,
                enable_privacy=privacy,
            )
            self.txt_preview.setPlainText(preview_text)
        except Exception as exc:
            self.txt_preview.setPlainText(f"Failed to generate preview: {exc}")

    def _handle_export_clicked(self) -> None:
        if not self.current_run_id or not self.reporting_service:
            return

        if self._is_exporting:
            return  # Prevent concurrent duplicate export attempts

        fmt = self._get_current_export_format()
        privacy = self.chk_privacy.isChecked()
        ext = self.reporting_service.get_extension_for_format(fmt)

        filter_map = {
            ExportFormat.MARKDOWN: "Markdown Report (*.md)",
            ExportFormat.JSON: "Structured Diagnostic JSON (*.json)",
            ExportFormat.CSV: "Comprehensive Diagnostic Report (*.csv)",
        }
        filter_str = filter_map.get(fmt, "All Files (*.*)")

        default_filename = self.reporting_service.get_recommended_filename(self.current_run_id, fmt)

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Diagnostic Report",
            default_filename,
            filter_str,
        )

        if not file_path:
            return  # User canceled file dialog

        # Visual indicator of background export
        self._is_exporting = True
        self.btn_export.setEnabled(False)
        self.btn_export.setText("Exporting Report (Background Task)...")

        # Create and dispatch background QRunnable export worker
        worker = ExportWorker(
            reporting_service=self.reporting_service,
            run_id=self.current_run_id,
            export_format=fmt,
            output_path=file_path,
            enable_privacy=privacy,
            overwrite=True,
        )
        self._active_export_worker = worker
        worker.signals.sig_completed.connect(self._on_export_completed)
        worker.signals.sig_failed.connect(self._on_export_failed)

        QThreadPool.globalInstance().start(worker)

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Safely detach and await active export worker before widget destruction."""
        self._is_shutting_down = True
        if self._is_exporting and self._active_export_worker:
            try:
                self._active_export_worker.signals.sig_completed.disconnect(self._on_export_completed)
            except (RuntimeError, TypeError):
                pass
            try:
                self._active_export_worker.signals.sig_failed.disconnect(self._on_export_failed)
            except (RuntimeError, TypeError):
                pass
            return QThreadPool.globalInstance().waitForDone(timeout_ms)
        return True

    def _on_export_completed(self, record: ReportExport) -> None:
        """Handle background report export completion on Qt main thread."""
        if self._is_shutting_down:
            return

        self._is_exporting = False
        self._active_export_worker = None
        try:
            self.btn_export.setEnabled(True)
            self.btn_export.setText("Export Report to File")
        except RuntimeError:
            return

        if record.export_status == ExportStatus.SUCCESS:
            QMessageBox.information(
                self,
                "Export Succeeded",
                f"Diagnostic report exported successfully!\n\n"
                f"Format: {record.export_format.value}\n"
                f"Size: {record.file_size_bytes or 0} bytes\n"
                f"Audit ID: {record.export_id}",
            )
        else:
            QMessageBox.warning(
                self,
                "Export Incomplete",
                f"Report export recorded as {record.export_status.value}:\n{record.error_message}",
            )

    def _on_export_failed(self, run_id: str, error_message: str) -> None:
        """Handle background report export failure on Qt main thread."""
        if self._is_shutting_down:
            return

        self._is_exporting = False
        self._active_export_worker = None
        try:
            self.btn_export.setEnabled(True)
            self.btn_export.setText("Export Report to File")
        except RuntimeError:
            return

        QMessageBox.critical(
            self,
            "Export Failed",
            f"Failed to export diagnostic report for run {run_id[:8]}:\n\n{error_message}\n\n"
            f"Destination was protected by Phase 9 atomic write rollback.",
        )

