"""Structured Empirical Evidence Matrix Widget for TicNet Results Presentation.

Presents a standardized, transparent empirical evidence matrix mapping raw network
measurements directly to diagnostic interpretations:
1. Gateway Reachability: Discovered router, reachability, probe responses.
2. Gateway Latency: Local delay distribution (min, avg, max, p50, p95).
3. External Connectivity: Multi-target probes, success/failure breakdown.
4. Packet Loss: Real lost probes, total attempts, loss percentage.
5. External Latency: Minimum, average, maximum, p50 (n>=3), p95 (n>=10).
6. DNS Resolution: Resolver, UDP 53 transport, query status, response time, RCODE details.
7. Throughput Capacity: Measured downstream transfer under bounded stream.
8. Loaded Latency (Bufferbloat): Baseline latency, loaded latency, measured delta, ratio.
9. Diagnostic Root Cause: Evaluated rule ID, priority, status, evidence strength, explanation.

Strict Invariants:
- Grounded solely in actual measurements from the diagnostic pipeline or persisted SQLite records.
- Accurate Evidence States: COLLECTED, NOT TESTED, UNAVAILABLE, FAILED, INCONCLUSIVE.
- Never converts missing measurements to 0.0 ms or 0.0 Mbps.
- Never masks failed/timed-out probes as successful responses.
- Respects statistical minimum sample sizes (p50 when n>=3, p95 when n>=10).
"""

from __future__ import annotations

from enum import Enum
import json
import statistics
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
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
from ticnet.measurements.statistics import (
    calculate_delay_summary_stats,
    calculate_median_rtt,
    calculate_p95_rtt,
    calculate_packet_loss_pct,
)
from ticnet.models.entities import Finding, Observation, Summary, Target, TestRun, TestStage
from ticnet.models.enums import FindingPriority, MeasurementStatus, RunStatus, StageStatus, TargetType


