"""Comprehensive tests for dynamic diagnostic stage cards and measurement presentation (Phase 5).

Covers:
1. Initial state (Pending / Idle).
2. Each stage's real measurement mapping (Stage 1..5).
3. All seven card states (IDLE, PENDING, RUNNING, SUCCESS, FAILED, SKIPPED, INCONCLUSIVE).
4. Missing measurements (honest "N/A (Unmeasured)", not zero).
5. Zero-valued measurements (0.0% loss vs missing loss).
6. Partial external target results (e.g. 2 of 3 responding).
7. DNS error and inconclusive cases.
8. Throughput unavailable / skipped per SRS-F-008.
9. New run reset behavior.
10. Prevention of stale updates from previous run IDs.
11. Long measurement text wrapping without clipping.
12. Responsive layout and offscreen rendering.
13. Existing public attribute compatibility (stage_boxes[1..5], stage_status_labels[1..5]).
"""

from __future__ import annotations

import json
import os
import unittest.mock as mock
import pytest

# Ensure offscreen Qt platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QWidget

from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.stage_card_widget import (
    DiagnosticStageCard,
    Stage1Data,
    Stage2Data,
    Stage3Data,
    Stage3TargetResult,
    Stage4Data,
    Stage4DNSResult,
    Stage5Data,
    StageCardState,
    StageMeasurementExtractor,
)
from ticnet.models.entities import Observation, Summary, Target, TestRun, TestStage, new_id, now_utc_iso
from ticnet.models.enums import MeasurementStatus, StageStatus, TargetType
from ticnet.persistence.database import DatabaseManager


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provide a persistent QApplication for desktop UI testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_db(tmp_path) -> DatabaseManager:
    """Provide a migrated SQLite test database."""
    db_file = tmp_path / "test_stage_cards.sqlite3"
    db = DatabaseManager(db_path=db_file)
    db.run_migrations()
    return db


# =============================================================================
# 1. Initial State & Public Attribute Compatibility
# =============================================================================


def test_stage_cards_initial_state_and_attributes(qapp: QApplication) -> None:
    """Verify all 5 stage cards initialize with correct attributes and idle state."""
    page = DiagnosisPage()

    # Section 3: Compatibility with public attributes
    assert len(page.stage_boxes) == 5
    assert len(page.stage_status_labels) == 5

    for order in range(1, 6):
        box = page.stage_boxes[order]
        lbl = page.stage_status_labels[order]

        assert isinstance(box, QFrame)
        assert isinstance(box, DiagnosticStageCard)
        assert isinstance(lbl, QLabel)
        assert box.lbl_status is lbl

        # Initial card state is IDLE
        assert box.current_state == StageCardState.IDLE
        assert box.lbl_badge.text() == "IDLE"
        assert "Idle" in lbl.text()

        # Telemetry panel contains the initial stage description
        assert box.telemetry_layout.count() >= 1


# =============================================================================
# 2. All 7 Card States Visual and Textual Transitions
# =============================================================================


@pytest.mark.parametrize(
    "state,expected_badge,status_snippet",
    [
        (StageCardState.IDLE, "IDLE", "Idle"),
        (StageCardState.PENDING, "PENDING", "Pending"),
        (StageCardState.RUNNING, "RUNNING", "In Progress"),
        (StageCardState.SUCCESS, "SUCCESS", "Completed (SUCCESS)"),
        (StageCardState.FAILED, "FAILED", "Failed (Network Unreachable)"),
        (StageCardState.SKIPPED, "SKIPPED", "Skipped (Policy)"),
        (StageCardState.INCONCLUSIVE, "INCONCLUSIVE", "Inconclusive (High Variance)"),
    ],
)
def test_stage_card_all_seven_states(
    qapp: QApplication,
    state: StageCardState,
    expected_badge: str,
    status_snippet: str,
) -> None:
    """Verify distinct styling and status updates across all 7 card states."""
    card = DiagnosticStageCard(
        order=1,
        stage_num="Stage 1",
        name="Gateway Discovery",
        desc="Locates default gateway",
    )

    card.set_stage_state(state, f"Status: {status_snippet}")
    assert card.current_state == state
    assert card.lbl_badge.text() == expected_badge
    assert status_snippet in card.lbl_status.text()

    # Verify stylesheet includes border definitions
    stylesheet = card.styleSheet()
    assert "#stageBox_1" in stylesheet
    assert "#stageTelemetryFrame" in stylesheet

    # Ensure SKIPPED and INCONCLUSIVE never inherit SUCCESS styling
    if state == StageCardState.SKIPPED:
        assert ThemeTokens.COLOR_SUCCESS not in stylesheet
        assert ThemeTokens.COLOR_WARNING in stylesheet
    elif state == StageCardState.INCONCLUSIVE:
        assert ThemeTokens.COLOR_SUCCESS not in stylesheet
        assert ThemeTokens.COLOR_INCONCLUSIVE in stylesheet


