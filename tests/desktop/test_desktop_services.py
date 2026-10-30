"""Unit tests for TicNet Desktop Service Adapter Layer (Phase 10 - Stage 2).

Verifies:
- DiagnosticService background run dispatch, single-concurrency enforcement,
  cancellation, event-to-Qt signal translation, and completion handling.
- HistoryService run querying, ordering, filtering, stage details, and empty states.
- FindingsService evidence retrieval, priority ordering, and action recommendations.
- ReportingService Phase 9 export invocation, privacy filtering, and preview generation.
"""

from __future__ import annotations

import os
import queue
from pathlib import Path
from typing import List
import unittest.mock as mock

# Headless Qt environment
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from ticnet.configuration.loader import load_app_config
from ticnet.desktop.services.diagnostic_service import DiagnosticService
from ticnet.desktop.services.findings_service import FindingsService
from ticnet.desktop.services.history_service import HistoryService
from ticnet.desktop.services.reporting_service import ReportingService
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
    RunMode,
    RunStatus,
    StageStatus,
    TargetType,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.export_repo import ExportRepository
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType
from ticnet.workflow.worker import ConcurrentRunError


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Ensure persistent QApplication exists for Qt Signal and QTimer testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh SQLite database with migrations applied."""
    db_file = tmp_path / "test_desktop.sqlite3"
    db = DatabaseManager(db_path=db_file)
    db.run_migrations()
    return db


# ============================================================================
# DiagnosticService Tests
# ============================================================================

def test_diagnostic_service_initialization(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify DiagnosticService wires dependencies and initializes poll timer."""
    service = DiagnosticService(db=test_db)
    assert service.db is test_db
    assert service.is_running is False
    assert service.active_run_id is None
    assert service._poll_timer is not None
    assert service._poll_timer.interval() == 50


def test_diagnostic_service_duplicate_run_rejection(
    qapp: QApplication, test_db: DatabaseManager
) -> None:
    """Verify start_diagnosis enforces single-run concurrency and emits sig_error."""
    service = DiagnosticService(db=test_db)

    # Mock worker to simulate an active running thread
    service.worker._is_running = True
    service.worker._active_run_id = "run-active-123"
    mock_thread = mock.MagicMock()
    mock_thread.is_alive.return_value = True
    service.worker._thread = mock_thread

    errors_emitted: List[str] = []
    service.sig_error.connect(lambda err: errors_emitted.append(err))

    with pytest.raises(ConcurrentRunError) as exc_info:
        service.start_diagnosis()

    assert "already active" in str(exc_info.value)
    assert len(errors_emitted) == 1
    assert "already active" in errors_emitted[0]


def test_diagnostic_service_event_dispatching(
    qapp: QApplication, test_db: DatabaseManager
) -> None:
    """Verify worker events are dequeued and translated into typed Qt signals."""
    service = DiagnosticService(db=test_db)

    dispatched_events: List[str] = []
    service.sig_stage_started.connect(
        lambda run_id, order, name: dispatched_events.append(f"STARTED:{order}:{name}")
    )
    service.sig_stage_completed.connect(
        lambda run_id, order, name, status: dispatched_events.append(f"COMPLETED:{order}:{name}:{status}")
    )
    service.sig_stage_skipped.connect(
        lambda run_id, order, name, reason: dispatched_events.append(f"SKIPPED:{order}:{name}:{reason}")
    )
    service.sig_stage_failed.connect(
        lambda run_id, order, name, err: dispatched_events.append(f"FAILED:{order}:{name}:{err}")
    )

    # Put simulated workflow events onto the worker event queue
    service.worker.event_queue.put(
        WorkflowEvent(
            event_type=WorkflowEventType.STAGE_STARTED,
            run_id="run-1",
            stage_order=1,
            stage_name="Gateway Discovery",
        )
    )
    service.worker.event_queue.put(
        WorkflowEvent(
            event_type=WorkflowEventType.STAGE_COMPLETED,
            run_id="run-1",
            stage_order=1,
            stage_name="Gateway Discovery",
            status="SUCCESS",
        )
    )
    service.worker.event_queue.put(
        WorkflowEvent(
            event_type=WorkflowEventType.STAGE_SKIPPED,
            run_id="run-1",
            stage_order=5,
            stage_name="Throughput",
            message="Gateway unreachable",
        )
    )
    service.worker.event_queue.put(
        WorkflowEvent(
            event_type=WorkflowEventType.STAGE_FAILED,
            run_id="run-1",
            stage_order=4,
            stage_name="DNS Resolution",
            message="Resolver timeout",
        )
    )

    # Trigger drain
    service._poll_events()

    assert "STARTED:1:Gateway Discovery" in dispatched_events
    assert "COMPLETED:1:Gateway Discovery:SUCCESS" in dispatched_events
    assert "SKIPPED:5:Throughput:Gateway unreachable" in dispatched_events
    assert "FAILED:4:DNS Resolution:Resolver timeout" in dispatched_events


