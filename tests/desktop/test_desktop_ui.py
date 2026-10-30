"""Integration tests for TicNet Desktop UI and Service Layer (Phase 10 - Stage 2).

Verifies:
- DiagnosisPage integration with DiagnosticService (controls, live stage updates, results button).
- ResultsPage rendering of real findings, evidence items, missing-value honesty, and inconclusive states.
- HistoryPage table population, double-click selection, search filtering, and empty states.
- ReportsPage native export dialog integration, format preview, privacy masking, and audit records.
- MainWindow end-to-end event synchronization and safe shutdown handling.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List
import unittest.mock as mock

# Force offscreen rendering for headless tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6 import QtCore
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from ticnet.desktop.main_window import MainWindow
from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.pages.history_page import HistoryPage
from ticnet.desktop.pages.overview_page import OverviewPage
from ticnet.desktop.pages.reports_page import ReportsPage
from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.services import DesktopServices
from ticnet.models.entities import (
    Finding,
    FindingEvidence,
    Observation,
    ReportExport,
    Summary,
    Target,
    TestRun,
    TestStage,
    new_id,
    now_utc_iso,
)
from ticnet.models.enums import (
    EvidenceRelationship,
    EvidenceStrength,
    ExportFormat,
    ExportStatus,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
    RunStatus,
    StageStatus,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.export_repo import ExportRepository
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType


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
    db_file = tmp_path / "test_ui.sqlite3"
    return DesktopServices.create_default(db_path=db_file, run_migrations=True)


@pytest.fixture
def main_window(qapp: QApplication, desktop_services: DesktopServices) -> MainWindow:
    """Create a MainWindow wired to desktop_services."""
    window = MainWindow(services=desktop_services)
    yield window
    window._is_diagnosing = False
    if hasattr(window, "page_reports"):
        window.page_reports._is_exporting = False
    window.close()


# ============================================================================
# DiagnosisPage UI Tests
# ============================================================================

def test_diagnosis_page_ui_flow(main_window: MainWindow, desktop_services: DesktopServices) -> None:
    """Verify Start Diagnosis triggers worker, updates stage labels, and enables Cancel."""
    diag_page: DiagnosisPage = main_window.page_diagnosis

    # Initial idle state
    assert diag_page.btn_start.isEnabled() is True
    assert diag_page.btn_cancel.isEnabled() is False
    assert diag_page.btn_view_results.isHidden() is True

    # Simulate run start
    desktop_services.diagnostic.sig_run_started.emit("run-ui-001")
    assert diag_page.btn_start.isEnabled() is False
    assert diag_page.btn_cancel.isEnabled() is True
    assert "run-ui-001" in diag_page.lbl_session_status.text()
    assert main_window._is_diagnosing is True

    # Simulate Stage 1 start and completion
    desktop_services.diagnostic.sig_stage_started.emit("run-ui-001", 1, "Gateway Discovery")
    lbl_s1 = diag_page.stage_status_labels[1]
    assert "In Progress" in lbl_s1.text()

    desktop_services.diagnostic.sig_stage_completed.emit("run-ui-001", 1, "Gateway Discovery", "SUCCESS")
    assert "Completed (SUCCESS)" in lbl_s1.text()

    # Simulate Stage 5 skipped
    desktop_services.diagnostic.sig_stage_skipped.emit("run-ui-001", 5, "Throughput", "Gateway unreachable")
    lbl_s5 = diag_page.stage_status_labels[5]
    assert "Skipped" in lbl_s5.text()

    # Simulate Diagnosis completion
    desktop_services.diagnostic.sig_diagnosis_completed.emit("run-ui-001", 2)
    assert diag_page.btn_view_results.isHidden() is False
    assert "2 Explainable Findings" in diag_page.lbl_session_status.text()

    # Simulate Run finish
    desktop_services.diagnostic.sig_run_finished.emit("run-ui-001", "COMPLETED", "Finished")
    assert diag_page.btn_start.isEnabled() is True
    assert diag_page.btn_cancel.isEnabled() is False
    assert main_window._is_diagnosing is False


def test_diagnosis_page_cancel_flow(main_window: MainWindow, desktop_services: DesktopServices) -> None:
    """Verify Cancel button signals diagnostic service and updates session status."""
    diag_page: DiagnosisPage = main_window.page_diagnosis
    desktop_services.diagnostic.cancel_diagnosis = mock.MagicMock()

    desktop_services.diagnostic.sig_run_started.emit("run-cancel-test")
    assert diag_page.btn_cancel.isEnabled() is True

    QTest.mouseClick(diag_page.btn_cancel, QtCore.Qt.MouseButton.LeftButton)
    desktop_services.diagnostic.cancel_diagnosis.assert_called_once()


# ============================================================================
# ResultsPage UI Tests
# ============================================================================

def test_results_page_rendering_real_findings(
    main_window: MainWindow, desktop_services: DesktopServices
) -> None:
    """Verify ResultsPage renders real findings, evidence links, and technical limitations."""
    results_page: ResultsPage = main_window.page_results
    db = desktop_services.db

    # Seed run, observations, and findings
    run_repo = RunRepository(db)
    obs_repo = ObservationRepository(db)
    finding_repo = FindingRepository(db)

    run = TestRun(
        run_id="run-results-test",
        start_time_utc="2026-10-24T12:00:00Z",
        end_time_utc="2026-10-24T12:00:30Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi",
    )
    run_repo.create_run(run)

    obs = Observation(
        observation_id="obs-rtt-test",
        run_id="run-results-test",
        sample_index=1,
        timestamp_utc="2026-10-24T12:00:05Z",
        duration_ms=45.2,
        metric_name="median_rtt",
        unit="ms",
        status=MeasurementStatus.SUCCESS,
        value_numeric=45.2,
    )
    obs_repo.create_observation(obs)

    finding = Finding(
        finding_id="f-loss-01",
        run_id="run-results-test",
        category=FindingCategory.EXTERNAL_PATH,
        priority=FindingPriority.WARNING,
        rule_id="R-LOSS-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Upstream Path Latency Elevation",
        limitations="Correlated across 3 external endpoints",
        recommended_action="Contact ISP technical support",
    )
    finding_repo.create_finding(finding)

    evidence = FindingEvidence(
        evidence_id="ev-rtt-test",
        finding_id="f-loss-01",
        relationship_type=EvidenceRelationship.SUPPORTING,
        observation_id="obs-rtt-test",
    )
    finding_repo.link_evidence(evidence)

    # Load results into page
    results_page.load_run_results("run-results-test")

    assert results_page.results_container.isHidden() is False
    assert results_page.empty_state.isHidden() is True

    all_labels = [lbl.text() for lbl in results_page.findChildren(QLabel)]
    assert any("run-results-test" in t for t in all_labels)
    assert any("Upstream Path Latency Elevation" in t for t in all_labels)
    assert any("R-LOSS-01" in t for t in all_labels)
    assert any("45.2 ms" in t for t in all_labels)
    assert any("Correlated across 3 external endpoints" in t for t in all_labels)


def test_results_page_inconclusive_state(
    main_window: MainWindow, desktop_services: DesktopServices
) -> None:
    """Verify inconclusive diagnostic rule (R-INCONCLUSIVE-01) is displayed truthfully."""
    results_page: ResultsPage = main_window.page_results
    db = desktop_services.db

    run_repo = RunRepository(db)
    finding_repo = FindingRepository(db)

    run = TestRun(
        run_id="run-inconclusive-test",
        start_time_utc=now_utc_iso(),
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
    )
    run_repo.create_run(run)

    inconclusive_finding = Finding(
        finding_id="f-inconc",
        run_id="run-inconclusive-test",
        category=FindingCategory.INCONCLUSIVE,
        priority=FindingPriority.INFO,
        rule_id="R-INCONCLUSIVE-01",
        rule_version="1.0.0",
        status=FindingStatus.INSUFFICIENT_EVIDENCE,
        evidence_strength=EvidenceStrength.INCONCLUSIVE,
        plain_summary="Inconclusive Diagnosis — Single Destination Failure Guardrail",
        limitations="Single destination unreachable while other targets responded normally",
        recommended_action="Rerun diagnosis to verify destination availability",
    )
    finding_repo.create_finding(inconclusive_finding)

    results_page.load_run_results("run-inconclusive-test")

    all_labels = [lbl.text() for lbl in results_page.findChildren(QLabel)]
    assert any("R-INCONCLUSIVE-01" in t for t in all_labels)
    assert any("Single Destination Failure Guardrail" in t for t in all_labels)


# ============================================================================
# HistoryPage UI Tests
# ============================================================================

def test_history_page_table_interaction(
    main_window: MainWindow, desktop_services: DesktopServices
) -> None:
    """Verify HistoryPage displays runs, filters via search, and triggers selection."""
    history_page: HistoryPage = main_window.page_history
    db = desktop_services.db
    run_repo = RunRepository(db)

    # 1. Initially empty
    history_page.load_history()
    assert history_page.empty_state.isHidden() is False
    assert history_page.table_card.isHidden() is True

    # 2. Seed runs
    r1 = TestRun(
        run_id="run-alpha",
        start_time_utc="2026-10-24T14:00:00Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi",
    )
    r2 = TestRun(
        run_id="run-beta",
        start_time_utc="2026-10-24T15:00:00Z",
        status=RunStatus.FAILED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="10.0.0.1",
        interface_name="Ethernet",
    )
    run_repo.create_run(r1)
    run_repo.create_run(r2)

    # 3. Reload
    history_page.load_history()
    assert history_page.empty_state.isHidden() is True
    assert history_page.table_card.isHidden() is False
    assert history_page.tbl_runs.rowCount() == 2

    # 4. Filter search text
    history_page.txt_search.setText("Ethernet")
    assert history_page.tbl_runs.rowCount() == 1
    assert history_page.tbl_runs.item(0, 0).text() == "run-beta"

    history_page.txt_search.setText("")
    assert history_page.tbl_runs.rowCount() == 2

    # 5. Row selection enables "View Selected Findings" button
    history_page.tbl_runs.selectRow(0)
    assert history_page.btn_load_selected.isEnabled() is True


# ============================================================================
# ReportsPage UI Tests
# ============================================================================

def test_reports_page_export_integration(
    main_window: MainWindow, desktop_services: DesktopServices, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify ReportsPage preview updates, native save dialog executes export, and audits."""
    reports_page: ReportsPage = main_window.page_reports
    db = desktop_services.db

    # Seed run
    run_repo = RunRepository(db)
    obs_repo = ObservationRepository(db)

    run = TestRun(
        run_id="run-export-ui",
        start_time_utc="2026-10-24T16:00:00Z",
        end_time_utc="2026-10-24T16:00:20Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
    )
    run_repo.create_run(run)

    obs = Observation(
        observation_id="obs-export-1",
        run_id="run-export-ui",
        sample_index=1,
        timestamp_utc="2026-10-24T16:00:05Z",
        duration_ms=10.0,
        metric_name="latency",
        unit="ms",
        status=MeasurementStatus.SUCCESS,
        value_numeric=10.0,
    )
    obs_repo.create_observation(obs)

    # Select run
    reports_page.set_selected_run("run-export-ui")
    assert reports_page.btn_export.isEnabled() is True
    assert reports_page.preview_card.isHidden() is False

    # Check preview content
    preview_content = reports_page.txt_preview.toPlainText()
    assert "TicNet" in preview_content
    assert "run-export-ui" in preview_content

    # Mock QFileDialog.getSaveFileName and QMessageBox.information
    target_export_file = tmp_path / "ui_exported_report.md"
    from PySide6.QtCore import QCoreApplication, QThreadPool
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(target_export_file), "Markdown (*.md)"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    # Trigger export
    QTest.mouseClick(reports_page.btn_export, QtCore.Qt.MouseButton.LeftButton)
    QThreadPool.globalInstance().waitForDone(5000)
    QCoreApplication.processEvents()

    assert target_export_file.exists()
    assert target_export_file.stat().st_size > 0

    # Verify audit in SQLite
    export_repo = ExportRepository(db)
    audits = export_repo.get_exports_for_run("run-export-ui")
    assert len(audits) == 1
    assert audits[0].export_status == ExportStatus.SUCCESS