# =============================================================================
# 3. Stage 1: Default Gateway Discovery Telemetry
# =============================================================================


def test_stage1_gateway_discovery_success_presentation(qapp: QApplication) -> None:
    """Verify Stage 1 correctly renders discovered gateway IP and interface."""
    card = DiagnosticStageCard(1, "Stage 1", "Gateway Discovery", "Desc")

    data = Stage1Data(
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi",
        interface_type="IPv4",
        discovery_source="Routing Table (Kernel IP)",
        status="SUCCESS",
    )
    card.set_measurements(data)

    # Inspect rendered labels inside telemetry
    labels = [w.text() for w in card.findChildren(QLabel)]
    assert any("Gateway IP:" in t for t in labels)
    assert any("192.168.1.1" in t for t in labels)
    assert any("Interface:" in t for t in labels)
    assert any("Wi-Fi (IPv4)" in t for t in labels)
    assert any("Discovery Source:" in t for t in labels)


def test_stage1_gateway_discovery_failure_presentation(qapp: QApplication) -> None:
    """Verify Stage 1 displays real failure explanation when gateway is not discovered."""
    card = DiagnosticStageCard(1, "Stage 1", "Gateway Discovery", "Desc")

    data = Stage1Data(
        gateway_ip=None,
        status="FAILED",
        error_message="No active default IPv4 route found in routing table",
    )
    card.set_measurements(data)

    labels = [w.text() for w in card.findChildren(QLabel)]
    assert any("Discovery Result:" in t for t in labels)
    assert any("No active default IPv4 route found in routing table" in t for t in labels)


# =============================================================================
# 4. Stage 2: Gateway Reachability, RTT, and Invariants (0.0% vs None)
# =============================================================================


def test_stage2_gateway_reachability_measurements(qapp: QApplication) -> None:
    """Verify Stage 2 renders packet loss, median RTT, min/max, P95, and MACD jitter."""
    card = DiagnosticStageCard(2, "Stage 2", "Gateway Reachability", "Desc")

    data = Stage2Data(
        packet_loss_pct=0.0,
        median_rtt_ms=1.45,
        min_rtt_ms=1.12,
        avg_rtt_ms=1.52,
        max_rtt_ms=2.31,
        p95_rtt_ms=2.10,
        jitter_macd_ms=0.28,
        probe_count_attempted=10,
        probe_count_successful=10,
        status="SUCCESS",
    )
    card.set_measurements(data)

    labels = [w.text() for w in card.findChildren(QLabel)]
    # 0.0% loss must be explicitly rendered, not replaced with N/A
    assert any("0.0% (0/10 dropped)" in t for t in labels)
    assert any("Median: 1.45 ms" in t for t in labels)
    assert any("Min: 1.12 | Max: 2.31" in t for t in labels)
    assert any("P95 Latency:" in t for t in labels)
    assert any("2.10 ms (n=10)" in t for t in labels)
    assert any("MACD Jitter:" in t for t in labels)
    assert any("0.28 ms delay variation" in t for t in labels)


def test_stage2_missing_measurements_distinction(qapp: QApplication) -> None:
    """Verify Stage 2 distinguishes missing measurements from 0.0."""
    card = DiagnosticStageCard(2, "Stage 2", "Gateway Reachability", "Desc")

    data = Stage2Data(
        packet_loss_pct=None,  # Missing
        median_rtt_ms=None,   # Missing
        p95_rtt_ms=None,      # Not enough samples (< 10)
        jitter_macd_ms=None,  # Not enough samples (< 2)
        probe_count_attempted=0,
        probe_count_successful=0,
        status="FAILED",
    )
    card.set_measurements(data)

    labels = [w.text() for w in card.findChildren(QLabel)]
    assert any("N/A (Unmeasured)" in t for t in labels)
    assert any("N/A (No successful probes)" in t for t in labels)
    # P95 and MACD must NOT be rendered if samples are insufficient
    assert not any("P95 Latency:" in t for t in labels)
    assert not any("MACD Jitter:" in t for t in labels)


# =============================================================================
# 5. Stage 3: Multi-Target External Probing & Partial Results
# =============================================================================


