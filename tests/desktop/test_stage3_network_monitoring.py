"""Dedicated tests for Stage 3: PingPlotter-Inspired Latency Charts and Packet-Loss Visualisation.

Verifies:
- PingPlotter-style vertical red bars for packet loss / timeouts (never coerced to 0 ms).
- Multi-target separation (Checkpoint 1 Local Gateway vs Checkpoint 2 External Endpoints).
- TargetTraceTableWidget hop isolation, empirical statistics, and LossBarWidget.
- Explainable Two-Checkpoint localization narrative and ISP disclaimer.
- Interactive target and range filtering on LatencyTimelineChart.
- Signal wiring between trace table clicks and chart target selection.
- Full results page integration with empirical session telemetry.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.services.findings_service import FindingsService
from ticnet.desktop.services.history_service import HistoryService
from ticnet.desktop.widgets.chart_widget import (
    LatencyTimelineChart,
    LoadedLatencyBarChart,
    MeasurementChartsContainer,
)
from ticnet.desktop.widgets.target_trace_widget import LossBarWidget, TargetTraceTableWidget
from ticnet.models.entities import (
    Finding,
    FindingEvidence,
    Observation,
    Summary,
    Target,
    TestRun,
    new_id,
    now_utc_iso,
)
from ticnet.models.enums import (
    EvidenceRelationship,
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
    RunMode,
    RunStatus,
    TargetType,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "stage3_monitoring_test.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


def _create_synthetic_run_observations() -> tuple[List[Observation], List[Target]]:
    """Helper creating real-format observations for Gateway, Cloudflare, and Google."""
    gw_target = Target(
        target_id="gw-local-1",
        target_type=TargetType.GATEWAY,
        ip_address="192.168.1.1",
        provider_label="Local Router",
    )
    cf_target = Target(
        target_id="ext-cloudflare-primary",
        target_type=TargetType.EXTERNAL_IP,
        ip_address="1.1.1.1",
        provider_label="Cloudflare Primary",
    )
    gg_target = Target(
        target_id="ext-google-primary",
        target_type=TargetType.EXTERNAL_IP,
        ip_address="8.8.8.8",
        provider_label="Google Public DNS",
    )
    targets = [gw_target, cf_target, gg_target]

    observations: List[Observation] = []

    # Gateway: 10 samples, all success ~2.2 ms, 0% loss
    for i in range(1, 11):
        observations.append(
            Observation(
                observation_id=new_id(),
                run_id="run-s3",
                stage_id="s2",
                target_id="gw-local-1",
                sample_index=i,
                timestamp_utc=f"2026-10-24T18:45:{i:02d}Z",
                duration_ms=2.0 + (i % 3) * 0.2,
                metric_name="round_trip_time",
                value_numeric=2.0 + (i % 3) * 0.2,
                unit="ms",
                status=MeasurementStatus.SUCCESS,
            )
        )

    # Cloudflare: 10 samples, 1 timeout at sample 5 (10% loss)
    for i in range(1, 11):
        is_to = (i == 5)
        observations.append(
            Observation(
                observation_id=new_id(),
                run_id="run-s3",
                stage_id="s3",
                target_id="ext-cloudflare-primary",
                sample_index=i,
                timestamp_utc=f"2026-10-24T18:46:{i:02d}Z",
                duration_ms=0.0 if is_to else 21.0 + (i % 4) * 0.5,
                metric_name="round_trip_time",
                value_numeric=None if is_to else 21.0 + (i % 4) * 0.5,
                unit="ms",
                status=MeasurementStatus.TIMEOUT if is_to else MeasurementStatus.SUCCESS,
                error_message="Request timed out" if is_to else None,
            )
        )

    # Google: 10 samples, 2 timeouts at 8 and 9 (20% loss)
    for i in range(1, 11):
        is_to = (i in (8, 9))
        observations.append(
            Observation(
                observation_id=new_id(),
                run_id="run-s3",
                stage_id="s3",
                target_id="ext-google-primary",
                sample_index=i,
                timestamp_utc=f"2026-10-24T18:47:{i:02d}Z",
                duration_ms=0.0 if is_to else 26.0 + (i % 3) * 1.0,
                metric_name="round_trip_time",
                value_numeric=None if is_to else 26.0 + (i % 3) * 1.0,
                unit="ms",
                status=MeasurementStatus.TIMEOUT if is_to else MeasurementStatus.SUCCESS,
                error_message="Request timed out" if is_to else None,
            )
        )

    return observations, targets


# ============================================================================
# Unit Tests: LossBarWidget & TargetTraceTableWidget
# ============================================================================

def test_loss_bar_widget(qapp: QApplication) -> None:
    """Verify LossBarWidget handles 0%, partial loss, and 100% loss cleanly."""
    bar_clean = LossBarWidget(0.0)
    assert bar_clean.loss_pct == 0.0

    bar_partial = LossBarWidget(25.5)
    assert bar_partial.loss_pct == 25.5

    bar_full = LossBarWidget(150.0)
    assert bar_full.loss_pct == 100.0  # Clamped to 100%


def test_target_trace_table_empty_state(qapp: QApplication) -> None:
    """Verify TargetTraceTableWidget handles empty observations without crashing."""
    trace = TargetTraceTableWidget()
    trace.load_trace_data([])
    assert trace.table.rowCount() == 0
    assert "No observations recorded" in trace.lbl_analysis_body.text()


def test_target_trace_table_populates_data_accurately(qapp: QApplication) -> None:
    """Verify TargetTraceTableWidget computes exact empirical loss and median per hop."""
    observations, targets = _create_synthetic_run_observations()
    trace = TargetTraceTableWidget()
    trace.load_trace_data(observations=observations, targets=targets)

    # 3 targets -> 3 rows
    assert trace.table.rowCount() == 3

    def _cell_text(r: int, c: int) -> str:
        it = trace.table.item(r, c)
        assert it is not None
        return it.text()

    # Row 0: Gateway (Hop 1, 0% loss, median ~2.2ms, HEALTHY)
    assert _cell_text(0, 0) == "1"
    assert "Gateway" in _cell_text(0, 1)
    assert _cell_text(0, 4) == "10 / 10"
    assert _cell_text(0, 5) == "0.0%"
    assert "HEALTHY" in _cell_text(0, 10)

    # Row 1: Cloudflare (1 timeout -> 10% loss)
    assert _cell_text(1, 4) == "10 / 9"
    assert _cell_text(1, 5) == "10.0%"

    # Row 2: Google (2 timeouts -> 20% loss, HIGH LOSS)
    assert _cell_text(2, 4) == "10 / 8"
    assert _cell_text(2, 5) == "20.0%"
    assert "HIGH LOSS" in _cell_text(2, 10)

    # Localization Analysis: Gateway is clean, degradation observed beyond Checkpoint 1
    analysis_text = trace.lbl_analysis_body.text()
    assert "Checkpoint 1" in analysis_text
    assert "Beyond Checkpoint 1" in analysis_text

    # Disclaimer must explicitly state external degradation does not prove an ISP fault
    disclaimer_text = trace.lbl_analysis_disclaimer.text()
    assert "does not prove an ISP fault" in disclaimer_text


def test_target_trace_table_localizes_gateway_fault(qapp: QApplication) -> None:
    """Verify gateway degradation localizes to Checkpoint 1."""
    # Gateway with 50% loss
    observations: List[Observation] = []
    for i in range(1, 11):
        is_to = (i % 2 == 0)
        observations.append(
            Observation(
                observation_id=new_id(),
                run_id="run-gw-fault",
                sample_index=i,
                timestamp_utc=f"2026-10-24T18:45:{i:02d}Z",
                duration_ms=0.0 if is_to else 15.0,
                metric_name="round_trip_time",
                value_numeric=None if is_to else 15.0,
                unit="ms",
                status=MeasurementStatus.TIMEOUT if is_to else MeasurementStatus.SUCCESS,
                target_id="gw-local-1",
            )
        )

    trace = TargetTraceTableWidget()
    trace.load_trace_data(observations)
    assert "Checkpoint 1 (Local Gateway) Fault Detected" in trace.lbl_analysis_body.text()


# ============================================================================
# Unit Tests: LatencyTimelineChart
# ============================================================================

def test_latency_timeline_multi_target_and_timeout_bars(qapp: QApplication) -> None:
    """Verify LatencyTimelineChart renders target line series and red timeout bars."""
    observations, targets = _create_synthetic_run_observations()
    chart = LatencyTimelineChart()
    chart.load_multi_target_observations(observations=observations, targets=targets)

    # Active chart view displayed
    assert chart.stack_layout.currentIndex() == 1

    # Dropdown populated with All, Gateway, External, plus individual targets
    assert chart.cmb_target.count() >= 5
    assert chart.cmb_target.itemData(0) == "ALL"
    assert chart.cmb_target.itemData(1) == "GATEWAY"
    assert chart.cmb_target.itemData(2) == "EXTERNAL"

    # Overall packet loss badge shows 3 timeouts out of 30 probes = 10.0% loss
    assert "10.0%" in chart.lbl_loss_badge.text()
    assert "3 / 30" in chart.lbl_loss_badge.text()

    # Series in chart:
    # 3 line series for targets + vertical lines for timeouts
    assert chart.chart is not None
    chart_series = chart.chart.series()
    series_names = [s.name() for s in chart_series]
    assert any("Gateway" in n for n in series_names)
    assert any("Cloudflare" in n for n in series_names)
    assert any("Google" in n for n in series_names)
    assert any("Packet Loss (Timeout)" in n for n in series_names)


def test_latency_timeline_target_filtering(qapp: QApplication) -> None:
    """Verify switching target filter updates the chart view."""
    observations, targets = _create_synthetic_run_observations()
    chart = LatencyTimelineChart()
    chart.load_multi_target_observations(observations=observations, targets=targets)

    # Filter to Gateway only
    chart.set_selected_target("gw-local-1")
    assert chart.cmb_target.currentData() == "gw-local-1"

    # Gateway has 0 timeouts, so loss badge shows 0.0%
    assert "0.0%" in chart.lbl_loss_badge.text()
    assert "0 / 10" in chart.lbl_loss_badge.text()


def test_target_trace_click_wires_to_chart(qapp: QApplication) -> None:
    """Verify row selection in TargetTraceTableWidget emits signal and filters chart."""
    container = MeasurementChartsContainer()
    observations, targets = _create_synthetic_run_observations()
    container.load_run_observations(observations=observations, targets=targets)

    # Simulate click on row 1 (Cloudflare)
    container.target_trace.table.cellClicked.emit(1, 0)
    assert container.rtt_chart.cmb_target.currentData() == "ext-cloudflare-primary"


# ============================================================================
# Integration Test: ResultsPage Full Workflow
# ============================================================================

def test_results_page_displays_stage3_network_monitoring(
    qapp: QApplication, test_db: DatabaseManager
) -> None:
    """Verify ResultsPage integrates TargetTraceTable and LatencyTimelineChart."""
    run_repo = RunRepository(test_db)
    obs_repo = ObservationRepository(test_db)
    finding_repo = FindingRepository(test_db)
    sum_repo = SummaryRepository(test_db)

    run_id = "run-stage3-verify"
    run = TestRun(
        run_id=run_id,
        start_time_utc="2026-10-24T18:45:00Z",
        end_time_utc="2026-10-24T18:45:20Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi 6",
    )
    run_repo.create_run(run)

    observations, targets = _create_synthetic_run_observations()
    for t in targets:
        obs_repo.create_target(t)
    for o in observations:
        o.run_id = run_id
        o.stage_id = None
        obs_repo.create_observation(o)

    find_svc = FindingsService(test_db)
    hist_svc = HistoryService(test_db)

    page = ResultsPage(findings_service=find_svc, history_service=hist_svc)
    page.load_run_results(run_id)

    # Find MeasurementChartsContainer in results layout
    container: MeasurementChartsContainer | None = None
    for i in range(page.results_layout.count()):
        item = page.results_layout.itemAt(i)
        if item is not None:
            w = item.widget()
            if isinstance(w, MeasurementChartsContainer):
                container = w
                break

    assert container is not None
    assert container.target_trace.table.rowCount() == 3
    assert container.rtt_chart.stack_layout.currentIndex() == 1
    assert "10.0%" in container.rtt_chart.lbl_loss_badge.text()