def test_diagnostic_service_completion_triggers_diagnosis_engine(
    qapp: QApplication, test_db: DatabaseManager
) -> None:
    """Verify RUN_COMPLETED event halts polling, invokes DiagnosisEngine, and emits signals."""
    service = DiagnosticService(db=test_db)

    # Seed a test run in SQLite
    run_repo = RunRepository(test_db)
    test_run = TestRun(
        run_id="run-eval-test",
        start_time_utc=now_utc_iso(),
        end_time_utc=now_utc_iso(),
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
    )
    run_repo.create_run(test_run)

    completed_signals: List[tuple] = []
    finished_signals: List[tuple] = []
    service.sig_diagnosis_completed.connect(lambda run_id, count: completed_signals.append((run_id, count)))
    service.sig_run_finished.connect(lambda run_id, status, msg: finished_signals.append((run_id, status, msg)))

    # Mock DiagnosisEngine.evaluate_run to return 2 dummy findings
    dummy_findings = [
        Finding(
            finding_id="f-1",
            run_id="run-eval-test",
            category=FindingCategory.LOCAL_NETWORK,
            priority=FindingPriority.CRITICAL,
            rule_id="R-GW-01",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            plain_summary="Gateway Unreachable",
            limitations="None",
            recommended_action="Restart router",
        ),
        Finding(
            finding_id="f-2",
            run_id="run-eval-test",
            category=FindingCategory.INCONCLUSIVE,
            priority=FindingPriority.INFO,
            rule_id="R-INCONCLUSIVE-01",
            rule_version="1.0.0",
            status=FindingStatus.INSUFFICIENT_EVIDENCE,
            evidence_strength=EvidenceStrength.INCONCLUSIVE,
            plain_summary="Inconclusive Telemetry",
            limitations="Single target failed",
            recommended_action="Retest",
        ),
    ]
    service.diagnosis_engine.evaluate_run = mock.MagicMock(return_value=dummy_findings)

    # Push RUN_COMPLETED event
    service.worker.event_queue.put(
        WorkflowEvent(
            event_type=WorkflowEventType.RUN_COMPLETED,
            run_id="run-eval-test",
            status="COMPLETED",
            message="All stages completed",
        )
    )

    service._poll_events()

    # DiagnosisEngine.evaluate_run must have been invoked with run_id and db
    service.diagnosis_engine.evaluate_run.assert_called_once_with(
        run_id="run-eval-test", db=test_db
    )
    assert len(completed_signals) == 1
    assert completed_signals[0] == ("run-eval-test", 2)

    assert len(finished_signals) == 1
    assert finished_signals[0] == ("run-eval-test", "COMPLETED", "All stages completed")


def test_diagnostic_service_cancel_and_shutdown(
    qapp: QApplication, test_db: DatabaseManager
) -> None:
    """Verify cancellation calls worker.cancel() and shutdown joins worker cleanly."""
    service = DiagnosticService(db=test_db)
    service.worker.cancel = mock.MagicMock()
    service.worker.join = mock.MagicMock(return_value=True)

    # Not running -> cancel does nothing
    service.cancel_diagnosis()
    service.worker.cancel.assert_not_called()

    # Running -> cancel invokes worker.cancel
    service.worker._is_running = True
    service.worker._active_run_id = "run-active"
    mock_th = mock.MagicMock()
    mock_th.is_alive.return_value = True
    service.worker._thread = mock_th

    service.cancel_diagnosis()
    service.worker.cancel.assert_called_once()

    # Shutdown joins worker
    res = service.shutdown(timeout_s=1.0)
    assert res is True
    service.worker.join.assert_called_once_with(timeout_s=1.0)