def test_stage3_multi_target_partial_results(qapp: QApplication) -> None:
    """Verify Stage 3 accurately presents partial responses across external targets."""
    card = DiagnosticStageCard(3, "Stage 3", "Multi-Target External Probing", "Desc")

    t1 = Stage3TargetResult(
        target_id="cf_dns",
        host="1.1.1.1",
        label="Cloudflare DNS",
        packet_loss_pct=0.0,
        median_rtt_ms=12.4,
        is_reachable=True,
    )
    t2 = Stage3TargetResult(
        target_id="goog_dns",
        host="8.8.8.8",
        label="Google DNS",
        packet_loss_pct=0.0,
        median_rtt_ms=14.1,
        is_reachable=True,
    )
    t3 = Stage3TargetResult(
        target_id="quad9",
        host="9.9.9.9",
        label="Quad9 DNS",
        packet_loss_pct=100.0,
        median_rtt_ms=None,
        is_reachable=False,
    )

    data = Stage3Data(
        targets=[t1, t2, t3],
        reachable_count=2,
        total_count=3,
        overall_median_rtt_ms=13.25,
        status="PARTIAL",
    )
    card.set_measurements(data)

    labels = [w.text() for w in card.findChildren(QLabel)]
    assert any("2/3 Targets Reachable" in t for t in labels)
    assert any("Median RTT: 13.2 ms" in t for t in labels)
    assert any("Cloudflare DNS:" in t for t in labels)
    assert any("12.4 ms (Loss: 0%)" in t for t in labels)
    assert any("Quad9 DNS:" in t for t in labels)
    assert any("Timeout (Loss: 100%)" in t for t in labels)


# =============================================================================
# 6. Stage 4: Direct DNS Query Timing & Resolution Status
# =============================================================================


def test_stage4_dns_query_success_and_error(qapp: QApplication) -> None:
    """Verify Stage 4 renders target domain, resolver, RCODE, and error handling."""
    card = DiagnosticStageCard(4, "Stage 4", "Direct DNS Query Timing", "Desc")

    # Success case
    data_ok = Stage4Data(
        tested_domain="example.com",
        resolver_ip="1.1.1.1",
        response_time_ms=18.6,
        rcode_name="NOERROR",
        resolution_status="RESOLVED",
        status="SUCCESS",
    )
    card.set_measurements(data_ok)

    labels_ok = [w.text() for w in card.findChildren(QLabel)]
    assert any("example.com (UDP port 53)" in t for t in labels_ok)
    assert any("1.1.1.1" in t for t in labels_ok)
    assert any("18.6 ms" in t for t in labels_ok)
    assert any("NOERROR (RESOLVED)" in t for t in labels_ok)

    # DNS failure case (SERVFAIL)
    data_err = Stage4Data(
        tested_domain="internal.corp",
        resolver_ip="8.8.8.8",
        response_time_ms=None,
        rcode_name="SERVFAIL",
        resolution_status="SERVER_FAILURE",
        error_message="Resolver returned SERVFAIL",
        status="FAILED",
    )
    card.set_measurements(data_err)

    labels_err = [w.text() for w in card.findChildren(QLabel)]
    assert any("internal.corp" in t for t in labels_err)
    assert any("SERVFAIL (SERVER_FAILURE)" in t for t in labels_err)


# =============================================================================
# 7. Stage 5: Throughput & Loaded Latency Policy Bypasses
# =============================================================================


def test_stage5_throughput_and_loaded_latency(qapp: QApplication) -> None:
    """Verify Stage 5 renders downstream speed and bufferbloat metrics."""
    card = DiagnosticStageCard(5, "Stage 5", "Throughput & Loaded Latency", "Desc")

    data = Stage5Data(
        throughput_mbps=84.2,
        baseline_median_rtt_ms=12.0,
        loaded_median_rtt_ms=28.5,
        delta_rtt_median_ms=16.5,
        bufferbloat_ratio=2.38,
        status="SUCCESS",
    )
    card.set_measurements(data)

    labels = [w.text() for w in card.findChildren(QLabel)]
    assert any("84.2 Mbps" in t for t in labels)
    assert any("28.5 ms vs baseline 12.0 ms (+16.5 ms)" in t for t in labels)
    assert any("2.38x baseline" in t for t in labels)