class EvidenceState(str, Enum):
    """Accurate evidence collection states conforming to SRS-F-008 integrity standards."""
    COLLECTED = "COLLECTED"
    NOT_TESTED = "NOT TESTED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class StructuredEvidenceMatrix(QFrame):
    """Empirical evidence matrix table mapping telemetry directly to diagnostic conclusions."""

    # Emitted when an evidence row is selected (evidence_name, target_id)
    sig_evidence_selected = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background-color: {ThemeTokens.BG_CARD}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        self._row_metadata: List[Tuple[str, str]] = []

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 14, 16, 14)
        self.main_layout.setSpacing(10)

        # Header Section
        header_widget = QWidget(self)
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel("Empirical Evidence Matrix", header_widget)
        title_lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY};")
        h_layout.addWidget(title_lbl)

        badge_lbl = QLabel("Direct Telemetry Correlation | Transparent Grounding", header_widget)
        badge_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ThemeTokens.BRAND_PRIMARY}; "
            f"background-color: {ThemeTokens.BG_MUTED}; padding: 2px 8px; "
            f"border-radius: {ThemeTokens.RADIUS_PILL};"
        )
        h_layout.addWidget(badge_lbl)
        h_layout.addStretch()
        self.main_layout.addWidget(header_widget)

        # Subtitle
        sub_lbl = QLabel(
            "Every diagnostic conclusion is grounded in actual empirical measurements. Missing tests are truthfully "
            "reported as 'Not Measured' or 'Unavailable' rather than coerced to zero.",
            self,
        )
        sub_lbl.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 12px;")
        sub_lbl.setWordWrap(True)
        self.main_layout.addWidget(sub_lbl)

        # 6-Column Data Table - ZERO horizontal scrolling tolerance
        self.table = QTableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Evidence Item",
            "Target / Context",
            "Actual Measurement",
            "Status",
            "Interpretation & Explanation",
            "Diagnostic Outcome",
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
            f"    padding: 4px 8px; "
            f"}} "
            f"QTableWidget::item:selected {{ "
            f"    background-color: {ThemeTokens.BRAND_HOVER}; "
            f"    color: {ThemeTokens.TEXT_PRIMARY}; "
            f"}} "
            f"QHeaderView::section {{ "
            f"    background-color: {ThemeTokens.BG_MUTED}; "
            f"    color: {ThemeTokens.TEXT_SECONDARY}; "
            f"    font-weight: 700; "
            f"    font-size: 11px; "
            f"    padding: 6px 8px; "
            f"    border: none; "
            f"    border-bottom: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"}}"
        )
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(60)

        self.table.setMinimumHeight(320)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.main_layout.addWidget(self.table)

    def _apply_proportional_column_widths(self) -> None:
        """Distribute table columns proportionally across 100% of viewport width with zero horizontal scrolling."""
        viewport_w = self.table.viewport().width()
        if viewport_w <= 0:
            viewport_w = max(600, self.width() - 36)

        # Proportions: Col 0: 16%, Col 1: 15%, Col 2: 20%, Col 3: 11%, Col 4: 25%, Col 5: 13%
        ratios = [0.16, 0.15, 0.20, 0.11, 0.25, 0.13]
        widths = [int(viewport_w * r) for r in ratios]
        # Assign remainder to Interpretation & Explanation column
        widths[4] = max(180, viewport_w - (widths[0] + widths[1] + widths[2] + widths[3] + widths[5]))

        header = self.table.horizontalHeader()
        for c in range(6):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, widths[c])
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        # Enforce equal, compact, balanced row heights
        self.table.resizeRowsToContents()
        for r in range(self.table.rowCount()):
            h = max(44, min(self.table.rowHeight(r), 62))
            self.table.setRowHeight(r, h)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_proportional_column_widths()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._apply_proportional_column_widths()

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if 0 <= row < len(self._row_metadata):
            ev_name, target_id = self._row_metadata[row]
            self.sig_evidence_selected.emit(ev_name, target_id)

    def populate_evidence_matrix(
        self,
        observations: List[Observation],
        summaries: Optional[List[Summary]] = None,
        run: Optional[TestRun] = None,
        targets: Optional[List[Target]] = None,
        stages: Optional[List[TestStage]] = None,
        findings: Optional[List[Finding]] = None,
    ) -> None:
        """Populate the standardized evidence rows strictly from actual diagnostic telemetry."""
        self._row_metadata.clear()
        self.table.setRowCount(0)

        # --------------------------------------------------------------------
        # 0. Index Pipeline Context & Stages
        # --------------------------------------------------------------------
        stages_by_order: Dict[int, TestStage] = {}
        stages_by_name: Dict[str, TestStage] = {}
        if stages:
            for st in stages:
                stages_by_order[st.stage_order] = st
                stages_by_name[st.stage_name.lower().strip()] = st

        stage1 = stages_by_order.get(1) or stages_by_name.get("gateway discovery")
        stage2 = stages_by_order.get(2) or stages_by_name.get("gateway reachability")
        stage3 = stages_by_order.get(3) or stages_by_name.get("external targets probe") or stages_by_name.get("external targets reachability")
        stage4 = stages_by_order.get(4) or stages_by_name.get("dns resolution")
        stage5 = stages_by_order.get(5) or stages_by_name.get("throughput & loaded latency") or stages_by_name.get("throughput")

        # Resolve Gateway IP and Gateway Target IDs
        gw_ip = run.gateway_ip if run and run.gateway_ip else None
        gw_target_ids: set[str] = set()

        for t in (targets or []):
            is_gw = t.target_type == TargetType.GATEWAY or (gw_ip and t.ip_address == gw_ip)
            if is_gw:
                gw_target_ids.add(t.target_id)
                if not gw_ip and t.ip_address:
                    gw_ip = t.ip_address
            elif "gateway" in (t.provider_label or "").lower() or "gw" in t.target_id.lower():
                gw_target_ids.add(t.target_id)

        if gw_ip:
            gw_target_ids.add(gw_ip)

        # --------------------------------------------------------------------
        # Partition Observations Accurately by Subsystem
        # --------------------------------------------------------------------
        gw_obs: List[Observation] = []
        ext_obs: List[Observation] = []
        dns_obs: List[Observation] = []
        tp_obs: List[Observation] = []
        delta_obs: Optional[Observation] = None
        loaded_rtt_obs: List[Observation] = []

        for o in observations:
            # Stage 2 or Gateway Probe
            if (stage2 and o.stage_id == stage2.stage_id and o.metric_name == "round_trip_time") or (
                o.target_id in gw_target_ids and o.metric_name == "round_trip_time"
            ):
                gw_obs.append(o)
            # Stage 3 or External Probe
            elif (stage3 and o.stage_id == stage3.stage_id and o.metric_name == "round_trip_time") or (
                o.metric_name == "round_trip_time" and o.target_id not in gw_target_ids
            ):
                ext_obs.append(o)
            # DNS Queries
            elif o.metric_name in ("dns_query_time", "dns_lookup_time", "dns_resolution_time") or (
                stage4 and o.stage_id == stage4.stage_id
            ):
                dns_obs.append(o)
            # Throughput
            elif o.metric_name in ("download_throughput", "throughput_mbps", "download_throughput_mbps"):
                tp_obs.append(o)
            # Loaded Latency Delta
            elif o.metric_name in ("loaded_latency_delta", "loaded_delta"):
                delta_obs = o
            # Loaded RTT Probes
            elif o.metric_name in ("loaded_rtt", "loaded_latency"):
                loaded_rtt_obs.append(o)

        # Fallback: If stages were absent and gw_target_ids empty, check for 'gw' in target_id
        if not gw_obs and not stages:
            gw_obs = [o for o in observations if o.metric_name == "round_trip_time" and "gw" in (o.target_id or "").lower()]
            ext_obs = [o for o in observations if o.metric_name == "round_trip_time" and "gw" not in (o.target_id or "").lower()]

        rows_to_render: List[Tuple[str, str, str, str, str, str, str]] = []

        # ====================================================================
        # 1. Gateway Reachability
        # ====================================================================
        gw_ctx = f"Router ({gw_ip or 'Default Gateway'})"
        if gw_obs:
            gw_total = len(gw_obs)
            gw_valid = [o for o in gw_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]
            gw_succ = len(gw_valid)
            gw_failed = gw_total - gw_succ

            if gw_succ > 0:
                gw_reach_str = f"Reachable ({gw_succ}/{gw_total} probes, {gw_succ/gw_total*100.0:.0f}%)"
                gw_status = EvidenceState.COLLECTED.value
                if gw_failed == 0:
                    gw_interp = f"Local gateway at {gw_ip or 'router'} answered all {gw_total} ICMP probes. First hop link is operational."
                    gw_relevance = "● BASELINE (Clean)"
                else:
                    gw_interp = f"Local gateway answered {gw_succ} of {gw_total} probes ({gw_failed} lost). Partial local Wi-Fi or router link degradation."
                    gw_relevance = "● SUPPORTED (Degraded)"
            else:
                gw_reach_str = f"Unreachable (0/{gw_total} probes, 100% loss)"
                gw_status = EvidenceState.FAILED.value
                gw_interp = f"Local gateway at {gw_ip or 'router'} did not answer any ICMP probes. Local physical or Wi-Fi connectivity failed."
                gw_relevance = "● SUPPORTED (Critical)"

            rows_to_render.append((
                "Gateway reachability",
                gw_ctx,
                gw_reach_str,
                gw_status,
                gw_interp,
                gw_relevance,
                "GATEWAY",
            ))
        elif stage1 and stage1.status == StageStatus.FAILED:
            rows_to_render.append((
                "Gateway reachability",
                gw_ctx,
                "Unavailable (Discovery Failed)",
                EvidenceState.UNAVAILABLE.value,
                f"Gateway discovery failed during Stage 1: {stage1.error_message or 'No gateway IP found in routing table'}.",
                "● SUPPORTED (Critical)",
                "GATEWAY",
            ))
        elif stage2 and stage2.status == StageStatus.SKIPPED:
            rows_to_render.append((
                "Gateway reachability",
                gw_ctx,
                "Not Measured (Skipped)",
                EvidenceState.UNAVAILABLE.value,
                f"Gateway reachability probing was skipped: {stage2.skip_reason or 'Upstream dependency missing'}.",
                "● UNAVAILABLE",
                "GATEWAY",
            ))
        else:
            rows_to_render.append((
                "Gateway reachability",
                gw_ctx,
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "Gateway reachability probing was not executed for this run.",
                "● NOT TESTED",
                "GATEWAY",
            ))

        # ====================================================================
        # 2. Gateway Latency
        # ====================================================================
        if gw_obs:
            gw_rtts = [float(o.value_numeric) for o in gw_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]
            if gw_rtts:
                gw_stats = calculate_delay_summary_stats(gw_rtts)
                parts = [
                    f"Min: {gw_stats['min_rtt']:.1f} ms",
                    f"Avg: {gw_stats['avg_rtt']:.1f} ms",
                    f"Max: {gw_stats['max_rtt']:.1f} ms",
                ]
                line2_parts = []
                if gw_stats["median_rtt"] is not None:
                    line2_parts.append(f"p50: {gw_stats['median_rtt']:.1f} ms")
                if gw_stats["p95_rtt"] is not None:
                    line2_parts.append(f"p95: {gw_stats['p95_rtt']:.1f} ms")

                line1 = " | ".join(parts)
                gw_lat_str = f"{line1}\n{' | '.join(line2_parts)}" if line2_parts else line1
                gw_lat_status = EvidenceState.COLLECTED.value
                effective_med = gw_stats["median_rtt"] if gw_stats["median_rtt"] is not None else gw_stats["avg_rtt"]

                if effective_med is not None and effective_med <= 20.0:
                    gw_lat_interp = f"Nominal local response delay ({effective_med:.1f} ms median) within normal Wi-Fi/Ethernet baseline."
                    gw_lat_rel = "● BASELINE (Clean)"
                elif effective_med is not None and effective_med <= 50.0:
                    gw_lat_interp = f"Moderate local delay ({effective_med:.1f} ms) — acceptable wireless home range."
                    gw_lat_rel = "● BASELINE (Acceptable)"
                else:
                    gw_lat_interp = f"Elevated local delay ({effective_med or 0:.1f} ms) — Wi-Fi radio attenuation, channel congestion, or router CPU load."
                    gw_lat_rel = "● SUPPORTED (Degraded)"
            else:
                gw_lat_str = "Unavailable (All Probes Timed Out)"
                gw_lat_status = EvidenceState.FAILED.value
                gw_lat_interp = "No valid latency samples recorded; all gateway probes timed out."
                gw_lat_rel = "● SUPPORTED (Failure)"

            rows_to_render.append((
                "Gateway latency",
                gw_ctx,
                gw_lat_str,
                gw_lat_status,
                gw_lat_interp,
                gw_lat_rel,
                "GATEWAY",
            ))
        else:
            rows_to_render.append((
                "Gateway latency",
                gw_ctx,
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "No gateway RTT measurements recorded.",
                "● NOT TESTED",
                "GATEWAY",
            ))

        # ====================================================================
        # 3. External Connectivity
        # ====================================================================
        ext_target_ids = list(dict.fromkeys(o.target_id for o in ext_obs if o.target_id))
        ext_target_count = len(ext_target_ids) if ext_target_ids else len([t for t in (targets or []) if t.target_type == TargetType.EXTERNAL_IP])
        ext_ctx = f"Multi-Target ({ext_target_count or 2} Endpoints)"

        if ext_obs:
            ext_total = len(ext_obs)
            ext_valid = [o for o in ext_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]
            ext_succ = len(ext_valid)
            ext_failed = ext_total - ext_succ

            ext_reach_str = f"Reachable ({ext_succ}/{ext_total} probes, {ext_succ/ext_total*100.0:.0f}%)"
            ext_status = EvidenceState.COLLECTED.value

            if ext_succ == ext_total:
                ext_interp = f"All {ext_target_count} independent external endpoints responded with zero probe loss."
                ext_rel = "● BASELINE (Clean)"
            elif ext_succ > 0:
                ext_interp = f"Partial reachability: {ext_failed} of {ext_total} probes lost across external test endpoints."
                ext_rel = "● SUPPORTED (Degraded)"
            else:
                ext_reach_str = f"Unreachable (0/{ext_total} probes, 100% loss)"
                ext_status = EvidenceState.FAILED.value
                ext_interp = "Total loss of external connectivity beyond the default gateway."
                ext_rel = "● SUPPORTED (Critical)"

            rows_to_render.append((
                "External connectivity",
                ext_ctx,
                ext_reach_str,
                ext_status,
                ext_interp,
                ext_rel,
                "EXTERNAL",
            ))
        elif stage3 and stage3.status == StageStatus.SKIPPED:
            rows_to_render.append((
                "External connectivity",
                ext_ctx,
                "Not Measured (Skipped)",
                EvidenceState.UNAVAILABLE.value,
                f"External probing was skipped: {stage3.skip_reason or 'Gateway unreachable per SRS-F-008'}.",
                "● UNAVAILABLE",
                "EXTERNAL",
            ))
        else:
            rows_to_render.append((
                "External connectivity",
                ext_ctx,
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "External multi-target probing was not executed.",
                "● NOT TESTED",
                "EXTERNAL",
            ))

        # ====================================================================
        # 4. Packet Loss
        # ====================================================================
        all_rtt_obs = [o for o in observations if o.metric_name == "round_trip_time"]
        if all_rtt_obs:
            tot_probes = len(all_rtt_obs)
            lost_probes = sum(1 for o in all_rtt_obs if o.status != MeasurementStatus.SUCCESS or o.value_numeric is None)
            loss_pct = calculate_packet_loss_pct(tot_probes, tot_probes - lost_probes) or 0.0

            loss_str = f"{loss_pct:.1f}% ({lost_probes}/{tot_probes} lost)"
            loss_status = EvidenceState.COLLECTED.value

            if loss_pct == 0.0:
                loss_interp = f"Zero probe loss observed across all {tot_probes} sampled destinations."
                loss_rel = "● BASELINE (Clean)"
            elif loss_pct <= 10.0:
                loss_interp = f"Intermittent probe loss ({loss_pct:.1f}%) observed across sampled destinations."
                loss_rel = "● SUPPORTED (Warning)"
            else:
                loss_interp = f"Severe probe loss ({loss_pct:.1f}%) observed across sampled destinations."
                loss_rel = "● SUPPORTED (Critical)"

            rows_to_render.append((
                "Packet loss",
                "Combined Checkpoints",
                loss_str,
                loss_status,
                loss_interp,
                loss_rel,
                "PACKET_LOSS",
            ))
        else:
            rows_to_render.append((
                "Packet loss",
                "Combined Checkpoints",
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "No probe attempts were recorded to compute packet loss.",
                "● NOT TESTED",
                "PACKET_LOSS",
            ))

        # ====================================================================
        # 5. External Latency
        # ====================================================================
        if ext_obs:
            ext_rtts = [float(o.value_numeric) for o in ext_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]
            if ext_rtts:
                ext_stats = calculate_delay_summary_stats(ext_rtts)
                parts = [
                    f"Min: {ext_stats['min_rtt']:.1f} ms",
                    f"Avg: {ext_stats['avg_rtt']:.1f} ms",
                    f"Max: {ext_stats['max_rtt']:.1f} ms",
                ]
                line2_parts = []
                if ext_stats["median_rtt"] is not None:
                    line2_parts.append(f"p50: {ext_stats['median_rtt']:.1f} ms")
                if ext_stats["p95_rtt"] is not None:
                    line2_parts.append(f"p95: {ext_stats['p95_rtt']:.1f} ms")

                line1 = " | ".join(parts)
                ext_lat_str = f"{line1}\n{' | '.join(line2_parts)}" if line2_parts else line1
                ext_lat_status = EvidenceState.COLLECTED.value
                effective_ext_med = ext_stats["median_rtt"] if ext_stats["median_rtt"] is not None else ext_stats["avg_rtt"]

                if effective_ext_med is not None and effective_ext_med <= 60.0:
                    ext_lat_interp = f"Low upstream path latency ({effective_ext_med:.1f} ms) across external internet backbones."
                    ext_lat_rel = "● BASELINE (Clean)"
                elif effective_ext_med is not None and effective_ext_med <= 120.0:
                    ext_lat_interp = f"Moderate upstream latency ({effective_ext_med:.1f} ms) — normal regional routing delay."
                    ext_lat_rel = "● BASELINE (Acceptable)"
                else:
                    ext_lat_interp = f"Elevated upstream transit latency ({effective_ext_med or 0:.1f} ms) across external backbones."
                    ext_lat_rel = "● SUPPORTED (Degraded)"
            else:
                ext_lat_str = "Unavailable (All Probes Timed Out)"
                ext_lat_status = EvidenceState.FAILED.value
                ext_lat_interp = "No valid latency samples recorded; all external probes failed or timed out."
                ext_lat_rel = "● SUPPORTED (Failure)"

            rows_to_render.append((
                "External latency",
                ext_ctx,
                ext_lat_str,
                ext_lat_status,
                ext_lat_interp,
                ext_lat_rel,
                "EXTERNAL",
            ))
        else:
            rows_to_render.append((
                "External latency",
                ext_ctx,
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "External path latency probing was not executed.",
                "● NOT TESTED",
                "EXTERNAL",
            ))

        # ====================================================================
        # 6. DNS Resolution
        # ====================================================================
        dns_ctx = "Resolvers (Port 53 UDP)"
        if dns_obs:
            dns_total = len(dns_obs)
            dns_valid = [o for o in dns_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]
            dns_succ = len(dns_valid)
            dns_failed = dns_total - dns_succ

            # Parse metadata
            resolver_ips = list(dict.fromkeys(
                json.loads(o.method_metadata).get("resolver_ip")
                for o in dns_obs if o.method_metadata and "resolver_ip" in json.loads(o.method_metadata)
            ))
            if resolver_ips:
                dns_ctx = f"Resolvers ({', '.join(resolver_ips[:2])} via UDP 53)"

            if dns_succ == dns_total:
                dns_vals = [float(o.value_numeric) for o in dns_valid]  # type: ignore
                med_dns = statistics.median(dns_vals)
                dns_str = f"NOERROR ({dns_succ}/{dns_total} resolved) | {med_dns:.1f} ms"
                dns_status = EvidenceState.COLLECTED.value
                dns_interp = f"Direct DNS queries resolved successfully bypassing OS cache (median response: {med_dns:.1f} ms)."
                dns_rel = "● BASELINE (Clean)" if med_dns <= 50.0 else "● BASELINE (Acceptable)"

            elif dns_succ > 0:
                dns_vals = [float(o.value_numeric) for o in dns_valid]  # type: ignore
                med_dns = statistics.median(dns_vals)
                dns_str = f"Partial Resolution ({dns_succ}/{dns_total} resolved) | {med_dns:.1f} ms"
                dns_status = EvidenceState.COLLECTED.value
                dns_interp = f"DNS resolution was degraded: {dns_failed} of {dns_total} queries failed or timed out."
                dns_rel = "● SUPPORTED (Degraded)"
            else:
                # All queries failed
                rcodes = [
                    json.loads(o.method_metadata).get("rcode_name") or o.error_message or "TIMEOUT"
                    for o in dns_obs if o.method_metadata
                ]
                rcode_summary = rcodes[0] if rcodes else "TIMEOUT"
                dns_str = f"Resolution Failed ({rcode_summary}, 0/{dns_total} resolved)"
                dns_status = EvidenceState.FAILED.value
                dns_interp = f"Primary DNS resolver failed to resolve queries via UDP 53 ({rcode_summary}). Name resolution subsystem down."
                dns_rel = "● SUPPORTED (Critical)"

            rows_to_render.append((
                "DNS resolution",
                dns_ctx,
                dns_str,
                dns_status,
                dns_interp,
                dns_rel,
                "DNS",
            ))
        elif stage4 and stage4.status == StageStatus.SKIPPED:
            rows_to_render.append((
                "DNS resolution",
                dns_ctx,
                "Not Measured (Skipped)",
                EvidenceState.UNAVAILABLE.value,
                f"DNS resolution stage was skipped: {stage4.skip_reason or 'Upstream connectivity failure'}.",
                "● UNAVAILABLE",
                "DNS",
            ))
        else:
            rows_to_render.append((
                "DNS resolution",
                dns_ctx,
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "Stage 4 DNS query timing was skipped or unrecorded.",
                "● NOT TESTED",
                "DNS",
            ))

        # ====================================================================
        # 7. Throughput Capacity
        # ====================================================================
        tp_ctx = "Downstream Transfer (CDN)"
        # Check if throughput observation exists directly
        tp_valid_obs = [o for o in tp_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None]

        # Or if throughput was recorded in loaded latency metadata
        load_tp_mbps: Optional[float] = None
        load_bytes: Optional[int] = None
        if delta_obs and delta_obs.method_metadata:
            try:
                meta = json.loads(delta_obs.method_metadata)
                load_tp_mbps = meta.get("load_throughput_mbps")
                load_bytes = meta.get("load_bytes_transferred")
            except Exception:
                pass

        if tp_valid_obs:
            tp_val = float(tp_valid_obs[0].value_numeric)  # type: ignore
            tp_str = f"{tp_val:.1f} Mbps (HTTP Stream)"
            tp_status = EvidenceState.COLLECTED.value
            tp_interp = f"Measured downstream capacity under bounded transfer ({tp_val:.1f} Mbps)."
            tp_rel = "● BASELINE (Evaluated)"
            rows_to_render.append((
                "Throughput capacity",
                tp_ctx,
                tp_str,
                tp_status,
                tp_interp,
                tp_rel,
                "THROUGHPUT",
            ))
        elif load_tp_mbps is not None and load_tp_mbps > 0.0:
            bytes_str = f" ({load_bytes / (1024*1024):.1f} MB transfer)" if load_bytes else ""
            tp_str = f"{load_tp_mbps:.1f} Mbps (Active Load Stream){bytes_str}"
            tp_status = EvidenceState.COLLECTED.value
            tp_interp = f"Sustained downstream transfer rate during active concurrent load testing ({load_tp_mbps:.1f} Mbps)."
            tp_rel = "● BASELINE (Evaluated)"
            rows_to_render.append((
                "Throughput capacity",
                tp_ctx,
                tp_str,
                tp_status,
                tp_interp,
                tp_rel,
                "THROUGHPUT",
            ))
        elif tp_obs and tp_obs[0].status != MeasurementStatus.SUCCESS:
            err = tp_obs[0].error_message or "Transfer error"
            rows_to_render.append((
                "Throughput capacity",
                tp_ctx,
                f"Measurement Failed ({err})",
                EvidenceState.FAILED.value,
                f"Downstream throughput measurement failed during execution: {err}.",
                "● SUPPORTED (Warning)",
                "THROUGHPUT",
            ))
        elif stage5 and stage5.status == StageStatus.SKIPPED:
            rows_to_render.append((
                "Throughput capacity",
                tp_ctx,
                "Not Measured (Skipped)",
                EvidenceState.UNAVAILABLE.value,
                f"Stage 5 throughput test was skipped: {stage5.skip_reason or 'Gateway unreachable per SRS-F-008'}.",
                "● UNAVAILABLE",
                "THROUGHPUT",
            ))
        else:
            rows_to_render.append((
                "Throughput capacity",
                tp_ctx,
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "Downstream throughput test was skipped or not authorized.",
                "● NOT TESTED",
                "THROUGHPUT",
            ))

        # ====================================================================
        # 8. Loaded Latency (Bufferbloat)
        # ====================================================================
        load_ctx = "Bufferbloat Comparison"
        if delta_obs:
            base_med = None
            loaded_med = None
            delta_val = delta_obs.value_numeric
            ratio_val = None
            load_active = True

            if delta_obs.method_metadata:
                try:
                    meta = json.loads(delta_obs.method_metadata)
                    base_med = meta.get("baseline_median_rtt_ms")
                    loaded_med = meta.get("loaded_median_rtt_ms")
                    if delta_val is None:
                        delta_val = meta.get("delta_rtt_median_ms")
                    ratio_val = meta.get("bufferbloat_ratio")
                    load_active = meta.get("load_active_during_probes", True)
                    if meta.get("target_ip"):
                        load_ctx = f"Bufferbloat Comparison ({meta.get('target_ip')})"
                except Exception:
                    pass

            if delta_val is not None and delta_obs.status == MeasurementStatus.SUCCESS:
                if not load_active:
                    load_str = f"Inconclusive (Traffic inactive during probes)"
                    load_status = EvidenceState.INCONCLUSIVE.value
                    load_interp = "Background download terminated before loaded probes completed; bufferbloat cannot be confirmed."
                    load_rel = "● INCONCLUSIVE"
                else:
                    b_str = f"Base: {base_med:.1f} ms" if base_med is not None else "Base: --"
                    l_str = f"Loaded: {loaded_med:.1f} ms" if loaded_med is not None else "Loaded: --"
                    r_str = f" | Ratio: {ratio_val:.1f}x" if ratio_val is not None else ""
                    load_str = f"{b_str} | {l_str}\nDelta: +{float(delta_val):.1f} ms{r_str}"
                    load_status = EvidenceState.COLLECTED.value

                    if float(delta_val) <= 30.0:
                        load_interp = f"Minimal latency inflation under load (+{float(delta_val):.1f} ms) — robust queueing and minimal bufferbloat."
                        load_rel = "● BASELINE (Clean)"
                    elif float(delta_val) <= 70.0:
                        load_interp = f"Moderate bufferbloat (+{float(delta_val):.1f} ms) — packet queuing delay under download transfer."
                        load_rel = "● SUPPORTED (Warning)"
                    else:
                        load_interp = f"Severe bufferbloat (+{float(delta_val):.1f} ms) — large unmanaged queue inflation under traffic load."
                        load_rel = "● SUPPORTED (Critical)"
            else:
                load_str = f"Measurement Failed ({delta_obs.error_message or 'Evaluation failed'})"
                load_status = EvidenceState.FAILED.value
                load_interp = f"Loaded latency measurement failed: {delta_obs.error_message or 'Probing error'}."
                load_rel = "● SUPPORTED (Failure)"

            rows_to_render.append((
                "Loaded latency (Bufferbloat)",
                load_ctx,
                load_str,
                load_status,
                load_interp,
                load_rel,
                "BUFFERBLOAT",
            ))
        elif stage5 and stage5.status == StageStatus.SKIPPED:
            rows_to_render.append((
                "Loaded latency (Bufferbloat)",
                load_ctx,
                "Not Measured (Skipped)",
                EvidenceState.UNAVAILABLE.value,
                f"Stage 5 loaded latency was skipped: {stage5.skip_reason or 'Gateway unreachable per SRS-F-008'}.",
                "● UNAVAILABLE",
                "BUFFERBLOAT",
            ))
        else:
            rows_to_render.append((
                "Loaded latency (Bufferbloat)",
                load_ctx,
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "Stage 5 loaded latency was not evaluated for this session.",
                "● NOT TESTED",
                "BUFFERBLOAT",
            ))

        # ====================================================================
        # 9. Diagnostic Root Cause (DiagnosisEngine Rule Evaluation)
        # ====================================================================
        if findings:
            primary_finding = findings[0]
            rule_id = primary_finding.rule_id
            cat_str = primary_finding.category.value if hasattr(primary_finding.category, "value") else str(primary_finding.category)
            stat_str = primary_finding.status.value if hasattr(primary_finding.status, "value") else str(primary_finding.status)
            strength_str = primary_finding.evidence_strength.value if hasattr(primary_finding.evidence_strength, "value") else str(primary_finding.evidence_strength)

            diag_ctx = f"Rule {rule_id} ({cat_str})"
            diag_meas = f"{rule_id}: {stat_str} ({strength_str} Evidence)"

            if rule_id == "R-INCONCLUSIVE-01":
                diag_status = EvidenceState.INCONCLUSIVE.value
                diag_rel = "● INCONCLUSIVE"
            else:
                diag_status = EvidenceState.COLLECTED.value
                if primary_finding.priority == FindingPriority.CRITICAL:
                    diag_rel = "● SUPPORTED (Critical)"
                elif primary_finding.priority == FindingPriority.WARNING:
                    diag_rel = "● SUPPORTED (Warning)"
                elif primary_finding.priority == FindingPriority.INFO:
                    diag_rel = "● BASELINE (Info)"
                else:
                    diag_rel = "● SUPPORTED (Evaluated)"

            diag_interp = f"{primary_finding.plain_summary} Limitation: {primary_finding.limitations}"

            rows_to_render.append((
                "Diagnostic root cause",
                diag_ctx,
                diag_meas,
                diag_status,
                diag_interp,
                diag_rel,
                "DIAGNOSIS",
            ))
        elif run and run.status == RunStatus.COMPLETED:
            rows_to_render.append((
                "Diagnostic root cause",
                "Deterministic Rules",
                "Nominal / Clean (0 Faults)",
                EvidenceState.COLLECTED.value,
                "All deterministic diagnostic rules evaluated against empirical evidence without triggering fault thresholds.",
                "● BASELINE (Clean)",
                "DIAGNOSIS",
            ))
        elif run and run.status in (RunStatus.FAILED, RunStatus.CANCELLED):
            err_msg = run.overall_error or f"Run ended with {run.status.value}"
            rows_to_render.append((
                "Diagnostic root cause",
                f"Execution ({run.status.value})",
                f"Interrupted ({run.status.value})",
                EvidenceState.FAILED.value if run.status == RunStatus.FAILED else EvidenceState.UNAVAILABLE.value,
                f"Diagnostic run was {run.status.value.lower()}; rule evaluation halted ({err_msg}).",
                "● SUPPORTED (Failure)",
                "DIAGNOSIS",
            ))
        else:
            rows_to_render.append((
                "Diagnostic root cause",
                "Rules Not Evaluated",
                "Not Measured",
                EvidenceState.NOT_TESTED.value,
                "No diagnostic findings or rule evaluation results available.",
                "● NOT TESTED",
                "DIAGNOSIS",
            ))

        # ====================================================================
        # Render Rows to QTableWidget
        # ====================================================================
        self.table.setRowCount(len(rows_to_render))
        for row_idx, (ev_name, ctx, meas, status_val, interp, relevance, target_id) in enumerate(rows_to_render):
            self._row_metadata.append((ev_name, target_id))

            # Col 0: Evidence Item
            item_name = QTableWidgetItem(ev_name)
            item_name.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_name.setForeground(QColor(ThemeTokens.TEXT_PRIMARY))
            item_name.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            self.table.setItem(row_idx, 0, item_name)

            # Col 1: Target / Context
            item_ctx = QTableWidgetItem(ctx)
            item_ctx.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_ctx.setForeground(QColor(ThemeTokens.TEXT_SECONDARY))
            item_ctx.setFont(QFont(ThemeTokens.FONT_MONO, 10))
            self.table.setItem(row_idx, 1, item_ctx)

            # Col 2: Actual Measurement
            item_meas = QTableWidgetItem(meas)
            item_meas.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_meas.setFont(QFont(ThemeTokens.FONT_MONO, 10))
            if "Not Measured" in meas or "Unavailable" in meas:
                item_meas.setForeground(QColor(ThemeTokens.TEXT_MUTED))
            elif "Failed" in meas or "Unreachable" in meas:
                item_meas.setForeground(QColor(ThemeTokens.COLOR_ERROR))
            elif "Inconclusive" in meas:
                item_meas.setForeground(QColor("#8b5cf6"))
            else:
                item_meas.setForeground(QColor(ThemeTokens.BRAND_PRIMARY))
            self.table.setItem(row_idx, 2, item_meas)

            # Col 3: Status Badge
            item_status = QTableWidgetItem(status_val)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_status.setFont(QFont(ThemeTokens.FONT_MONO, 9, QFont.Weight.Bold))
            if status_val == EvidenceState.COLLECTED.value:
                item_status.setForeground(QColor(ThemeTokens.COLOR_SUCCESS))
            elif status_val == EvidenceState.FAILED.value:
                item_status.setForeground(QColor(ThemeTokens.COLOR_ERROR))
            elif status_val == EvidenceState.UNAVAILABLE.value:
                item_status.setForeground(QColor(ThemeTokens.COLOR_WARNING))
            elif status_val == EvidenceState.INCONCLUSIVE.value:
                item_status.setForeground(QColor("#8b5cf6"))
            else:  # NOT TESTED
                item_status.setForeground(QColor(ThemeTokens.TEXT_MUTED))
            self.table.setItem(row_idx, 3, item_status)

            # Col 4: Interpretation & Explanation
            item_interp = QTableWidgetItem(interp)
            item_interp.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item_interp.setForeground(QColor(ThemeTokens.TEXT_SECONDARY))
            item_interp.setFont(QFont("Segoe UI", 10))
            self.table.setItem(row_idx, 4, item_interp)

            # Col 5: Diagnostic Outcome
            item_rel = QTableWidgetItem(relevance)
            item_rel.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_rel.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            if "Critical" in relevance or "Failure" in relevance:
                item_rel.setForeground(QColor(ThemeTokens.COLOR_ERROR))
            elif "Warning" in relevance or "Degraded" in relevance:
                item_rel.setForeground(QColor(ThemeTokens.COLOR_WARNING))
            elif "Clean" in relevance or "Normal" in relevance or "Baseline" in relevance or "Evaluated" in relevance or "Info" in relevance:
                item_rel.setForeground(QColor(ThemeTokens.COLOR_SUCCESS))
            elif "INCONCLUSIVE" in relevance:
                item_rel.setForeground(QColor("#8b5cf6"))
            else:
                item_rel.setForeground(QColor(ThemeTokens.TEXT_MUTED))
            self.table.setItem(row_idx, 5, item_rel)

        # Distribute columns proportionally and enforce compact, balanced row heights
        self._apply_proportional_column_widths()