# ============================================================================
# HistoryService Tests
# ============================================================================

def test_history_service_queries(test_db: DatabaseManager) -> None:
    """Verify HistoryService retrieves real runs, stages, summaries, and handles empty state."""
    service = HistoryService(db=test_db)

    # 1. Empty database returns empty list without error
    assert service.list_runs() == []
    assert service.get_run("non-existent") is None
    assert service.get_run_detail("non-existent") is None

    # 2. Seed runs in SQLite
    run_repo = RunRepository(test_db)
    run1 = TestRun(
        run_id="run-001",
        start_time_utc="2026-10-24T08:00:00Z",
        end_time_utc="2026-10-24T08:00:30Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi",
    )
    run2 = TestRun(
        run_id="run-002",
        start_time_utc="2026-10-24T09:00:00Z",
        end_time_utc="2026-10-24T09:00:25Z",
        status=RunStatus.FAILED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="10.0.0.1",
        interface_name="Ethernet",
    )
    run_repo.create_run(run1)
    run_repo.create_run(run2)

    # Seed stages for run2
    s1 = TestStage(
        stage_id=new_id(),
        run_id="run-002",
        stage_name="Gateway Discovery",
        stage_order=1,
        start_time_utc="2026-10-24T09:00:00Z",
        status=StageStatus.SUCCESS,
    )
    s2 = TestStage(
        stage_id=new_id(),
        run_id="run-002",
        stage_name="Gateway Reachability",
        stage_order=2,
        start_time_utc="2026-10-24T09:00:05Z",
        status=StageStatus.FAILED,
        error_message="100% loss",
    )
    run_repo.create_stage(s1)
    run_repo.create_stage(s2)

    # List runs in reverse chronological order (run2 first, then run1)
    runs = service.list_runs()
    assert len(runs) == 2
    assert runs[0].run_id == "run-002"
    assert runs[1].run_id == "run-001"

    # Get stages for run2
    stages = service.get_stages_for_run("run-002")
    assert len(stages) == 2
    assert stages[0].stage_name == "Gateway Discovery"
    assert stages[1].status == StageStatus.FAILED

    # Get full historical detail
    detail = service.get_run_detail("run-002")
    assert detail is not None
    assert detail.run.run_id == "run-002"
    assert len(detail.stages) == 2

    # Search runs
    assert len(service.search_runs("Wi-Fi")) == 1
    assert service.search_runs("Wi-Fi")[0].run_id == "run-001"
    assert len(service.search_runs("10.0.0.1")) == 1
    assert service.search_runs("10.0.0.1")[0].run_id == "run-002"
    assert len(service.search_runs("FAILED")) == 1


# ============================================================================
# FindingsService Tests
# ============================================================================

def test_findings_service(test_db: DatabaseManager) -> None:
    """Verify FindingsService retrieves findings, evidence, and recommendations."""
    service = FindingsService(db=test_db)

    # 1. Empty state
    assert service.get_findings_for_run("empty-run") == []
    assert service.get_recommendations_for_run("empty-run") == []

    # 2. Seed a run, finding, target, observation, and evidence
    run_repo = RunRepository(test_db)
    finding_repo = FindingRepository(test_db)
    obs_repo = ObservationRepository(test_db)

    run = TestRun(
        run_id="run-find-1",
        start_time_utc=now_utc_iso(),
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
    )
    run_repo.create_run(run)

    obs = Observation(
        observation_id="obs-gw-fail",
        run_id="run-find-1",
        sample_index=1,
        timestamp_utc=now_utc_iso(),
        duration_ms=1000.0,
        metric_name="gateway_ping",
        unit="ms",
        status=MeasurementStatus.TIMEOUT,
        value_numeric=None,
    )
    obs_repo.create_observation(obs)

    finding = Finding(
        finding_id="f-gw-01",
        run_id="run-find-1",
        category=FindingCategory.LOCAL_NETWORK,
        priority=FindingPriority.CRITICAL,
        rule_id="R-GW-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Default Gateway Unreachable",
        limitations="Wi-Fi link state could not be read unprivileged",
        recommended_action="Restart router",
    )
    finding_repo.create_finding(finding)

    evidence = FindingEvidence(
        evidence_id="ev-1",
        finding_id="f-gw-01",
        relationship_type=EvidenceRelationship.SUPPORTING,
        observation_id="obs-gw-fail",
    )
    finding_repo.link_evidence(evidence)

    # Test retrieval
    findings = service.get_findings_for_run("run-find-1")
    assert len(findings) == 1
    assert findings[0].rule_id == "R-GW-01"
    assert findings[0].priority == FindingPriority.CRITICAL

    ev_list = service.get_evidence_for_finding("f-gw-01")
    assert len(ev_list) == 1
    assert ev_list[0].observation_id == "obs-gw-fail"

    # Test resolved evidence details
    ev_details = service.get_evidence_details("f-gw-01")
    assert len(ev_details) == 1
    assert ev_details[0]["type"] == "OBSERVATION"
    assert ev_details[0]["metric_name"] == "gateway_ping"
    assert ev_details[0]["status"] == "TIMEOUT"
    assert ev_details[0]["value_numeric"] is None  # Honesty check: missing metric is None

    # Test recommendations generation
    recs = service.get_recommendations_for_run("run-find-1")
    assert len(recs) == 1
    assert recs[0].rule_id == "R-GW-01"
    assert len(recs[0].user_actions) > 0
    assert len(recs[0].technician_actions) > 0