def test_stage5_skipped_policy_handling(qapp: QApplication) -> None:
    """Verify Stage 5 correctly displays skipped policy according to SRS-F-008."""
    card = DiagnosticStageCard(5, "Stage 5", "Throughput & Loaded Latency", "Desc")

    data = Stage5Data(
        is_skipped=True,
        skip_reason="Gateway unreachable; active downstream load test bypassed",
        status="SKIPPED",
    )
    card.set_measurements(data)

    labels = [w.text() for w in card.findChildren(QLabel)]
    assert any("Stage Skipped per SRS-F-008" in t for t in labels)
    assert any("Gateway unreachable" in t for t in labels)
    assert any("N/A (Bypassed)" in t for t in labels)


# =============================================================================
# 8. Reset Behavior & New Run Clean Lifecycle
# =============================================================================


def test_new_run_resets_all_stage_cards(qapp: QApplication) -> None:
    """Verify all cards and telemetry are completely reset when a new run begins."""
    page = DiagnosisPage()

    # Artificially populate Stage 1 with telemetry
    card1: DiagnosticStageCard = page.stage_boxes[1]
    card1.set_stage_state(StageCardState.SUCCESS, "Status: Completed (SUCCESS)")
    card1.set_measurements(
        Stage1Data(gateway_ip="192.168.1.1", interface_name="eth0", status="SUCCESS")
    )
    assert any("192.168.1.1" in w.text() for w in card1.findChildren(QLabel))

    # Trigger new run started
    page._on_run_started("run-new-lifecycle-001")

    # Verify all cards are reset to PENDING and previous telemetry cleared
    for order in range(1, 6):
        c: DiagnosticStageCard = page.stage_boxes[order]
        assert c.current_state == StageCardState.PENDING
        assert c.lbl_badge.text() == "PENDING"
        assert "Pending" in c.lbl_status.text()
        # Telemetry should be reset to description
        assert not any("192.168.1.1" in w.text() for w in c.findChildren(QLabel))


# =============================================================================
# 9. Stale-Run Protection
# =============================================================================


def test_stale_run_events_are_discarded(qapp: QApplication) -> None:
    """Verify events from an older or mismatched run ID cannot overwrite active run cards."""
    page = DiagnosisPage()

    # Start Run A
    page._on_run_started("run-active-A")
    page._on_stage_started("run-active-A", 1, "Gateway Discovery")

    card1: DiagnosticStageCard = page.stage_boxes[1]
    assert card1.current_state == StageCardState.RUNNING

    # Stale event arrives from Run X (e.g. late worker event)
    page._on_stage_completed("run-stale-X", 1, "Gateway Discovery", "SUCCESS")
    # State should remain RUNNING for run-active-A, NOT overwritten by run-stale-X
    assert card1.current_state == StageCardState.RUNNING
    assert "In Progress" in card1.lbl_status.text()

    # Stale skipped event arrives for Stage 5 from Run X
    card5: DiagnosticStageCard = page.stage_boxes[5]
    page._on_stage_skipped("run-stale-X", 5, "Throughput", "Old reason")
    assert card5.current_state == StageCardState.PENDING

    # Legitimate event arrives from Run A
    page._on_stage_completed("run-active-A", 1, "Gateway Discovery", "SUCCESS")
    assert card1.current_state == StageCardState.SUCCESS


# =============================================================================
# 10. Database Extractor Integration & Non-blocking Safety
# =============================================================================


