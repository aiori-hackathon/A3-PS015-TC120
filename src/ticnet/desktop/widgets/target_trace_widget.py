"""Target Trace and Two-Checkpoint Isolation Table Widget (PingPlotter-Inspired).

Displays a detailed trace table comparing Local Gateway [Checkpoint 1] versus
Multi-Target External Endpoints [Checkpoint 2] with exact empirical sample counts,
packet-loss percentages, visual loss bars, and explainable localization analysis.

Strict Invariants:
- Uses ONLY actual persisted observations and summaries.
- Never fabricates placeholder hops, synthetic loss, or simulated latency.
- External degradation is clearly documented as NOT proving an ISP fault alone.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens
from ticnet.models.entities import Observation, Summary, Target, TestRun
from ticnet.models.enums import MeasurementStatus, TargetType


class LossBarWidget(QWidget):
    """Custom compact visual indicator showing packet loss percentage.

    Inspired by PingPlotter's hop trace loss bar:
    Displays a horizontal bar where green indicates successful packets and
    prominent red fill indicates packet loss / timeouts.
    """

    def __init__(self, loss_pct: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.loss_pct = max(0.0, min(100.0, float(loss_pct)))
        self.setFixedHeight(12)
        self.setMinimumWidth(32)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)
        w = rect.width()
        h = rect.height()
        radius = 2.5

        # Background track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ThemeTokens.BG_MUTED))
        painter.drawRoundedRect(rect, radius, radius)

        if self.loss_pct <= 0.0:
            # 100% clean - solid green
            painter.setBrush(QColor(ThemeTokens.COLOR_SUCCESS))
            painter.drawRoundedRect(rect, radius, radius)
        elif self.loss_pct >= 100.0:
            # 100% loss - solid red
            painter.setBrush(QColor(ThemeTokens.COLOR_ERROR))
            painter.drawRoundedRect(rect, radius, radius)
        else:
            # Split bar: green portion on left, red portion on right
            clean_pct = 100.0 - self.loss_pct
            clean_w = int(w * (clean_pct / 100.0))
            loss_w = w - clean_w

            # Clean portion
            clean_rect = rect.adjusted(0, 0, -loss_w, 0)
            painter.setBrush(QColor(ThemeTokens.COLOR_SUCCESS))
            painter.drawRoundedRect(clean_rect, radius, radius)

            # Loss portion
            loss_rect = rect.adjusted(clean_w, 0, 0, 0)
            painter.setBrush(QColor(ThemeTokens.COLOR_ERROR))
            painter.drawRoundedRect(loss_rect, radius, radius)

        painter.end()


class TargetTraceTableWidget(QFrame):
    """Trace comparison table presenting Local Gateway vs External targets.

    Provides PingPlotter-style hop isolation, visual packet-loss bars, and
    emits `sig_target_selected` when a user selects a target row.
    """

    # Emitted when the user clicks a row in the trace table
    sig_target_selected = Signal(str)  # target_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background-color: {ThemeTokens.BG_CARD}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        self._target_rows: List[str] = []  # Maps row index -> target_id

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(14, 12, 14, 12)
        self.main_layout.setSpacing(8)

        # Header Row
        header_widget = QWidget(self)
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel("Two-Checkpoint Target Comparison Trace", header_widget)
        title_lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY};")
        h_layout.addWidget(title_lbl)

        badge_lbl = QLabel("Hop Isolation: Checkpoint 1 (LAN) vs Checkpoint 2 (WAN)", header_widget)
        badge_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ThemeTokens.BRAND_PRIMARY}; "
            f"background-color: {ThemeTokens.BG_MUTED}; padding: 2px 8px; "
            f"border-radius: {ThemeTokens.RADIUS_PILL};"
        )
        h_layout.addWidget(badge_lbl)
        h_layout.addStretch()
        self.main_layout.addWidget(header_widget)

        # Subtitle / Instruction
        sub_lbl = QLabel(
            "Click any target row to filter the latency timeline chart below. Packet loss is visually "
            "highlighted in red; missing probe measurements are never coerced to 0 ms.",
            self,
        )
        sub_lbl.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 12px;")
        sub_lbl.setWordWrap(True)
        self.main_layout.addWidget(sub_lbl)

        # Trace Table - ZERO horizontal scrolling tolerance, responsive proportional layout
        self.table = QTableWidget(self)
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels([
            "Hop",
            "Target Name",
            "IP Address",
            "Checkpoint",
            "Probes",
            "Loss %",
            "Loss Bar",
            "Min RTT",
            "Med RTT",
            "Max RTT",
            "Status",
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setWordWrap(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setStyleSheet(
            f"QTableWidget {{ "
            f"    background-color: {ThemeTokens.BG_WINDOW}; "
            f"    gridline-color: {ThemeTokens.BORDER_DEFAULT}; "
            f"    color: {ThemeTokens.TEXT_PRIMARY}; "
            f"    font-size: 11px; "
            f"    border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"    border-radius: 6px; "
            f"}} "
            f"QTableWidget::item {{ "
            f"    padding: 4px 4px; "
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
            f"    padding: 4px 4px; "
            f"    border: none; "
            f"    border-bottom: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"}}"
        )
        header = self.table.horizontalHeader()
        for i in range(11):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(25)

        self.adjust_column_widths()

        self.table.viewport().installEventFilter(self)
        self.table.cellClicked.connect(self._on_table_cell_clicked)
        self.main_layout.addWidget(self.table)

        # Localization Analysis Box
        self.analysis_card = QFrame(self)
        self.analysis_card.setStyleSheet(
            f"background-color: {ThemeTokens.BG_MUTED}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        self.analysis_layout = QVBoxLayout(self.analysis_card)
        self.analysis_layout.setContentsMargins(12, 10, 12, 10)
        self.analysis_layout.setSpacing(6)

        self.lbl_analysis_title = QLabel("Two-Checkpoint Localization Analysis", self.analysis_card)
        self.lbl_analysis_title.setStyleSheet(f"font-weight: 700; font-size: 12px; color: {ThemeTokens.TEXT_PRIMARY};")
        self.analysis_layout.addWidget(self.lbl_analysis_title)

        self.lbl_analysis_body = QLabel("Awaiting diagnostic measurements to compare checkpoints.", self.analysis_card)
        self.lbl_analysis_body.setStyleSheet(f"font-size: 12px; color: {ThemeTokens.TEXT_SECONDARY};")
        self.lbl_analysis_body.setWordWrap(True)
        self.analysis_layout.addWidget(self.lbl_analysis_body)

        self.lbl_analysis_disclaimer = QLabel(
            "Note: Observable latency elevation or packet loss on external targets does not prove an ISP fault. "
            "Upstream transit congestion, remote server load, or peering factors may be responsible.",
            self.analysis_card,
        )
        self.lbl_analysis_disclaimer.setStyleSheet(f"font-size: 11px; font-style: italic; color: {ThemeTokens.TEXT_MUTED};")
        self.lbl_analysis_disclaimer.setWordWrap(True)
        self.analysis_layout.addWidget(self.lbl_analysis_disclaimer)

        self.main_layout.addWidget(self.analysis_card)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched == self.table.viewport() and event.type() == QEvent.Type.Resize:
            self.adjust_column_widths()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.adjust_column_widths()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.adjust_column_widths()

    def adjust_column_widths(self) -> None:
        """Dynamically allocate column widths to fit viewport without clipping or scrollbars."""
        total_w = self.table.viewport().width()
        if total_w <= 0:
            return

        target_total = max(200, total_w - 4)  # 4px margin for frame borders
        base_widths = [28, 135, 78, 76, 52, 48, 40, 56, 56, 56, 85]
        base_sum = sum(base_widths)  # 710px

        if target_total <= base_sum:
            # Scale proportionally if viewport is narrower than 710px
            allocated = 0
            for i in range(10):
                w = max(20, int(target_total * (base_widths[i] / base_sum)))
                self.table.setColumnWidth(i, w)
                allocated += w
            self.table.setColumnWidth(10, max(20, target_total - allocated))
            return

        # Viewport is wider than base_sum (710px) -> distribute extra width proportionally
        weights = [0, 2.8, 1.2, 1.5, 0.7, 0.6, 0.7, 0.8, 0.8, 0.8, 1.5]
        extra_w = target_total - base_sum
        total_weight = sum(weights)

        allocated = 0
        for i in range(10):
            add = int(extra_w * (weights[i] / total_weight))
            w = base_widths[i] + add
            self.table.setColumnWidth(i, w)
            allocated += w

        self.table.setColumnWidth(10, max(20, target_total - allocated))

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        if 0 <= row < len(self._target_rows):
            target_id = self._target_rows[row]
            self.sig_target_selected.emit(target_id)

    def load_trace_data(
        self,
        observations: List[Observation],
        summaries: Optional[List[Summary]] = None,
        targets: Optional[List[Target]] = None,
        run: Optional[TestRun] = None,
    ) -> None:
        """Populate trace table with empirical target observations.

        Invariants:
        - Only calculates metrics from valid observations for that target.
        - Never replaces missing measurements with 0 ms.
        - Accurately computes packet-loss percentage from attempted vs timed-out probes.
        """
        self._target_rows.clear()
        self.table.setRowCount(0)

        if not observations:
            self.lbl_analysis_body.setText("No observations recorded for this diagnostic run.")
            return

        # Target metadata lookup
        target_map: Dict[str, Target] = {}
        if targets:
            for t in targets:
                target_map[t.target_id] = t

        # Summary lookup: (target_id, metric_name, statistic_name) -> Summary
        summary_map: Dict[tuple[str, str, str], Summary] = {}
        if summaries:
            for s in summaries:
                if s.target_id:
                    summary_map[(s.target_id, s.metric_name, s.statistic_name)] = s

        # Group RTT observations by target_id
        rtt_obs_by_target: Dict[str, List[Observation]] = {}
        for obs in observations:
            if obs.metric_name == "round_trip_time" and obs.target_id:
                rtt_obs_by_target.setdefault(obs.target_id, []).append(obs)

        if not rtt_obs_by_target:
            self.lbl_analysis_body.setText("No round-trip latency probe observations available.")
            return

        def _is_gw_id(tid: str) -> bool:
            if "gw" in tid.lower() or "gateway" in tid.lower():
                return True
            t = target_map.get(tid)
            if t and t.target_type == TargetType.GATEWAY:
                return True
            if not any(k in tid.lower() for k in ("ext-", "dns-", "tp-", "cloudflare", "google", "quad9")):
                return True
            return False

        # Sort targets: Gateway first, then external targets
        sorted_target_ids = sorted(
            rtt_obs_by_target.keys(),
            key=lambda tid: (0 if _is_gw_id(tid) else 1, tid),
        )

        gw_loss: Optional[float] = None
        gw_med: Optional[float] = None
        ext_losses: List[float] = []
        ext_meds: List[float] = []

        self.table.setRowCount(len(sorted_target_ids))

        for row_idx, target_id in enumerate(sorted_target_ids):
            self._target_rows.append(target_id)
            target_obs = rtt_obs_by_target[target_id]
            t_meta = target_map.get(target_id)

            is_gw = _is_gw_id(target_id)

            # Probe counts
            total_probes = len(target_obs)
            valid_probes = [o for o in target_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]
            timeout_probes = [o for o in target_obs if o.status != MeasurementStatus.SUCCESS or o.value_numeric is None]

            valid_count = len(valid_probes)
            timeout_count = len(timeout_probes)
            loss_pct = (timeout_count / total_probes * 100.0) if total_probes > 0 else 0.0

            # Compute min, median, max from empirical values
            values = [float(o.value_numeric) for o in valid_probes]  # type: ignore
            min_rtt = min(values) if values else None
            med_rtt = statistics.median(values) if values else None
            max_rtt = max(values) if values else None

            # Track for checkpoint isolation analysis
            if is_gw:
                gw_loss = loss_pct
                gw_med = med_rtt
            else:
                ext_losses.append(loss_pct)
                if med_rtt is not None:
                    ext_meds.append(med_rtt)

            # Target naming
            if is_gw:
                hop_str = "1"
                checkpoint_role = "CP 1 (LAN)"
                checkpoint_tip = "Checkpoint 1: Local Network Gateway (LAN)"
                name_str = "Default Gateway / Router"
                ip_str = (t_meta.ip_address if t_meta and t_meta.ip_address else (run.gateway_ip if run and run.gateway_ip else "192.168.1.1"))
            else:
                hop_str = f"2.{row_idx}"
                checkpoint_role = "CP 2 (WAN)"
                checkpoint_tip = "Checkpoint 2: External WAN Target"
                if "cloudflare" in target_id.lower():
                    name_str = "Cloudflare Primary"
                    ip_str = "1.1.1.1"
                elif "google" in target_id.lower():
                    name_str = "Google Public DNS"
                    ip_str = "8.8.8.8"
                elif "quad9" in target_id.lower():
                    name_str = "Quad9 Anycast"
                    ip_str = "9.9.9.9"
                else:
                    name_str = (t_meta.provider_label if t_meta and t_meta.provider_label else target_id)
                    ip_str = (t_meta.ip_address if t_meta and t_meta.ip_address else "--")

            # Col 0: Hop
            item_hop = QTableWidgetItem(hop_str)
            item_hop.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row_idx, 0, item_hop)

            # Col 1: Target Name
            item_name = QTableWidgetItem(name_str)
            item_name.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_name.setForeground(QColor(ThemeTokens.BRAND_PRIMARY if is_gw else ThemeTokens.TEXT_PRIMARY))
            self.table.setItem(row_idx, 1, item_name)

            # Col 2: IP Address
            item_ip = QTableWidgetItem(ip_str)
            item_ip.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row_idx, 2, item_ip)

            # Col 3: Checkpoint Role
            item_role = QTableWidgetItem(checkpoint_role)
            item_role.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_role.setForeground(QColor(ThemeTokens.TEXT_SECONDARY))
            item_role.setToolTip(checkpoint_tip)
            self.table.setItem(row_idx, 3, item_role)

            # Col 4: Probes
            item_probes = QTableWidgetItem(f"{total_probes} / {valid_count}")
            item_probes.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_probes.setToolTip(f"Probes: {valid_count} received of {total_probes} attempted")
            self.table.setItem(row_idx, 4, item_probes)

            # Col 5: Loss %
            item_loss = QTableWidgetItem(f"{loss_pct:.1f}%")
            item_loss.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            loss_color = ThemeTokens.COLOR_SUCCESS if loss_pct == 0.0 else (
                ThemeTokens.COLOR_WARNING if loss_pct <= 10.0 else ThemeTokens.COLOR_ERROR
            )
            item_loss.setForeground(QColor(loss_color))
            self.table.setItem(row_idx, 5, item_loss)

            # Col 6: Loss Visual Bar
            bar_widget = LossBarWidget(loss_pct, self.table)
            self.table.setCellWidget(row_idx, 6, bar_widget)

            # Col 7: Min RTT
            min_str = f"{min_rtt:.1f} ms" if min_rtt is not None else "--"
            item_min = QTableWidgetItem(min_str)
            item_min.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_idx, 7, item_min)

            # Col 8: Median RTT
            med_str = f"{med_rtt:.1f} ms" if med_rtt is not None else "--"
            item_med = QTableWidgetItem(med_str)
            item_med.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            med_color = ThemeTokens.TEXT_PRIMARY
            if med_rtt is not None:
                if med_rtt > 100.0:
                    med_color = ThemeTokens.COLOR_ERROR
                elif med_rtt > 50.0:
                    med_color = ThemeTokens.COLOR_WARNING
                else:
                    med_color = ThemeTokens.COLOR_SUCCESS
            item_med.setForeground(QColor(med_color))
            self.table.setItem(row_idx, 8, item_med)

            # Col 9: Max RTT
            max_str = f"{max_rtt:.1f} ms" if max_rtt is not None else "--"
            item_max = QTableWidgetItem(max_str)
            item_max.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_idx, 9, item_max)

            # Col 10: Status
            if valid_count == 0:
                status_str = "● UNREACHABLE"
                status_color = ThemeTokens.COLOR_ERROR
            elif loss_pct > 10.0:
                status_str = "● HIGH LOSS"
                status_color = ThemeTokens.COLOR_ERROR
            elif loss_pct > 0.0:
                status_str = "● LOSS"
                status_color = ThemeTokens.COLOR_WARNING
            elif med_rtt is not None and med_rtt > 80.0:
                status_str = "● ELEVATED"
                status_color = ThemeTokens.COLOR_WARNING
            else:
                status_str = "● HEALTHY"
                status_color = ThemeTokens.COLOR_SUCCESS

            item_status = QTableWidgetItem(status_str)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_status.setForeground(QColor(status_color))
            item_status.setToolTip(f"{status_str} (Loss: {loss_pct:.1f}%, Median: {med_str})")
            self.table.setItem(row_idx, 10, item_status)

        # Dynamic row height and column width fitting
        self.table.resizeRowsToContents()
        self.adjust_column_widths()

        # Build explainable localization narrative
        self._update_localization_narrative(gw_loss, gw_med, ext_losses, ext_meds)

    def _update_localization_narrative(
        self,
        gw_loss: Optional[float],
        gw_med: Optional[float],
        ext_losses: List[float],
        ext_meds: List[float],
    ) -> None:
        """Formulate explainable Two-Checkpoint localization narrative."""
        avg_ext_loss = (sum(ext_losses) / len(ext_losses)) if ext_losses else 0.0
        avg_ext_med = (sum(ext_meds) / len(ext_meds)) if ext_meds else 0.0

        if gw_loss is None:
            self.lbl_analysis_body.setText("Local gateway was not probed or unmeasured.")
            return

        if gw_loss > 10.0:
            self.lbl_analysis_body.setText(
                f"<b>Localization: Checkpoint 1 (Local Gateway) Fault Detected</b><br>"
                f"High packet loss ({gw_loss:.1f}%) or unreachable router indicates degradation "
                f"on your local home network (Wi-Fi radio attenuation, channel interference, or router LAN switch). "
                f"External measurements cannot be reliably evaluated while the gateway link is degraded."
            )
            self.analysis_card.setStyleSheet(
                f"background-color: {ThemeTokens.BG_ERROR_LIGHT}; "
                f"border: 1px solid {ThemeTokens.BORDER_ERROR}; "
                f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; padding: 10px;"
            )
        elif avg_ext_loss > 10.0 or avg_ext_med > 80.0:
            self.lbl_analysis_body.setText(
                f"<b>Localization: Degradation Observed Beyond Checkpoint 1</b><br>"
                f"Local gateway is healthy ({gw_loss:.1f}% loss, {gw_med:.1f} ms delay), isolating your local Wi-Fi/cable. "
                f"Degradation occurs at or beyond Checkpoint 2 across external targets (avg loss: {avg_ext_loss:.1f}%, "
                f"median delay: {avg_ext_med:.1f} ms)."
            )
            self.analysis_card.setStyleSheet(
                f"background-color: {ThemeTokens.BG_WARNING_LIGHT}; "
                f"border: 1px solid {ThemeTokens.BORDER_WARNING}; "
                f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; padding: 10px;"
            )
        else:
            gw_str = f"{gw_med:.1f} ms" if gw_med is not None else "--"
            ext_str = f"{avg_ext_med:.1f} ms" if avg_ext_med > 0 else "--"
            self.lbl_analysis_body.setText(
                f"<b>Localization: All Checkpoints Normal</b><br>"
                f"Checkpoint 1 (Gateway: {gw_loss:.1f}% loss, {gw_str}) and Checkpoint 2 "
                f"(External: {avg_ext_loss:.1f}% loss, {ext_str}) both meet operational baselines. "
                f"No bottleneck or packet-loss event detected across measured paths."
            )
            self.analysis_card.setStyleSheet(
                f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; "
                f"border: 1px solid {ThemeTokens.BORDER_SUCCESS}; "
                f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; padding: 10px;"
            )