# ============================================================================
# ReportingService Tests
# ============================================================================

def test_reporting_service_export_and_preview(tmp_path: Path, test_db: DatabaseManager) -> None:
    """Verify ReportingService exports JSON, CSV, and Markdown adhering to Phase 9 rules."""
    service = ReportingService(db=test_db)

    # Seed run, observation, finding
    run_repo = RunRepository(test_db)
    obs_repo = ObservationRepository(test_db)
    finding_repo = FindingRepository(test_db)

    run = TestRun(
        run_id="run-rep-1",
        start_time_utc="2026-10-24T10:00:00Z",
        end_time_utc="2026-10-24T10:00:30Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
    )
    run_repo.create_run(run)

    obs = Observation(
        observation_id="obs-ping-1",
        run_id="run-rep-1",
        sample_index=1,
        timestamp_utc="2026-10-24T10:00:05Z",
        duration_ms=12.5,
        metric_name="round_trip_time",
        unit="ms",
        status=MeasurementStatus.SUCCESS,
        value_numeric=12.5,
    )
    obs_repo.create_observation(obs)

    finding = Finding(
        finding_id="f-ok-1",
        run_id="run-rep-1",
        category=FindingCategory.LOCAL_NETWORK,
        priority=FindingPriority.INFO,
        rule_id="R-LOCAL-01",
        rule_version="1.0.0",
        status=FindingStatus.CONTRADICTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Local network path healthy",
        limitations="None",
        recommended_action="None",
    )
    finding_repo.create_finding(finding)

    # 1. Preview Generation (Markdown, JSON, CSV)
    md_preview = service.get_export_preview(run_id="run-rep-1", export_format=ExportFormat.MARKDOWN)
    assert "# TicNet Diagnostic Report" in md_preview
    assert "run-rep-1" in md_preview

    json_preview = service.get_export_preview(run_id="run-rep-1", export_format=ExportFormat.JSON)
    assert '"run_id": "run-rep-1"' in json_preview

    csv_preview = service.get_export_preview(run_id="run-rep-1", export_format=ExportFormat.CSV)
    assert "round_trip_time" in csv_preview

    # 2. File Export to Disk
    export_path = tmp_path / "test_report.md"
    export_record = service.export_report(
        run_id="run-rep-1",
        export_format=ExportFormat.MARKDOWN,
        output_path=export_path,
        enable_privacy=True,
        overwrite=True,
    )

    assert export_record.export_status == ExportStatus.SUCCESS
    assert export_path.exists()
    assert export_path.stat().st_size > 0

    # 3. Export audit entry in SQLite
    audits = service.list_exports_for_run("run-rep-1")
    assert len(audits) == 1
    assert audits[0].export_format == ExportFormat.MARKDOWN


def test_desktop_services_container_factory(tmp_path: Path) -> None:
    """Verify DesktopServices.create_default constructs all adapters seamlessly."""
    db_file = tmp_path / "container_test.sqlite3"
    services = DesktopServices.create_default(db_path=db_file, run_migrations=True)

    assert services.db is not None
    assert services.config is not None
    assert services.diagnostic is not None
    assert services.history is not None
    assert services.findings is not None
    assert services.reporting is not None
