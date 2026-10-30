"""Diagnosis page for TicNet desktop presentation layer.

Coordinates sequential multi-stage network probing, displays real-time stage progress,
and triggers deterministic rule evaluations without blocking the GUI thread.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.services.diagnostic_service import DiagnosticService
from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.diagnostic_activity_widget import (
    ActivityCategory,
    ActivityEvent,
    DiagnosticActivityFeedWidget,
)
from ticnet.desktop.widgets.radial_gauge_widget import GaugeState, RadialGaugeWidget
from ticnet.desktop.widgets.run_id_badge import RunIdBadge
from ticnet.desktop.widgets.stage_card_widget import (
    DiagnosticStageCard,
    Stage1Data,
    Stage2Data,
    Stage3Data,
    Stage4Data,
    Stage5Data,
    StageCardState,
    StageMeasurementExtractor,
)
from ticnet.models.entities import now_utc_iso


class DiagnosisPage(BasePage):
    """Diagnosis page presenting live stage execution and real workflow controls."""

    def __init__(
        self,
        diagnostic_service: Optional[DiagnosticService] = None,
        on_view_results: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="Run Diagnosis",
            subtitle="Sequential Multi-Stage Network Measurement and Fault Localization",
            parent=parent,
        )
        self.diagnostic_service = diagnostic_service
        self.on_view_results = on_view_results

        self.stage_status_labels: Dict[int, QLabel] = {}
        self.stage_boxes: Dict[int, QFrame] = {}
        self._last_run_id: Optional[str] = None
        self._active_run_id: Optional[str] = None
        self._confirmed_progress: float = 0.0
        self._failed_stages: int = 0
        self._skipped_stages: int = 0
        self._inconclusive_stages: int = 0

        self._extractor: StageMeasurementExtractor = StageMeasurementExtractor(
            db=self.diagnostic_service.db if self.diagnostic_service else None
        )

        self._populate_content()

        if self.diagnostic_service:
            self.connect_service(self.diagnostic_service)

    def connect_service(self, service: DiagnosticService) -> None:
        """Bind Qt signals from DiagnosticService to live UI slots."""
        self.diagnostic_service = service
        self._extractor.db = service.db
        service.sig_run_started.connect(self._on_run_started)
        service.sig_stage_started.connect(self._on_stage_started)
        service.sig_stage_progress.connect(self._on_stage_progress)
        service.sig_stage_completed.connect(self._on_stage_completed)
        service.sig_stage_skipped.connect(self._on_stage_skipped)
        service.sig_stage_failed.connect(self._on_stage_failed)
        service.sig_run_finished.connect(self._on_run_finished)
        service.sig_diagnosis_completed.connect(self._on_diagnosis_completed)
        service.sig_error.connect(self._on_error)

    def disconnect_service(self) -> None:
        """Safely disconnect DiagnosticService signals before widget teardown."""
        if not self.diagnostic_service:
            return

        for sig in [
            self.diagnostic_service.sig_run_started,
            self.diagnostic_service.sig_stage_started,
            self.diagnostic_service.sig_stage_progress,
            self.diagnostic_service.sig_stage_completed,
            self.diagnostic_service.sig_stage_skipped,
            self.diagnostic_service.sig_stage_failed,
            self.diagnostic_service.sig_run_finished,
            self.diagnostic_service.sig_diagnosis_completed,
            self.diagnostic_service.sig_error,
        ]:
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass

    def _populate_content(self) -> None:
        # Pre-Run Controls Card
        control_card, control_layout = self.create_card(
            title="Diagnostic Run Controls",
            description=(
                "Initiates a structured 5-stage sequential measurement pipeline. Measurements are "
                "executed without administrative privileges and stored locally in SQLite."
            ),
        )

        btn_row = QWidget(control_card)
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 6, 0, 0)
        btn_layout.setSpacing(8)

        self.btn_start = QPushButton("Start Diagnosis", btn_row)
        self.btn_start.setObjectName("primaryButton")
        self.btn_start.clicked.connect(self._handle_start_clicked)

        self.btn_cancel = QPushButton("Cancel Diagnosis", btn_row)
        self.btn_cancel.setObjectName("dangerButton")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._handle_cancel_clicked)

        self.btn_view_results = QPushButton("View Results & Evidence", btn_row)
        self.btn_view_results.setObjectName("primaryButton")
        self.btn_view_results.setVisible(False)
        self.btn_view_results.clicked.connect(self._handle_view_results_clicked)

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_view_results)
        btn_layout.addStretch()

        control_layout.addWidget(btn_row)

        # Active Run Status Banner & Run ID Badge Row
        status_row = QWidget(control_card)
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 4, 0, 0)
        status_layout.setSpacing(10)

        self.lbl_session_status = QLabel("Run: Idle | Ready to diagnose", status_row)
        self.lbl_session_status.setStyleSheet(
            f"color: {ThemeTokens.TEXT_MUTED}; font-family: {ThemeTokens.FONT_MONO}; font-size: 12px; font-weight: 500;"
        )
        self.lbl_run_status = self.lbl_session_status
        status_layout.addWidget(self.lbl_session_status, 1)

        self.run_id_badge = RunIdBadge(parent=status_row, compact=True)
        self.run_id_badge.setVisible(False)
        status_layout.addWidget(self.run_id_badge)

        control_layout.addWidget(status_row)

        self.content_layout.addWidget(control_card)

        # Central Diagnostic Dashboard Row (Radial Gauge + Live Activity Feed)
        dashboard_row = QWidget(self.content_widget)
        dashboard_layout = QHBoxLayout(dashboard_row)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(12)

        # Left Column: Radial Progress Gauge Card
        gauge_card = QFrame(dashboard_row)
        gauge_card.setObjectName("gaugeCard")
        gauge_card.setStyleSheet(
            f"#gaugeCard {{"
            f"  background-color: {ThemeTokens.BG_CARD};"
            f"  border: 1px solid {ThemeTokens.BORDER_DEFAULT};"
            f"  border-radius: {ThemeTokens.RADIUS_LARGE};"
            f"}}"
        )
        gauge_card.setMinimumWidth(260)
        gauge_card.setMaximumWidth(340)

        gauge_card_layout = QVBoxLayout(gauge_card)
        gauge_card_layout.setContentsMargins(14, 14, 14, 14)
        gauge_card_layout.setSpacing(8)

        self.gauge_widget = RadialGaugeWidget(parent=gauge_card)
        gauge_card_layout.addWidget(self.gauge_widget, 1, Qt.AlignmentFlag.AlignCenter)

        dashboard_layout.addWidget(gauge_card, 0)

        # Right Column: Live Diagnostic Activity Feed
        self.activity_feed_widget = DiagnosticActivityFeedWidget(parent=dashboard_row)
        self.activity_feed_widget.setMinimumHeight(280)
        dashboard_layout.addWidget(self.activity_feed_widget, 1)

        self.content_layout.addWidget(dashboard_row)

        # 5-Stage Architecture Overview Card
        stages_card, stages_layout = self.create_card(
            title="Diagnostic Architecture",
            description="Diagnostic measurements execute in strict sequential stages to isolate root causes:",
        )

        stages = [
            (1, "Stage 1", "Default Gateway Discovery", "Inspects routing table unprivileged to identify local router IP."),
            (2, "Stage 2", "Gateway Reachability & Delay", "Measures packet loss, median RTT, and delay variation (MACD) to local gateway."),
            (3, "Stage 3", "Multi-Target External Probing", "Measures packet loss and latency across multiple diverse external servers."),
            (4, "Stage 4", "Direct DNS Query Timing", "Bypasses OS resolver cache via UDP port 53 to measure resolver response times and RCODEs."),
            (5, "Stage 5", "Throughput & Loaded Latency", "Evaluates downstream throughput and bufferbloat under bounded load (consent required)."),
        ]

        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        for idx, (order, stage_num, name, desc) in enumerate(stages):
            card = DiagnosticStageCard(
                order=order,
                stage_num=stage_num,
                name=name,
                desc=desc,
                parent=stages_card,
            )
            self.stage_status_labels[order] = card.lbl_status
            self.stage_boxes[order] = card

            if idx == 4:
                grid.addWidget(card, 2, 0, 1, 2)
            else:
                grid.addWidget(card, idx // 2, idx % 2)

        stages_layout.addLayout(grid)
        self.content_layout.addWidget(stages_card)

        # Retain hidden empty_state attribute for internal state consistency without rendering
        self.empty_state = QWidget(self)
        self.empty_state.setVisible(False)

    def _handle_start_clicked(self) -> None:
        if not self.diagnostic_service:
            self.lbl_session_status.setText("Error: DiagnosticService not attached.")
            return

        try:
            self.btn_view_results.setVisible(False)
            self._reset_stage_boxes()
            self.gauge_widget.reset()
            self.activity_feed_widget.clear()
            self._confirmed_progress = 0.0
            self._failed_stages = 0
            self._skipped_stages = 0
            self._inconclusive_stages = 0
            self.diagnostic_service.start_diagnosis()
        except Exception as exc:
            self.lbl_session_status.setText(f"Error starting diagnosis: {exc}")

    def _handle_cancel_clicked(self) -> None:
        if self.diagnostic_service:
            self.lbl_session_status.setText("Cancellation requested... Aborting active probe operations.")
            self.diagnostic_service.cancel_diagnosis()

    def _handle_view_results_clicked(self) -> None:
        if self.on_view_results and self._last_run_id:
            self.on_view_results(self._last_run_id)

    def _reset_stage_boxes(self) -> None:
        for order, box in self.stage_boxes.items():
            if isinstance(box, DiagnosticStageCard):
                box.reset()
            else:
                box.setStyleSheet(
                    f"background-color: {ThemeTokens.BG_CARD}; "
                    f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
                    f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
                    f"padding: 12px;"
                )
        for order, lbl in self.stage_status_labels.items():
            lbl.setText("Status: Pending")
            lbl.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 11px; font-weight: 600;")

    # =========================================================================
    # Slot Implementations for DiagnosticService Signals
    # =========================================================================

    def _on_run_started(self, run_id: str) -> None:
        self._active_run_id = run_id
        self._last_run_id = run_id
        self._confirmed_progress = 0.0
        self._failed_stages = 0
        self._skipped_stages = 0
        self._inconclusive_stages = 0

        if self.diagnostic_service:
            self._extractor.db = self.diagnostic_service.db
        self._reset_stage_boxes()

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_view_results.setVisible(False)
        self.run_id_badge.set_run_id(run_id)
        self.run_id_badge.setVisible(True)
        self.lbl_session_status.setText(f"Active Run: {run_id} | Probing started...")
        self.lbl_session_status.setStyleSheet(
            f"color: {ThemeTokens.BRAND_PRIMARY}; font-size: 12px; margin-top: 6px; font-weight: 600;"
        )

        self.gauge_widget.reset()
        self.gauge_widget.set_status(GaugeState.RUNNING)
        self.activity_feed_widget.clear()
        self.activity_feed_widget.set_execution_state("RUNNING", is_active=True)
        self.activity_feed_widget.add_activity(
            category=ActivityCategory.DIAGNOSTIC_STARTED,
            message=f"Diagnostic session {run_id} initiated. Multi-stage measurement pipeline started.",
            timestamp=now_utc_iso(),
        )

    def _on_stage_started(self, run_id: str, stage_order: int, stage_name: str) -> None:
        if self._active_run_id and run_id != self._active_run_id:
            return

        box = self.stage_boxes.get(stage_order)
        if isinstance(box, DiagnosticStageCard):
            box.set_stage_state(StageCardState.RUNNING, f"Status: In Progress ({stage_name})...")
        else:
            lbl = self.stage_status_labels.get(stage_order)
            if lbl:
                lbl.setText(f"Status: In Progress ({stage_name})...")
                lbl.setStyleSheet(f"color: {ThemeTokens.BRAND_PRIMARY}; font-size: 11px; font-weight: 600;")
            if box:
                box.setStyleSheet(
                    f"background-color: {ThemeTokens.BG_INFO_LIGHT}; "
                    f"border: 1px solid {ThemeTokens.BORDER_INFO}; "
                    f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
                    f"padding: 10px;"
                )

        base_progress = max(self._confirmed_progress, (stage_order - 1) * 20.0)
        self.gauge_widget.set_progress(base_progress)
        self.gauge_widget.set_status(GaugeState.RUNNING)
        self.gauge_widget.set_active_stage(f"Stage {stage_order}: {stage_name}")

        self.activity_feed_widget.set_execution_state("RUNNING", is_active=True)
        self.activity_feed_widget.add_activity(
            category=ActivityCategory.STAGE_STARTED,
            message=f"Starting Stage {stage_order} ({stage_name})...",
            stage_order=stage_order,
            stage_name=stage_name,
            timestamp=now_utc_iso(),
        )

    def _on_stage_progress(self, run_id: str, stage_order: int, stage_name: str, message: str) -> None:
        if self._active_run_id and run_id != self._active_run_id:
            return

        box = self.stage_boxes.get(stage_order)
        if isinstance(box, DiagnosticStageCard):
            box.update_progress(message)
        else:
            lbl = self.stage_status_labels.get(stage_order)
            if lbl:
                lbl.setText(f"Status: {stage_name} — {message}")

        self.activity_feed_widget.add_activity(
            category=ActivityCategory.STAGE_PROGRESS,
            message=message,
            stage_order=stage_order,
            stage_name=stage_name,
            timestamp=now_utc_iso(),
        )

    def _on_stage_completed(self, run_id: str, stage_order: int, stage_name: str, status: str) -> None:
        if self._active_run_id and run_id != self._active_run_id:
            return

        is_inconclusive = status.upper() == "INCONCLUSIVE"
        if is_inconclusive:
            self._inconclusive_stages += 1

        box = self.stage_boxes.get(stage_order)
        summary: Optional[str] = None
        if isinstance(box, DiagnosticStageCard):
            card_state = (
                StageCardState.INCONCLUSIVE
                if is_inconclusive
                else StageCardState.SUCCESS
            )
            box.set_stage_state(card_state, f"Status: Completed ({status})")
            summary = self._update_card_measurements(run_id, stage_order)
        else:
            lbl = self.stage_status_labels.get(stage_order)
            if lbl:
                lbl.setText(f"Status: Completed ({status})")
                lbl.setStyleSheet(f"color: {ThemeTokens.COLOR_SUCCESS}; font-size: 11px; font-weight: 600;")
            if box:
                box.setStyleSheet(
                    f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; "
                    f"border: 1px solid {ThemeTokens.BORDER_SUCCESS}; "
                    f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
                    f"padding: 10px;"
                )

        # Advance to evaluated stage boundary (20% per completed stage)
        self._confirmed_progress = stage_order * 20.0
        self.gauge_widget.set_progress(self._confirmed_progress)
        if summary:
            self.gauge_widget.set_current_metric(summary)

        cat = ActivityCategory.INCONCLUSIVE if is_inconclusive else ActivityCategory.STAGE_COMPLETED
        self.activity_feed_widget.add_activity(
            category=cat,
            message=f"Stage {stage_order} ({stage_name}) completed: {status}",
            stage_order=stage_order,
            stage_name=stage_name,
            status=status,
            measurement=summary,
            timestamp=now_utc_iso(),
        )

    def _on_stage_skipped(self, run_id: str, stage_order: int, stage_name: str, reason: str) -> None:
        if self._active_run_id and run_id != self._active_run_id:
            return

        self._skipped_stages += 1

        box = self.stage_boxes.get(stage_order)
        if isinstance(box, DiagnosticStageCard):
            box.set_stage_state(StageCardState.SKIPPED, f"Status: Skipped ({reason})")
            self._update_card_measurements(run_id, stage_order, skip_reason=reason)
        else:
            lbl = self.stage_status_labels.get(stage_order)
            if lbl:
                lbl.setText(f"Status: Skipped ({reason})")
                lbl.setStyleSheet(f"color: {ThemeTokens.COLOR_WARNING}; font-size: 11px; font-weight: 600;")
            if box:
                box.setStyleSheet(
                    f"background-color: {ThemeTokens.BG_WARNING_LIGHT}; "
                    f"border: 1px solid {ThemeTokens.BORDER_WARNING}; "
                    f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
                    f"padding: 10px;"
                )

        # Skipped stage advances evaluated boundary, but remains visibly marked SKIPPED
        self._confirmed_progress = stage_order * 20.0
        self.gauge_widget.set_progress(self._confirmed_progress)
        self.gauge_widget.set_current_metric(f"Skipped: {reason}")

        self.activity_feed_widget.add_activity(
            category=ActivityCategory.STAGE_SKIPPED,
            message=f"Stage {stage_order} ({stage_name}) skipped: {reason}",
            stage_order=stage_order,
            stage_name=stage_name,
            status="SKIPPED",
            measurement=f"Reason: {reason}",
            timestamp=now_utc_iso(),
        )

    def _on_stage_failed(self, run_id: str, stage_order: int, stage_name: str, error: str) -> None:
        if self._active_run_id and run_id != self._active_run_id:
            return

        self._failed_stages += 1

        box = self.stage_boxes.get(stage_order)
        if isinstance(box, DiagnosticStageCard):
            box.set_stage_state(StageCardState.FAILED, f"Status: Failed ({error})")
            self._update_card_measurements(run_id, stage_order, error_msg=error)
        else:
            lbl = self.stage_status_labels.get(stage_order)
            if lbl:
                lbl.setText(f"Status: Failed ({error})")
                lbl.setStyleSheet(f"color: {ThemeTokens.COLOR_ERROR}; font-size: 11px; font-weight: 600;")
            if box:
                box.setStyleSheet(
                    f"background-color: {ThemeTokens.BG_ERROR_LIGHT}; "
                    f"border: 1px solid {ThemeTokens.BORDER_ERROR}; "
                    f"border-radius: {ThemeTokens.RADIUS_DEFAULT}; "
                    f"padding: 10px;"
                )

        # Preserve last confirmed progress boundary
        self.gauge_widget.set_status(GaugeState.FAILED)
        self.gauge_widget.set_current_metric(f"Failed: {error}")

        self.activity_feed_widget.add_activity(
            category=ActivityCategory.STAGE_FAILED,
            message=f"Stage {stage_order} ({stage_name}) failed: {error}",
            stage_order=stage_order,
            stage_name=stage_name,
            status="FAILED",
            measurement=f"Error: {error}",
            is_terminal=True,
            timestamp=now_utc_iso(),
        )

    def _update_card_measurements(
        self,
        run_id: str,
        stage_order: int,
        skip_reason: Optional[str] = None,
        error_msg: Optional[str] = None,
    ) -> Optional[str]:
        """Extract empirical telemetry for completed, skipped, or failed stage, update card, and return summary."""
        box = self.stage_boxes.get(stage_order)
        if not isinstance(box, DiagnosticStageCard):
            return None

        metric_summary: Optional[str] = None

        try:
            if stage_order == 1:
                data = self._extractor.extract_stage1(run_id)
                if error_msg and not data.error_message:
                    data.error_message = error_msg
                    data.status = "FAILED"
                box.set_measurements(data)
                if data.gateway_ip:
                    metric_summary = f"Gateway: {data.gateway_ip}"
                elif data.error_message:
                    metric_summary = f"Discovery: {data.error_message}"
                else:
                    metric_summary = f"Gateway: {data.status}"

            elif stage_order == 2:
                data = self._extractor.extract_stage2(run_id)
                if error_msg and not data.error_message:
                    data.error_message = error_msg
                    data.status = "FAILED"
                box.set_measurements(data)
                if data.packet_loss_pct is not None and data.median_rtt_ms is not None:
                    metric_summary = f"Loss: {data.packet_loss_pct:.1f}% | RTT: {data.median_rtt_ms:.1f}ms"
                elif data.median_rtt_ms is not None:
                    metric_summary = f"RTT: {data.median_rtt_ms:.1f}ms"
                elif data.probe_count_attempted > 0 and data.probe_count_successful == 0:
                    metric_summary = "Gateway: Unreachable"
                else:
                    metric_summary = f"Gateway: {data.status}"

            elif stage_order == 3:
                data = self._extractor.extract_stage3(run_id)
                if error_msg and not data.error_message:
                    data.error_message = error_msg
                    data.status = "FAILED"
                box.set_measurements(data)
                if data.total_count > 0:
                    rtt_str = f" | RTT: {data.overall_median_rtt_ms:.1f}ms" if data.overall_median_rtt_ms is not None else ""
                    metric_summary = f"Reachable: {data.reachable_count}/{data.total_count}{rtt_str}"
                else:
                    metric_summary = f"Targets: {len(data.targets)} probed"

            elif stage_order == 4:
                data = self._extractor.extract_stage4(run_id)
                if error_msg and not data.error_message:
                    data.error_message = error_msg
                    data.status = "FAILED"
                box.set_measurements(data)
                if data.response_time_ms is not None:
                    rcode_str = f" ({data.rcode_name})" if data.rcode_name else ""
                    metric_summary = f"DNS: {data.response_time_ms:.1f}ms{rcode_str}"
                elif data.status == "FAILED" or error_msg:
                    metric_summary = "DNS: Failed"
                else:
                    metric_summary = f"DNS: {data.status}"

            elif stage_order == 5:
                if skip_reason:
                    data = Stage5Data(is_skipped=True, skip_reason=skip_reason, status="SKIPPED")
                    metric_summary = f"Throughput: Skipped ({skip_reason})"
                else:
                    data = self._extractor.extract_stage5(run_id)
                    if error_msg and not data.error_message:
                        data.error_message = error_msg
                        data.status = "FAILED"
                    if data.throughput_mbps is not None:
                        delta_str = f" | +{data.delta_rtt_median_ms:.0f}ms loaded" if data.delta_rtt_median_ms is not None else ""
                        metric_summary = f"Speed: {data.throughput_mbps:.1f} Mbps{delta_str}"
                    elif data.is_skipped:
                        metric_summary = f"Throughput: Skipped ({data.skip_reason})"
                    else:
                        metric_summary = f"Throughput: {data.status}"
                box.set_measurements(data)
        except Exception:
            # Safe non-blocking degradation: do not interrupt GUI flow
            pass

        return metric_summary

    def _on_diagnosis_completed(self, run_id: str, findings_count: int) -> None:
        if self._active_run_id and run_id != self._active_run_id:
            return

        self._last_run_id = run_id
        self.run_id_badge.set_run_id(run_id)
        self.run_id_badge.setVisible(True)
        self.btn_view_results.setVisible(True)
        self.lbl_session_status.setText(
            f"Run {run_id} Completed | {findings_count} Explainable Findings Generated"
        )
        self.lbl_session_status.setStyleSheet(
            f"color: {ThemeTokens.COLOR_SUCCESS}; font-size: 12px; margin-top: 6px; font-weight: 700;"
        )

        self.activity_feed_widget.add_activity(
            category=ActivityCategory.DIAGNOSTIC_COMPLETED,
            message=f"Diagnosis evaluation complete: {findings_count} explainable findings identified.",
            measurement=f"{findings_count} findings",
            is_terminal=True,
            timestamp=now_utc_iso(),
        )

    def _on_run_finished(self, run_id: str, status: str, error_message: str) -> None:
        if self._active_run_id and run_id != self._active_run_id:
            return

        self._last_run_id = run_id
        self.run_id_badge.set_run_id(run_id)
        self.run_id_badge.setVisible(True)
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)

        clean_status = (status or "").upper()
        if clean_status == "COMPLETED":
            if self._skipped_stages > 0 or self._inconclusive_stages > 0:
                final_gauge_state = GaugeState.COMPLETED_WITH_WARNINGS
                final_feed_state = "COMPLETED_WITH_WARNINGS"
            else:
                final_gauge_state = GaugeState.COMPLETED
                final_feed_state = "COMPLETED"
            self._confirmed_progress = 100.0
            self.gauge_widget.set_progress(100.0)
            self.btn_view_results.setVisible(True)
        elif clean_status == "CANCELLED":
            final_gauge_state = GaugeState.CANCELLED
            final_feed_state = "CANCELLED"
            self.lbl_session_status.setText(f"Run {run_id} Cancelled by User.")
            self.lbl_session_status.setStyleSheet(
                f"color: {ThemeTokens.COLOR_WARNING}; font-size: 12px; margin-top: 6px; font-weight: 600;"
            )
            self.activity_feed_widget.add_activity(
                category=ActivityCategory.DIAGNOSTIC_CANCELLED,
                message=f"Diagnostic session {run_id} cancelled by user.",
                is_terminal=True,
                timestamp=now_utc_iso(),
            )
        elif clean_status in ("FAILED", "PARTIAL") or self._failed_stages > 0:
            final_gauge_state = GaugeState.FAILED
            final_feed_state = "FAILED"
            self.lbl_session_status.setText(f"Run {run_id} {clean_status}: {error_message}")
            self.lbl_session_status.setStyleSheet(
                f"color: {ThemeTokens.COLOR_ERROR}; font-size: 12px; margin-top: 6px; font-weight: 600;"
            )
            self.activity_feed_widget.add_activity(
                category=ActivityCategory.DIAGNOSTIC_ERROR,
                message=f"Diagnostic session {run_id} {clean_status.lower()}: {error_message or 'Failure occurred'}",
                is_terminal=True,
                timestamp=now_utc_iso(),
            )
        else:
            final_gauge_state = GaugeState.IDLE
            final_feed_state = "IDLE"

        self.gauge_widget.set_status(final_gauge_state)
        self.gauge_widget.set_active_stage("")
        self.activity_feed_widget.set_execution_state(final_feed_state, is_active=False)

        if self._confirmed_progress > 0:
            self.btn_view_results.setVisible(True)

    def _on_error(self, error_message: str) -> None:
        self.lbl_session_status.setText(f"Diagnostic Error: {error_message}")
        self.lbl_session_status.setStyleSheet(
            f"color: {ThemeTokens.COLOR_ERROR}; font-size: 12px; margin-top: 6px; font-weight: 600;"
        )
        self.activity_feed_widget.add_activity(
            category=ActivityCategory.DIAGNOSTIC_ERROR,
            message=f"Diagnostic error: {error_message}",
            is_terminal=True,
            timestamp=now_utc_iso(),
        )

