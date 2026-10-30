"""Comprehensive integration tests for the Interactive Diagnostic Pipeline Dashboard.

Verifies the integrated functionality of:
- RadialGaugeWidget (progress, boundaries, state transitions, metric chip, cancellation halt).
- DiagnosticActivityFeedWidget (chronological timeline, event mapping, critical retention, collapsing).
- DiagnosticStageCards (real measurement presentation, stage status, honest missing values).
- DiagnosisPage integration with DiagnosticService signals (run started, progress, completed, skipped, failed, cancelled).
- New-run resets and stale-run protection across all dashboard sub-components.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List
import unittest.mock as mock

# Headless Qt platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.services import DesktopServices
from ticnet.desktop.widgets.diagnostic_activity_widget import (
    ActivityCategory,
    ActivityEvent,
    DiagnosticActivityFeedWidget,
)
from ticnet.desktop.widgets.radial_gauge_widget import GaugeState, RadialGaugeWidget
from ticnet.desktop.widgets.stage_card_widget import (
    DiagnosticStageCard,
    Stage1Data,
    Stage2Data,
    Stage3Data,
    Stage4Data,
    Stage5Data,
    StageCardState,
)
from ticnet.models.entities import Observation, Summary, Target, TestRun, TestStage, new_id, now_utc_iso
from ticnet.models.enums import MeasurementStatus, RunMode, RunStatus, StageStatus
from ticnet.persistence.database import DatabaseManager


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Ensure persistent QApplication exists for headless Qt execution."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def desktop_services(tmp_path: Path) -> DesktopServices:
    """Provide a fresh DesktopServices suite with an initialized SQLite database."""
    db_file = tmp_path / "test_dashboard_integration.sqlite3"
    return DesktopServices.create_default(db_path=db_file, run_migrations=True)


@pytest.fixture
def diagnosis_page(qapp: QApplication, desktop_services: DesktopServices) -> DiagnosisPage:
    """Create a DiagnosisPage connected to live diagnostic services."""
    page = DiagnosisPage(diagnostic_service=desktop_services.diagnostic)
    yield page
    page.disconnect_service()


# =============================================================================
# 1. Initial State Verification
# =============================================================================


def test_dashboard_initial_idle_state(diagnosis_page: DiagnosisPage) -> None:
    """Verify initial idle state of all integrated dashboard components."""
    # Run Controls
    assert diagnosis_page.btn_start.isEnabled() is True
    assert diagnosis_page.btn_cancel.isEnabled() is False
    assert diagnosis_page.btn_view_results.isHidden() is True
    assert "Idle" in diagnosis_page.lbl_session_status.text()
    assert diagnosis_page.run_id_badge.isHidden() is True

    # Radial Gauge
    gauge = diagnosis_page.gauge_widget
    assert isinstance(gauge, RadialGaugeWidget)
    assert gauge.progress == 0.0
    assert gauge.status == GaugeState.IDLE
    assert gauge.active_stage == ""
    assert gauge.current_metric is None
    assert gauge.is_animating is False

    # Activity Feed
    feed = diagnosis_page.activity_feed_widget
    assert isinstance(feed, DiagnosticActivityFeedWidget)
    assert len(feed.events) == 0
    assert feed.lbl_exec_indicator.text() == "● IDLE"
    assert feed.lbl_count_badge.text() == "0 events"

    # Stage Cards
    assert len(diagnosis_page.stage_boxes) == 5
    for order in range(1, 6):
        card = diagnosis_page.stage_boxes[order]
        assert isinstance(card, DiagnosticStageCard)
        assert card.current_state == StageCardState.IDLE
        assert card.lbl_badge.text() == "IDLE"

    # Gauge and Dashboard verified; no intrusive Engine Idle banner
    assert diagnosis_page.gauge_widget is not None
    assert not any("Diagnostic Engine Idle" in lbl.text() for lbl in diagnosis_page.findChildren(QLabel))


# =============================================================================
# 2. Complete Successful 5-Stage Diagnostic Run
# =============================================================================


def test_dashboard_complete_successful_run(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify end-to-end signal streaming for a successful 5-stage pipeline."""
    run_id = "run-dash-success-001"
    db = desktop_services.db

    # Setup database records for telemetry extraction
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO test_runs (run_id, start_time_utc, status, run_mode, app_version, config_version, gateway_ip, interface_name, interface_type)
            VALUES (?, ?, 'RUNNING', 'OPERATIONAL', '1.0.0', '1.0.0', '192.168.1.1', 'Ethernet0', 'Ethernet');
            """,
            (run_id, now_utc_iso()),
        )
        conn.execute(
            "INSERT INTO targets (target_id, target_type, hostname, ip_address, provider_label, is_active) VALUES ('gw', 'GATEWAY', 'gateway', '192.168.1.1', 'Gateway', 1)"
        )
        conn.execute(
            "INSERT INTO targets (target_id, target_type, hostname, ip_address, provider_label, is_active) VALUES ('ext1', 'EXTERNAL_IP', 'cloudflare.com', '1.1.1.1', 'Cloudflare', 1)"
        )
        conn.execute(
            "INSERT INTO targets (target_id, target_type, hostname, ip_address, provider_label, is_active) VALUES ('dns1', 'DNS_RESOLVER', 'dns', '192.168.1.1', 'DNS Resolver', 1)"
        )

        # Stage 2 Summaries
        conn.execute(
            "INSERT INTO summaries (summary_id, run_id, target_id, metric_name, statistic_name, value_numeric, unit, sample_count_total, sample_count_valid, calculation_method, calculation_version) "
            "VALUES ('sum-2a', ?, 'gw', 'packet_loss', 'raw', 0.0, 'percent', 10, 10, 'raw_loss', '1.0.0');",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO summaries (summary_id, run_id, target_id, metric_name, statistic_name, value_numeric, unit, sample_count_total, sample_count_valid, calculation_method, calculation_version) "
            "VALUES ('sum-2b', ?, 'gw', 'round_trip_time', 'median', 1.85, 'ms', 10, 10, 'median_rtt', '1.0.0');",
            (run_id,),
        )

        # Stage 3 Summaries
        conn.execute(
            "INSERT INTO summaries (summary_id, run_id, target_id, metric_name, statistic_name, value_numeric, unit, sample_count_total, sample_count_valid, calculation_method, calculation_version) "
            "VALUES ('sum-3a', ?, 'ext1', 'packet_loss', 'raw', 0.0, 'percent', 10, 10, 'raw_loss', '1.0.0');",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO summaries (summary_id, run_id, target_id, metric_name, statistic_name, value_numeric, unit, sample_count_total, sample_count_valid, calculation_method, calculation_version) "
            "VALUES ('sum-3b', ?, 'ext1', 'round_trip_time', 'median', 12.4, 'ms', 10, 10, 'median_rtt', '1.0.0');",
            (run_id,),
        )

        # Stage 4 Observations
        conn.execute(
            "INSERT INTO observations (observation_id, run_id, sample_index, timestamp_utc, duration_ms, unit, target_id, metric_name, value_numeric, status, method_metadata) "
            "VALUES ('obs-4', ?, 1, ?, 14.2, 'ms', 'dns1', 'dns_query_time', 14.2, 'SUCCESS', ?);",
            (
                run_id,
                now_utc_iso(),
                json.dumps({
                    "domain": "example.com",
                    "resolver_ip": "192.168.1.1",
                    "rcode_name": "NOERROR",
                    "resolution_status": "RESOLVED",
                    "has_answer": True,
                }),
            ),
        )

        # Stage 5 Observations
        conn.execute(
            "INSERT INTO observations (observation_id, run_id, sample_index, timestamp_utc, duration_ms, unit, target_id, metric_name, value_numeric, status, method_metadata) "
            "VALUES ('obs-5', ?, 1, ?, 1000.0, 'ms', NULL, 'download_throughput', 95.5, 'SUCCESS', ?);",
            (
                run_id,
                now_utc_iso(),
                json.dumps({
                    "load_throughput_mbps": 95.5,
                    "baseline_median_rtt_ms": 12.0,
                    "loaded_median_rtt_ms": 30.0,
                    "delta_rtt_median_ms": 18.0,
                    "bufferbloat_ratio": 2.5,
                }),
            ),
        )
        conn.commit()

    # 1. Start Run
    desktop_services.diagnostic.sig_run_started.emit(run_id)
    assert diagnosis_page.btn_start.isEnabled() is False
    assert diagnosis_page.btn_cancel.isEnabled() is True
    assert diagnosis_page.gauge_widget.status == GaugeState.RUNNING
    assert diagnosis_page.gauge_widget.progress == 0.0
    assert diagnosis_page.activity_feed_widget.lbl_exec_indicator.text() == "● RUNNING"
    assert len(diagnosis_page.activity_feed_widget.events) == 1
    assert diagnosis_page.activity_feed_widget.events[0].category == ActivityCategory.DIAGNOSTIC_STARTED

    # 2. Stage 1: Started -> Completed
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 1, "Gateway Discovery")
    assert diagnosis_page.stage_boxes[1].current_state == StageCardState.RUNNING
    assert "Gateway Discovery" in diagnosis_page.gauge_widget.active_stage

    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 1, "Gateway Discovery", "SUCCESS")
    assert diagnosis_page.stage_boxes[1].current_state == StageCardState.SUCCESS
    assert diagnosis_page.gauge_widget.progress == 20.0
    assert "192.168.1.1" in str(diagnosis_page.gauge_widget.current_metric)

    # 3. Stage 2: Gateway Reachability
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 2, "Gateway Reachability")
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 2, "Gateway Reachability", "SUCCESS")
    assert diagnosis_page.stage_boxes[2].current_state == StageCardState.SUCCESS
    assert diagnosis_page.gauge_widget.progress == 40.0
    assert "Loss: 0.0%" in str(diagnosis_page.gauge_widget.current_metric)

    # 4. Stage 3: Multi-Target External Probing
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 3, "External Probing")
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 3, "External Probing", "SUCCESS")
    assert diagnosis_page.stage_boxes[3].current_state == StageCardState.SUCCESS
    assert diagnosis_page.gauge_widget.progress == 60.0

    # 5. Stage 4: DNS Query Timing
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 4, "DNS Diagnostics")
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 4, "DNS Diagnostics", "SUCCESS")
    assert diagnosis_page.stage_boxes[4].current_state == StageCardState.SUCCESS
    assert diagnosis_page.gauge_widget.progress == 80.0
    assert "DNS: 14.2ms" in str(diagnosis_page.gauge_widget.current_metric)

    # 6. Stage 5: Throughput & Loaded Latency
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 5, "Throughput & Loaded Latency")
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 5, "Throughput & Loaded Latency", "SUCCESS")
    assert diagnosis_page.stage_boxes[5].current_state == StageCardState.SUCCESS
    assert diagnosis_page.gauge_widget.progress == 100.0
    assert "95.5 Mbps" in str(diagnosis_page.gauge_widget.current_metric)

    # 7. Diagnosis Evaluation & Run Finished
    desktop_services.diagnostic.sig_diagnosis_completed.emit(run_id, 3)
    assert diagnosis_page.btn_view_results.isHidden() is False
    assert "3 Explainable Findings" in diagnosis_page.lbl_session_status.text()

    desktop_services.diagnostic.sig_run_finished.emit(run_id, "COMPLETED", "Workflow complete")
    assert diagnosis_page.btn_start.isEnabled() is True
    assert diagnosis_page.btn_cancel.isEnabled() is False
    assert diagnosis_page.gauge_widget.status == GaugeState.COMPLETED
    assert diagnosis_page.gauge_widget.progress == 100.0
    assert diagnosis_page.activity_feed_widget.lbl_exec_indicator.text() == "● COMPLETED"

    # Verify chronological event ordering in feed
    categories = [ev.category for ev in diagnosis_page.activity_feed_widget.events]
    assert categories[0] == ActivityCategory.DIAGNOSTIC_STARTED
    assert ActivityCategory.STAGE_STARTED in categories
    assert ActivityCategory.STAGE_COMPLETED in categories
    assert ActivityCategory.DIAGNOSTIC_COMPLETED in categories


# =============================================================================
# 3. Diagnostic Run with Skipped Stage & Completed With Warnings
# =============================================================================


def test_dashboard_run_with_skipped_stage_and_warnings(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify that skipped stages advance boundary but remain visibly marked SKIPPED."""
    run_id = "run-dash-warning-002"

    desktop_services.diagnostic.sig_run_started.emit(run_id)

    # Stages 1 to 4 succeed
    for order in range(1, 5):
        desktop_services.diagnostic.sig_stage_started.emit(run_id, order, f"Stage {order}")
        desktop_services.diagnostic.sig_stage_completed.emit(run_id, order, f"Stage {order}", "SUCCESS")

    assert diagnosis_page.gauge_widget.progress == 80.0

    # Stage 5 skipped (e.g. user consent not given or gateway unreachable per SRS-F-008)
    desktop_services.diagnostic.sig_stage_skipped.emit(
        run_id, 5, "Throughput", "High gateway loss prevents safe loaded testing"
    )

    # Verified: Stage 5 card is visibly marked SKIPPED (NOT SUCCESS)
    card5 = diagnosis_page.stage_boxes[5]
    assert card5.current_state == StageCardState.SKIPPED
    assert card5.lbl_badge.text() == "SKIPPED"
    assert "Skipped" in card5.lbl_status.text()

    # Progress advances to 100% because all 5 boundaries were evaluated
    assert diagnosis_page.gauge_widget.progress == 100.0
    assert "Skipped:" in str(diagnosis_page.gauge_widget.current_metric)

    # Activity feed logs STAGE_SKIPPED
    skipped_events = [
        ev for ev in diagnosis_page.activity_feed_widget.events
        if ev.category == ActivityCategory.STAGE_SKIPPED
    ]
    assert len(skipped_events) == 1
    assert "High gateway loss" in skipped_events[0].message

    # Workflow finishes with warnings
    desktop_services.diagnostic.sig_run_finished.emit(run_id, "COMPLETED", "Run complete with skipped stages")
    assert diagnosis_page.gauge_widget.status == GaugeState.COMPLETED_WITH_WARNINGS
    assert diagnosis_page.activity_feed_widget.lbl_exec_indicator.text() == "● COMPLETED_WITH_WARNINGS"


