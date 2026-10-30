"""Interactive historical session detail inspection panel.

Adheres strictly to Stage 5 requirements:
- Displays complete session summary, diagnostic results, and stage outcomes.
- Presents supporting empirical measurements with honest missingness formatting.
- Provides dual-action navigation to open the full Results & Charts view.
- Strictly guarantees measurements belong exclusively to the selected session.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.services.history_service import HistoricalSessionSummary
from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.run_id_badge import RunIdBadge


class HistoricalDetailPanel(QFrame):
    """Detailed drill-down card inspecting an individual historical diagnostic session."""

    sig_open_results = Signal(str)  # Emits run_id to switch to full Results page

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("historicalDetailPanel")
        self.setStyleSheet(
            f"#historicalDetailPanel {{ "
            f"  background-color: {ThemeTokens.BG_CARD}; "
            f"  border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"  border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
            f"  padding: 16px; "
            f"}}"
        )
        self._current_run_id: Optional[str] = None
        self._setup_ui()
        self.setVisible(False)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header: Run ID, Status, and Action Button
        header_row = QWidget(self)
        h_layout = QHBoxLayout(header_row)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(10)

        title_col = QWidget(header_row)
        tc_layout = QVBoxLayout(title_col)
        tc_layout.setContentsMargins(0, 0, 0, 0)
        tc_layout.setSpacing(2)

        self.lbl_run_title = QLabel("Run Inspection", title_col)
        self.lbl_run_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY};")

        self.lbl_meta = QLabel("Timestamp: -- | Interface: -- | Gateway: --", title_col)
        self.lbl_meta.setStyleSheet(
            f"font-size: 12px; font-family: {ThemeTokens.FONT_MONO}; color: {ThemeTokens.TEXT_MUTED};"
        )
        self.lbl_meta.setWordWrap(True)

        tc_layout.addWidget(self.lbl_run_title)
        tc_layout.addWidget(self.lbl_meta)
        h_layout.addWidget(title_col)
        h_layout.addStretch()

        self.run_id_badge = RunIdBadge(parent=header_row, compact=True)
        h_layout.addWidget(self.run_id_badge)

        self.btn_open_full = QPushButton("Open Full Results & Charts ➔", header_row)
        self.btn_open_full.setObjectName("primaryButton")
        self.btn_open_full.setStyleSheet(
            f"background-color: {ThemeTokens.BRAND_PRIMARY}; "
            f"color: white; font-weight: 600; padding: 5px 12px; border-radius: 4px;"
        )
        self.btn_open_full.clicked.connect(self._handle_open_full_clicked)
        h_layout.addWidget(self.btn_open_full)

        layout.addWidget(header_row)

        # Diagnosis Verdict Box
        self.verdict_box = QFrame(self)
        self.verdict_box.setStyleSheet(
            f"background-color: {ThemeTokens.BG_WINDOW}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
            f"padding: 8px 12px;"
        )
        vb_layout = QVBoxLayout(self.verdict_box)
        vb_layout.setContentsMargins(6, 6, 6, 6)
        vb_layout.setSpacing(3)

        self.lbl_verdict_title = QLabel("Primary Diagnostic Result", self.verdict_box)
        self.lbl_verdict_title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ThemeTokens.BRAND_PRIMARY};"
        )
        self.lbl_verdict_desc = QLabel("Loading diagnosis...", self.verdict_box)
        self.lbl_verdict_desc.setStyleSheet(f"font-size: 12px; color: {ThemeTokens.TEXT_SECONDARY};")
        self.lbl_verdict_desc.setWordWrap(True)

        vb_layout.addWidget(self.lbl_verdict_title)
        vb_layout.addWidget(self.lbl_verdict_desc)
        layout.addWidget(self.verdict_box)

        # Empirical Measurements Grid
        lbl_grid_heading = QLabel("Historical Run Telemetry (Empirical Measurements)", self)
        lbl_grid_heading.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ThemeTokens.TEXT_MUTED}; text-transform: uppercase;"
        )
        layout.addWidget(lbl_grid_heading)

        grid = QGridLayout()
        grid.setSpacing(6)

        self.card_gw = self._create_metric_chip("Gateway RTT", "--")
        self.card_ext = self._create_metric_chip("External RTT", "--")
        self.card_loss = self._create_metric_chip("Probe Loss", "--")
        self.card_dns = self._create_metric_chip("DNS Query Time", "--")
        self.card_speed = self._create_metric_chip("Download Speed", "--")
        self.card_load = self._create_metric_chip("Loaded Latency Delta", "--")

        grid.addWidget(self.card_gw, 0, 0)
        grid.addWidget(self.card_ext, 0, 1)
        grid.addWidget(self.card_loss, 0, 2)
        grid.addWidget(self.card_dns, 1, 0)
        grid.addWidget(self.card_speed, 1, 1)
        grid.addWidget(self.card_load, 1, 2)

        layout.addLayout(grid)

    def _create_metric_chip(self, label: str, val: str) -> QFrame:
        chip = QFrame(self)
        chip.setStyleSheet(
            f"background-color: {ThemeTokens.BG_WINDOW}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
            f"padding: 5px 8px;"
        )
        c_layout = QVBoxLayout(chip)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(2)

        lbl = QLabel(label, chip)
        lbl.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {ThemeTokens.TEXT_MUTED};")
        lbl.setWordWrap(True)
        v = QLabel(val, chip)
        v.setStyleSheet(
            f"font-size: 14px; font-weight: 700; font-family: {ThemeTokens.FONT_MONO}; color: {ThemeTokens.TEXT_PRIMARY};"
        )

        c_layout.addWidget(lbl)
        c_layout.addWidget(v)
        chip.val_label = v  # type: ignore[attr-defined]
        return chip

    def display_session(self, session: HistoricalSessionSummary) -> None:
        """Populate the inspection view with data from a specific historical run."""
        self._current_run_id = session.run_id
        self.setVisible(True)

        self.run_id_badge.set_run_id(session.run_id)
        self.lbl_run_title.setText(f"Run Inspection: {session.run_id}")
        self.lbl_meta.setText(
            f"Time: {session.start_time_utc} | Status: {session.status} | "
            f"Gateway: {session.gateway_ip or 'None'} | Interface: {session.interface_name or 'N/A'}"
        )

        # Verdict
        if session.diagnosis:
            self.lbl_verdict_title.setText(f"Diagnostic Finding ({session.category or 'General'})")
            self.lbl_verdict_desc.setText(session.diagnosis)
        elif session.status == "COMPLETED":
            self.lbl_verdict_title.setText("Diagnostic Finding: Normal Connectivity")
            self.lbl_verdict_desc.setText("All sequential checkpoints passed within nominal latency bounds.")
        elif session.overall_error:
            self.lbl_verdict_title.setText(f"Execution Error ({session.status})")
            self.lbl_verdict_desc.setText(session.overall_error)
        else:
            self.lbl_verdict_title.setText(f"Run Status: {session.status}")
            self.lbl_verdict_desc.setText("No specific diagnostic finding was committed for this run.")

        # Metric Chips
        gw_str = f"{session.gateway_latency_ms:.1f} ms" if session.gateway_latency_ms is not None else "Not Measured"
        ext_str = f"{session.avg_latency_ms:.1f} ms" if session.avg_latency_ms is not None else "Not Measured"
        loss_str = f"{session.packet_loss_pct:.1f} %" if session.packet_loss_pct is not None else "Not Measured"
        dns_str = f"{session.dns_status or 'Not Measured'}"
        speed_str = f"{session.throughput_mbps:.1f} Mbps" if session.throughput_mbps is not None else "Not Measured"
        load_str = f"+{session.loaded_latency_delta_ms:.1f} ms" if session.loaded_latency_delta_ms is not None else "Not Measured"

        self.card_gw.val_label.setText(gw_str)  # type: ignore[attr-defined]
        self.card_ext.val_label.setText(ext_str)  # type: ignore[attr-defined]
        self.card_loss.val_label.setText(loss_str)  # type: ignore[attr-defined]
        self.card_dns.val_label.setText(dns_str)  # type: ignore[attr-defined]
        self.card_speed.val_label.setText(speed_str)  # type: ignore[attr-defined]
        self.card_load.val_label.setText(load_str)  # type: ignore[attr-defined]

    def _handle_open_full_clicked(self) -> None:
        if self._current_run_id:
            self.sig_open_results.emit(self._current_run_id)