def test_stage_measurement_extractor_db_queries(test_db: DatabaseManager) -> None:
    """Verify StageMeasurementExtractor extracts data from SQLite without errors."""
    run_id = "run-db-extractor-001"
    now_iso = now_utc_iso()

    with test_db.transaction() as conn:
        # Insert targets for referential integrity
        conn.execute(
            "INSERT INTO targets (target_id, target_type, hostname, ip_address, provider_label, is_active) "
            "VALUES ('gateway', 'GATEWAY', 'router', '10.0.0.1', 'Local Gateway', 1);"
        )
        conn.execute(
            "INSERT INTO targets (target_id, target_type, hostname, ip_address, provider_label, is_active) "
            "VALUES ('dns', 'DNS_RESOLVER', 'cloudflare', '1.1.1.1', 'Cloudflare DNS', 1);"
        )
        # Insert test run
        conn.execute(
            "INSERT INTO test_runs (run_id, start_time_utc, status, run_mode, app_version, config_version, gateway_ip, interface_name, interface_type) "
            "VALUES (?, ?, 'COMPLETED', 'OPERATIONAL', '1.0.0', '1.0.0', '10.0.0.1', 'en0', 'Wi-Fi');",
            (run_id, now_iso),
        )
        # Insert Stage 2 summaries
        conn.execute(
            "INSERT INTO summaries (summary_id, run_id, target_id, metric_name, statistic_name, value_numeric, "
            "unit, sample_count_total, sample_count_valid, calculation_method, calculation_version) "
            "VALUES (?, ?, 'gateway', 'packet_loss', 'raw', 0.0, 'percent', 10, 10, 'raw_loss', '1.0.0');",
            (new_id(), run_id),
        )
        conn.execute(
            "INSERT INTO summaries (summary_id, run_id, target_id, metric_name, statistic_name, value_numeric, "
            "unit, sample_count_total, sample_count_valid, calculation_method, calculation_version) "
            "VALUES (?, ?, 'gateway', 'round_trip_time', 'median', 2.34, 'ms', 10, 10, 'median_rtt', '1.0.0');",
            (new_id(), run_id),
        )
        # Insert Stage 4 DNS observation
        conn.execute(
            "INSERT INTO observations (observation_id, run_id, sample_index, timestamp_utc, duration_ms, "
            "unit, target_id, metric_name, value_numeric, status, method_metadata) "
            "VALUES (?, ?, 1, ?, 15.2, 'ms', 'dns', 'dns_query_time', 15.2, 'SUCCESS', ?);",
            (
                new_id(),
                run_id,
                now_iso,
                json.dumps({
                    "domain": "cloudflare.com",
                    "resolver_ip": "1.1.1.1",
                    "rcode_name": "NOERROR",
                    "resolution_status": "RESOLVED",
                    "has_answer": True,
                }),
            ),
        )

    extractor = StageMeasurementExtractor(db=test_db)

    # Stage 1 DB extraction
    s1 = extractor.extract_stage1(run_id)
    assert s1.gateway_ip == "10.0.0.1"
    assert s1.interface_name == "en0"

    # Stage 2 DB extraction
    s2 = extractor.extract_stage2(run_id)
    assert s2.packet_loss_pct == 0.0
    assert s2.median_rtt_ms == 2.34

    # Stage 4 DB extraction
    s4 = extractor.extract_stage4(run_id)
    assert s4.tested_domain == "cloudflare.com"
    assert s4.resolver_ip == "1.1.1.1"
    assert s4.response_time_ms == 15.2
    assert s4.rcode_name == "NOERROR"


# =============================================================================
# 11. Long Text Wrapping & Overflow Handling
# =============================================================================


def test_stage_cards_long_text_wrapping(qapp: QApplication) -> None:
    """Verify very long error and description texts wrap safely without crashing."""
    card = DiagnosticStageCard(1, "Stage 1", "Gateway Discovery", "Desc")

    long_error = (
        "Fatal socket error while probing gateway 192.168.1.1: Operation timed out after 5000 milliseconds "
        "across all network interfaces without response from the upstream routing daemon or hardware bridge."
    )
    data = Stage1Data(status="FAILED", error_message=long_error)
    card.set_measurements(data)

    labels = card.findChildren(QLabel)
    assert any(long_error in l.text() for l in labels)


# =============================================================================
# 12. Responsive Sizing & Offscreen Rendering
# =============================================================================


@pytest.mark.parametrize("width,height", [(800, 600), (1024, 768), (1280, 800)])
def test_stage_cards_responsive_rendering(qapp: QApplication, width: int, height: int) -> None:
    """Verify Stage cards render cleanly without layout exceptions across viewport dimensions."""
    page = DiagnosisPage()
    page.resize(width, height)
    page.show()

    # Populate all 5 cards
    c1: DiagnosticStageCard = page.stage_boxes[1]
    c1.set_measurements(Stage1Data(gateway_ip="192.168.1.1", interface_name="en0", status="SUCCESS"))

    c2: DiagnosticStageCard = page.stage_boxes[2]
    c2.set_measurements(Stage2Data(packet_loss_pct=0.0, median_rtt_ms=2.1, status="SUCCESS"))

    c3: DiagnosticStageCard = page.stage_boxes[3]
    c3.set_measurements(Stage3Data(reachable_count=3, total_count=3, overall_median_rtt_ms=14.5, status="SUCCESS"))

    c4: DiagnosticStageCard = page.stage_boxes[4]
    c4.set_measurements(Stage4Data(tested_domain="example.com", response_time_ms=12.0, status="SUCCESS"))

    c5: DiagnosticStageCard = page.stage_boxes[5]
    c5.set_measurements(Stage5Data(throughput_mbps=112.5, status="SUCCESS"))

    # Render offscreen into QImage
    image = QImage(QSize(width, height), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    page.render(image, renderFlags=QWidget.RenderFlag.DrawChildren)

    assert not image.isNull()
    assert image.width() == width
    assert image.height() == height