# =============================================================================
# 4. Individual Stage Failure & Progress Preservation
# =============================================================================


def test_dashboard_stage_failure_halts_progress_and_preserves_confirmed_boundary(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify failed stage halts progress at confirmed boundary and never reaches 100%."""
    run_id = "run-dash-failure-003"

    desktop_services.diagnostic.sig_run_started.emit(run_id)

    # Stage 1 succeeds (progress 20%)
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 1, "Gateway Discovery")
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 1, "Gateway Discovery", "SUCCESS")
    assert diagnosis_page.gauge_widget.progress == 20.0

    # Stage 2 fails
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 2, "Gateway Reachability")
    desktop_services.diagnostic.sig_stage_failed.emit(
        run_id, 2, "Gateway Reachability", "100% packet loss to default gateway"
    )

    # Progress remains at 20.0% (does NOT advance to 40% or 100%)
    assert diagnosis_page.gauge_widget.progress == 20.0
    assert diagnosis_page.gauge_widget.status == GaugeState.FAILED
    assert "Failed:" in str(diagnosis_page.gauge_widget.current_metric)

    # Stage 2 card marked FAILED
    card2 = diagnosis_page.stage_boxes[2]
    assert card2.current_state == StageCardState.FAILED
    assert card2.lbl_badge.text() == "FAILED"

    # Terminal run finish
    desktop_services.diagnostic.sig_run_finished.emit(run_id, "FAILED", "Gateway unreachable")
    assert diagnosis_page.gauge_widget.status == GaugeState.FAILED
    # Progress must strictly remain at 20.0%
    assert diagnosis_page.gauge_widget.progress == 20.0
    assert diagnosis_page.activity_feed_widget.lbl_exec_indicator.text() == "● FAILED"

    # Terminal failure event retained in feed
    failed_events = [
        ev for ev in diagnosis_page.activity_feed_widget.events
        if ev.category in (ActivityCategory.STAGE_FAILED, ActivityCategory.DIAGNOSTIC_ERROR)
    ]
    assert len(failed_events) >= 1


# =============================================================================
# 5. User Cancellation Halts Active Animation & Preserves Progress
# =============================================================================


def test_dashboard_user_cancellation_during_run(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify cancellation stops gauge animation at confirmed boundary and logs cancellation."""
    run_id = "run-dash-cancel-004"
    desktop_services.diagnostic.cancel_diagnosis = mock.MagicMock()

    desktop_services.diagnostic.sig_run_started.emit(run_id)

    # Stage 1 succeeds (20%)
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 1, "Gateway Discovery")
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 1, "Gateway Discovery", "SUCCESS")
    assert diagnosis_page.gauge_widget.progress == 20.0

    # Stage 2 starts
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 2, "Gateway Reachability")
    assert diagnosis_page.btn_cancel.isEnabled() is True

    # User clicks Cancel
    QTest.mouseClick(diagnosis_page.btn_cancel, Qt.MouseButton.LeftButton)
    desktop_services.diagnostic.cancel_diagnosis.assert_called_once()

    # Backend emits RUN_CANCELLED
    desktop_services.diagnostic.sig_run_finished.emit(run_id, "CANCELLED", "User aborted test")

    # Gauge stops animation, stays at 20.0%, status CANCELLED
    assert diagnosis_page.gauge_widget.is_animating is False
    assert diagnosis_page.gauge_widget.progress == 20.0
    assert diagnosis_page.gauge_widget.status == GaugeState.CANCELLED

    # Controls restored
    assert diagnosis_page.btn_start.isEnabled() is True
    assert diagnosis_page.btn_cancel.isEnabled() is False

    # Feed logs DIAGNOSTIC_CANCELLED
    cancel_events = [
        ev for ev in diagnosis_page.activity_feed_widget.events
        if ev.category == ActivityCategory.DIAGNOSTIC_CANCELLED
    ]
    assert len(cancel_events) == 1
    assert diagnosis_page.activity_feed_widget.lbl_exec_indicator.text() == "● CANCELLED"


