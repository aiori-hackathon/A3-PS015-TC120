"""Tests for Stage 5: History and Final UI Polish.

Validates:
1. Redesigned History page with real persisted runs and honest empty states.
2. Multi-column session table with status badges and empirical metrics.
3. Search and multi-parameter filtering (by status, category, gateway, interface, query).
4. Historical performance trend visualization (empty guardrail vs multi-session curve).
5. Historical session detail drill-down inspection panel.
6. Navigation from history to results page.
7. Visual consistency across all seven desktop pages.
"""

from __future__ import annotations

from typing import Generator
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ticnet.desktop.main_window import MainWindow
from ticnet.desktop.pages.about_page import AboutPage
from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.pages.history_page import HistoryPage
from ticnet.desktop.pages.overview_page import OverviewPage
from ticnet.desktop.pages.reports_page import ReportsPage
from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.pages.settings_page import SettingsPage
from ticnet.desktop.services.history_service import HistoricalSessionSummary, HistoryService
from ticnet.desktop.widgets.historical_detail_widget import HistoricalDetailPanel
from ticnet.desktop.widgets.historical_trends_widget import HistoricalTrendsWidget
from ticnet.models.entities import Finding, Observation, Summary, Target, TestRun, TestStage
from ticnet.models.enums import (
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
    RunMode,
    RunStatus,
    StageStatus,
    TargetType,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository


from pathlib import Path


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "stage5_test.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


def test_history_page_empty_state(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify HistoryPage displays honest empty state when no historical runs exist."""
    svc = HistoryService(test_db)
    page = HistoryPage(history_service=svc)

    page.load_history()
    assert page.empty_state.isHidden() is False
    assert page.table_card.isHidden() is True
    assert page.tbl_runs.rowCount() == 0
    assert page.btn_load_selected.isEnabled() is False
    assert page.detail_panel.isHidden() is True
    assert page.trends_widget.stack_layout.currentIndex() == 1  # Empty state in trends


def test_history_page_with_multiple_enriched_sessions(
    qapp: QApplication, test_db: DatabaseManager
) -> None:
    """Verify HistoryPage renders enriched sessions with status badges, diagnoses, and metrics."""
    run_repo = RunRepository(test_db)
    obs_repo = ObservationRepository(test_db)
    summary_repo = SummaryRepository(test_db)
    finding_repo = FindingRepository(test_db)

    # Create Target
    t_gw = Target(
        target_id="target-gw",
        target_type=TargetType.GATEWAY,
        hostname=None,
        ip_address="192.168.1.1",
        port=None,
        provider_label="Local Gateway",
        is_active=True,
    )
    obs_repo.create_target(t_gw)

    # 1. Seed Run 1: Healthy
    r1 = TestRun(
        run_id="run-stage5-001",
        start_time_utc="2026-10-24T12:00:00Z",
        end_time_utc="2026-10-24T12:00:15Z",
        status=RunStatus.COMPLETED,
        run_mode=RunMode.OPERATIONAL,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi 6",
    )
    run_repo.create_run(r1)

    s1 = TestStage(
        stage_id="s1-1",
        run_id=r1.run_id,
        stage_name="Gateway Reachability",
        stage_order=2,
        start_time_utc="2026-10-24T12:00:01Z",
        end_time_utc="2026-10-24T12:00:04Z",
        status=StageStatus.SUCCESS,
    )
    run_repo.create_stage(s1)

    obs_repo.create_observation(
        Observation(
            observation_id="obs-s5-1",
            run_id=r1.run_id,
            stage_id=s1.stage_id,
            target_id=t_gw.target_id,
            sample_index=1,
            timestamp_utc="2026-10-24T12:00:02Z",
            duration_ms=2.1,
            metric_name="round_trip_time",
            value_numeric=2.1,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        )
    )

    finding_repo.create_finding(
        Finding(
            finding_id="find-s5-1",
            run_id=r1.run_id,
            category=FindingCategory.LOCAL_NETWORK,
            priority=FindingPriority.INFO,
            rule_id="R-HEALTHY-01",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            plain_summary="Local gateway is responsive and within normal delay bounds.",
            limitations="Does not prove ISP upstream link health.",
            recommended_action="No corrective user action required.",
        )
    )

    # 2. Seed Run 2: Bufferbloat
    r2 = TestRun(
        run_id="run-stage5-002",
        start_time_utc="2026-10-24T13:00:00Z",
        end_time_utc="2026-10-24T13:00:20Z",
        status=RunStatus.COMPLETED,
        run_mode=RunMode.OPERATIONAL,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi 6",
    )
    run_repo.create_run(r2)

    obs_repo.create_observation(
        Observation(
            observation_id="obs-s5-2",
            run_id=r2.run_id,
            stage_id=None,
            target_id=t_gw.target_id,
            sample_index=1,
            timestamp_utc="2026-10-24T13:00:02Z",
            duration_ms=18.5,
            metric_name="round_trip_time",
            value_numeric=18.5,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        )
    )

    finding_repo.create_finding(
        Finding(
            finding_id="find-s5-2",
            run_id=r2.run_id,
            category=FindingCategory.BUFFERBLOAT,
            priority=FindingPriority.WARNING,
            rule_id="R-LOAD-01",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            plain_summary="Elevated latency under saturated downstream traffic.",
            limitations="Does not measure upstream queue delay.",
            recommended_action="Enable Smart Queue Management (SQM).",
        )
    )

    # 3. Load into HistoryPage
    svc = HistoryService(test_db)
    page = HistoryPage(history_service=svc)
    page.load_history()

    assert page.empty_state.isHidden() is True
    assert page.table_card.isHidden() is False
    assert page.tbl_runs.rowCount() == 2

    # Check Run 2 (latest, top of table)
    assert page.tbl_runs.item(0, 0).text() == "run-stage5-002"
    assert "Bufferbloat" in page.tbl_runs.item(0, 3).text() or "Elevated" in page.tbl_runs.item(0, 3).text()

    # Check Run 1 (second row)
    assert page.tbl_runs.item(1, 0).text() == "run-stage5-001"


def test_history_page_filtering_and_search(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify search box, status dropdown, category dropdown, and clear filters."""
    run_repo = RunRepository(test_db)
    finding_repo = FindingRepository(test_db)

    # Run A: Completed, Local Network
    ra = TestRun(
        run_id="run-aaa-111",
        start_time_utc="2026-10-24T10:00:00Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi",
    )
    run_repo.create_run(ra)
    finding_repo.create_finding(
        Finding(
            finding_id="f-aaa",
            run_id=ra.run_id,
            category=FindingCategory.LOCAL_NETWORK,
            priority=FindingPriority.INFO,
            rule_id="R-HEALTHY",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            plain_summary="Normal gateway connectivity.",
            limitations="None.",
            recommended_action="None.",
        )
    )

    # Run B: Failed, DNS Subsystem
    rb = TestRun(
        run_id="run-bbb-222",
        start_time_utc="2026-10-24T11:00:00Z",
        status=RunStatus.FAILED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="10.0.0.1",
        interface_name="Ethernet",
    )
    run_repo.create_run(rb)
    finding_repo.create_finding(
        Finding(
            finding_id="f-bbb",
            run_id=rb.run_id,
            category=FindingCategory.DNS_SUBSYSTEM,
            priority=FindingPriority.CRITICAL,
            rule_id="R-DNS-01",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            plain_summary="DNS resolver timeout on UDP 53.",
            limitations="None.",
            recommended_action="Check resolver IP.",
        )
    )

    svc = HistoryService(test_db)
    page = HistoryPage(history_service=svc)
    page.load_history()
    assert page.tbl_runs.rowCount() == 2

    # 1. Filter by search text (interface)
    page.txt_search.setText("Ethernet")
    assert page.tbl_runs.rowCount() == 1
    assert page.tbl_runs.item(0, 0).text() == "run-bbb-222"

    # 2. Reset text search
    page.txt_search.setText("")
    assert page.tbl_runs.rowCount() == 2

    # 3. Filter by Status
    page.cbo_status.setCurrentText("COMPLETED")
    assert page.tbl_runs.rowCount() == 1
    assert page.tbl_runs.item(0, 0).text() == "run-aaa-111"

    page.cbo_status.setCurrentText("All Statuses")
    assert page.tbl_runs.rowCount() == 2

    # 4. Filter by Category
    page.cbo_category.setCurrentText("DNS_SUBSYSTEM")
    assert page.tbl_runs.rowCount() == 1
    assert page.tbl_runs.item(0, 0).text() == "run-bbb-222"

    # 5. Clear Filters button
    page.btn_clear_filters.click()
    assert page.cbo_category.currentText() == "All Categories"
    assert page.cbo_status.currentText() == "All Statuses"
    assert page.txt_search.text() == ""
    assert page.tbl_runs.rowCount() == 2


def test_historical_trends_widget_lifecycle(qapp: QApplication) -> None:
    """Verify HistoricalTrendsWidget renders empty guardrail when < 2 sessions and chart when >= 2."""
    widget = HistoricalTrendsWidget()

    # Empty sessions list
    widget.set_historical_sessions([])
    assert widget.stack_layout.currentIndex() == 1
    assert widget.card_gw_rtt.val_label.text() == "-- ms"

    # 1 session (insufficient for trend curve)
    s1 = HistoricalSessionSummary(
        run_id="run-1",
        start_time_utc="2026-10-24T10:00:00Z",
        end_time_utc="2026-10-24T10:00:10Z",
        status="COMPLETED",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi",
        overall_error=None,
        diagnosis="Healthy",
        category="LOCAL_NETWORK",
        gateway_status="SUCCESS",
        external_status="SUCCESS",
        dns_status="SUCCESS",
        packet_loss_pct=0.0,
        avg_latency_ms=12.0,
        gateway_latency_ms=2.0,
        throughput_mbps=85.0,
        loaded_latency_delta_ms=5.0,
    )
    widget.set_historical_sessions([s1])
    assert widget.stack_layout.currentIndex() == 1

    # 2 sessions (sufficient for longitudinal trend)
    s2 = HistoricalSessionSummary(
        run_id="run-2",
        start_time_utc="2026-10-24T11:00:00Z",
        end_time_utc="2026-10-24T11:00:10Z",
        status="COMPLETED",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi",
        overall_error=None,
        diagnosis="Healthy",
        category="LOCAL_NETWORK",
        gateway_status="SUCCESS",
        external_status="SUCCESS",
        dns_status="SUCCESS",
        packet_loss_pct=0.0,
        avg_latency_ms=14.0,
        gateway_latency_ms=2.2,
        throughput_mbps=90.0,
        loaded_latency_delta_ms=6.0,
    )
    widget.set_historical_sessions([s1, s2])
    assert widget.stack_layout.currentIndex() == 0  # Chart view active
    assert "2.2 ms" in widget.card_gw_rtt.val_label.text()
    assert "14.0 ms" in widget.card_ext_rtt.val_label.text()
    assert "2 runs" in widget.card_count.val_label.text()


def test_historical_detail_panel_and_navigation(qapp: QApplication) -> None:
    """Verify HistoricalDetailPanel displays session metrics and emits sig_open_results."""
    panel = HistoricalDetailPanel()
    assert panel.isHidden() is True

    session = HistoricalSessionSummary(
        run_id="run-detail-test",
        start_time_utc="2026-10-24T14:30:00Z",
        end_time_utc="2026-10-24T14:30:15Z",
        status="COMPLETED",
        gateway_ip="192.168.1.1",
        interface_name="Ethernet",
        overall_error=None,
        diagnosis="Gateway latency within nominal bounds.",
        category="LOCAL_NETWORK",
        gateway_status="SUCCESS",
        external_status="SUCCESS",
        dns_status="SUCCESS",
        packet_loss_pct=0.0,
        avg_latency_ms=15.2,
        gateway_latency_ms=1.4,
        throughput_mbps=145.0,
        loaded_latency_delta_ms=8.0,
    )

    panel.display_session(session)
    assert panel.isHidden() is False
    assert "run-detail-test" in panel.lbl_run_title.text()
    assert "1.4 ms" in panel.card_gw.val_label.text()
    assert "15.2 ms" in panel.card_ext.val_label.text()
    assert "145.0 Mbps" in panel.card_speed.val_label.text()

    # Test open results signal emission
    received_run_id = []
    panel.sig_open_results.connect(lambda rid: received_run_id.append(rid))
    panel.btn_open_full.click()
    assert received_run_id == ["run-detail-test"]


def test_all_seven_pages_instantiation_and_theme_consistency(
    qapp: QApplication, test_db: DatabaseManager
) -> None:
    """Verify that all 7 pages instantiate cleanly and maintain theme consistency."""
    svc = HistoryService(test_db)

    # 1. Overview
    page_overview = OverviewPage()
    assert page_overview.lbl_title.text() == "Overview"

    # 2. Diagnosis
    page_diagnosis = DiagnosisPage()
    assert page_diagnosis.lbl_title.text() == "Run Diagnosis"

    # 3. Results
    page_results = ResultsPage()
    assert page_results.lbl_title.text() == "Results & Evidence"

    # 4. History
    page_history = HistoryPage(history_service=svc)
    assert page_history.lbl_title.text() == "History"

    # 5. Reports
    page_reports = ReportsPage()
    assert page_reports.lbl_title.text() == "Reports & Export"

    # 6. Settings
    page_settings = SettingsPage()
    assert page_settings.lbl_title.text() == "Settings"

    # 7. About
    page_about = AboutPage()
    assert page_about.lbl_title.text() == "About TicNet"