# =============================================================================
# 6. Inconclusive Stage Handling
# =============================================================================


def test_dashboard_inconclusive_stage_handling(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify inconclusive stage is distinguished from success and logged appropriately."""
    run_id = "run-dash-inconclusive-005"

    desktop_services.diagnostic.sig_run_started.emit(run_id)

    # Stages 1 to 3 succeed
    for order in range(1, 4):
        desktop_services.diagnostic.sig_stage_completed.emit(run_id, order, f"Stage {order}", "SUCCESS")

    # Stage 4 completed with INCONCLUSIVE
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 4, "DNS Diagnostics", "INCONCLUSIVE")

    card4 = diagnosis_page.stage_boxes[4]
    assert card4.current_state == StageCardState.INCONCLUSIVE
    assert card4.lbl_badge.text() == "INCONCLUSIVE"

    # Activity feed logs INCONCLUSIVE event
    inconclusive_events = [
        ev for ev in diagnosis_page.activity_feed_widget.events
        if ev.category == ActivityCategory.INCONCLUSIVE
    ]
    assert len(inconclusive_events) == 1

    # Stage 5 completed
    desktop_services.diagnostic.sig_stage_completed.emit(run_id, 5, "Throughput", "SUCCESS")

    # Finish run -> final state is COMPLETED_WITH_WARNINGS
    desktop_services.diagnostic.sig_run_finished.emit(run_id, "COMPLETED", "Workflow complete")
    assert diagnosis_page.gauge_widget.status == GaugeState.COMPLETED_WITH_WARNINGS


# =============================================================================
# 7. Stale Run Protection Across All Dashboard Sub-components
# =============================================================================


def test_dashboard_stale_run_events_are_rejected(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify events from mismatched or previous run IDs are rejected by all components."""
    active_run = "run-dash-active-006"
    stale_run = "run-dash-stale-999"

    # Start Active Run
    desktop_services.diagnostic.sig_run_started.emit(active_run)
    desktop_services.diagnostic.sig_stage_started.emit(active_run, 1, "Gateway Discovery")

    initial_event_count = len(diagnosis_page.activity_feed_widget.events)
    assert diagnosis_page.gauge_widget.progress == 0.0

    # Stale run events arrive
    desktop_services.diagnostic.sig_stage_completed.emit(stale_run, 1, "Gateway Discovery", "SUCCESS")
    desktop_services.diagnostic.sig_stage_progress.emit(stale_run, 1, "Gateway Discovery", "Late ping")
    desktop_services.diagnostic.sig_stage_skipped.emit(stale_run, 5, "Throughput", "Stale skip")
    desktop_services.diagnostic.sig_stage_failed.emit(stale_run, 2, "Gateway Reachability", "Stale fail")
    desktop_services.diagnostic.sig_diagnosis_completed.emit(stale_run, 5)
    desktop_services.diagnostic.sig_run_finished.emit(stale_run, "COMPLETED", "Stale finish")

    # Verified: None of the stale events affected active run dashboard
    assert len(diagnosis_page.activity_feed_widget.events) == initial_event_count
    assert diagnosis_page.gauge_widget.progress == 0.0
    assert diagnosis_page.stage_boxes[1].current_state == StageCardState.RUNNING


# =============================================================================
# 8. New Run Resets All Sub-components
# =============================================================================


def test_dashboard_new_run_resets_all_components(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify initiating a new diagnostic run completely resets gauge, feed, and cards."""
    # 1. First run finishes
    desktop_services.diagnostic.sig_run_started.emit("run-first-001")
    for order in range(1, 6):
        desktop_services.diagnostic.sig_stage_completed.emit("run-first-001", order, f"Stage {order}", "SUCCESS")
    desktop_services.diagnostic.sig_run_finished.emit("run-first-001", "COMPLETED", "Done")

    assert diagnosis_page.gauge_widget.progress == 100.0
    assert len(diagnosis_page.activity_feed_widget.events) > 5

    # 2. Second run started
    desktop_services.diagnostic.sig_run_started.emit("run-second-002")

    # Gauge reset
    assert diagnosis_page.gauge_widget.progress == 0.0
    assert diagnosis_page.gauge_widget.status == GaugeState.RUNNING
    assert diagnosis_page.gauge_widget.active_stage == ""
    assert diagnosis_page.gauge_widget.current_metric is None

    # Activity feed reset with only the new run start event
    assert len(diagnosis_page.activity_feed_widget.events) == 1
    assert "run-second-002" in diagnosis_page.activity_feed_widget.events[0].message

    # All cards reset to PENDING
    for order in range(1, 6):
        card = diagnosis_page.stage_boxes[order]
        assert card.current_state == StageCardState.PENDING
        assert card.lbl_badge.text() == "PENDING"


# =============================================================================
# 9. Intra-stage Progress & Activity Deduplication
# =============================================================================


def test_dashboard_intra_stage_progress_and_deduplication(
    diagnosis_page: DiagnosisPage, desktop_services: DesktopServices
) -> None:
    """Verify consecutive in-flight progress messages are collapsed in the activity feed."""
    run_id = "run-progress-dedup"
    desktop_services.diagnostic.sig_run_started.emit(run_id)
    desktop_services.diagnostic.sig_stage_started.emit(run_id, 2, "Gateway Reachability")

    # Send 5 progress pings
    for i in range(1, 6):
        desktop_services.diagnostic.sig_stage_progress.emit(
            run_id, 2, "Gateway Reachability", f"Probing packet {i}/5..."
        )

    # Activity feed should collapse in-flight progress into 1 updating widget
    # (1 DIAGNOSTIC_STARTED, 1 STAGE_STARTED, 1 STAGE_PROGRESS collapsed)
    assert len(diagnosis_page.activity_feed_widget.events) == 3
    last_event = diagnosis_page.activity_feed_widget.events[-1]
    assert last_event.category == ActivityCategory.STAGE_PROGRESS
    assert "packet 5/5" in last_event.message


# =============================================================================
# 10. Responsive Viewport Layout Integrity
# =============================================================================


@pytest.mark.parametrize("width,height", [(800, 600), (1024, 768), (1280, 800)])
def test_dashboard_responsive_rendering(
    qapp: QApplication, desktop_services: DesktopServices, width: int, height: int
) -> None:
    """Verify entire DiagnosisPage with gauge, feed, and stage cards renders without layout issues."""
    page = DiagnosisPage(diagnostic_service=desktop_services.diagnostic)
    page.resize(width, height)
    page.show()

    # Populate some live data
    page.gauge_widget.set_progress(60.0)
    page.gauge_widget.set_status(GaugeState.RUNNING)
    page.gauge_widget.set_active_stage("Stage 3: External Multi-Target Probing")
    page.gauge_widget.set_current_metric("Reachable: 3/3 | RTT: 14.5ms")

    page.activity_feed_widget.add_activity(
        category=ActivityCategory.DIAGNOSTIC_STARTED, message="Diagnostic session initialized."
    )
    page.activity_feed_widget.add_activity(
        category=ActivityCategory.STAGE_COMPLETED,
        message="Stage 1 completed: Gateway discovered",
        measurement="Gateway: 192.168.1.1",
    )

    image = QImage(QSize(width, height), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    page.render(image, renderFlags=page.RenderFlag.DrawChildren)

    assert not image.isNull()
    assert image.width() == width
    assert image.height() == height

    page.disconnect_service()
