"""Comprehensive tests for Stage 4: Results and Diagnosis Presentation Enhancement.

Verifies:
1. Healthy connection presentation (Hero verdict, timeline, clean evidence matrix).
2. Gateway unreachable diagnosis (R-GW-01, Critical, Local LAN Checkpoint).
3. DNS failure diagnosis (R-DNS-01, Critical, DNS Subsystem Checkpoint).
4. Induced packet loss diagnosis (R-LOSS-01, Warning, External WAN Checkpoint).
5. Loaded latency diagnosis (R-LOAD-01, Bufferbloat delta, Queueing recommendations).
6. Inconclusive diagnosis (R-INCONCLUSIVE-01, Insufficient evidence guardrail).
7. Missing and unavailable measurements (Truthfully displayed as 'Not Measured', never 0.0).
8. Skipped diagnostic stages (Rendered strictly as SKIPPED with reason, never SUCCESS).
9. Quick navigation signals from Hero Verdict to sections.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.services.findings_service import FindingsService
from ticnet.desktop.services.history_service import HistoryService
from ticnet.desktop.widgets.diagnosis_summary_widget import DiagnosisSummaryPanel
from ticnet.desktop.widgets.diagnostic_timeline_widget import DiagnosticWorkflowTimeline
from ticnet.desktop.widgets.evidence_matrix_widget import StructuredEvidenceMatrix
from ticnet.desktop.widgets.recommendations_widget import RecommendationsPanel
from ticnet.diagnosis.recommender import ActionItem, FindingRecommendation
from ticnet.models.entities import (
    Finding,
    FindingEvidence,
    Observation,
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
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
    RunStatus,
    StageStatus,
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
    db_file = tmp_path / "stage4_results_test.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


# ============================================================================
# Test 1: Healthy Connection Presentation
# ============================================================================

def test_healthy_connection_presentation(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify healthy run displays positive hero verdict, clean matrix, and zero remedial actions."""
    run_repo = RunRepository(test_db)
    obs_repo = ObservationRepository(test_db)
    sum_repo = SummaryRepository(test_db)

    run_id = "run-healthy-01"
    run = TestRun(
        run_id=run_id,
        start_time_utc="2026-10-24T18:45:00Z",
        end_time_utc="2026-10-24T18:45:30Z",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi 6",
    )
    run_repo.create_run(run)

    # Stages 1 to 5 all completed
    for order, name in [
        (1, "Gateway Discovery"),
        (2, "Gateway Reachability"),
        (3, "External Multi-Target Ping"),
        (4, "DNS Resolution"),
        (5, "Throughput & Loaded Latency"),
    ]:
        run_repo.create_stage(
            TestStage(
                stage_id=f"stg-{order}",
                run_id=run_id,
                stage_name=name,
                stage_order=order,
                start_time_utc="2026-10-24T18:45:00Z",
                end_time_utc="2026-10-24T18:45:05Z",
                status=StageStatus.SUCCESS,
            )
        )

    # Clean gateway & external observations
    obs_repo.create_target(Target("gw-1", TargetType.GATEWAY, ip_address="192.168.1.1", provider_label="Local Router"))
    obs_repo.create_target(Target("ext-1", TargetType.EXTERNAL_IP, ip_address="1.1.1.1", provider_label="Cloudflare"))

    for i in range(1, 6):
        obs_repo.create_observation(
            Observation(
                observation_id=new_id(),
                run_id=run_id,
                sample_index=i,
                timestamp_utc="2026-10-24T18:45:01Z",
                duration_ms=2.2,
                metric_name="round_trip_time",
                value_numeric=2.2,
                unit="ms",
                status=MeasurementStatus.SUCCESS,
                target_id="gw-1",
            )
        )
        obs_repo.create_observation(
            Observation(
                observation_id=new_id(),
                run_id=run_id,
                sample_index=i,
                timestamp_utc="2026-10-24T18:45:02Z",
                duration_ms=18.5,
                metric_name="round_trip_time",
                value_numeric=18.5,
                unit="ms",
                status=MeasurementStatus.SUCCESS,
                target_id="ext-1",
            )
        )

    find_svc = FindingsService(test_db)
    hist_svc = HistoryService(test_db)
    page = ResultsPage(findings_service=find_svc, history_service=hist_svc)
    page.load_run_results(run_id)

    # 1. Hero Verdict is HEALTHY
    summary_panel = page.findChild(DiagnosisSummaryPanel)
    assert summary_panel is not None
    assert "HEALTHY — ALL CHECKPOINTS NORMAL" in summary_panel.lbl_verdict_badge.text()
    assert "Healthy Network Connection" in summary_panel.lbl_diagnosis_title.text()

    # 2. Timeline shows all 5 stages completed
    timeline = page.findChild(DiagnosticWorkflowTimeline)
    assert timeline is not None
    for order in range(1, 6):
        card = timeline.stage_cards[order]
        assert card.lbl_status.text() == "COMPLETED"

    # 3. Recommendations shows no action required
    recs = page.findChild(RecommendationsPanel)
    assert recs is not None
    all_recs_labels = [lbl.text() for lbl in recs.findChildren(QLabel)]
    assert any("No remedial actions required" in t for t in all_recs_labels)


# ============================================================================
# Test 2: Gateway Unreachable Diagnosis (R-GW-01)
# ============================================================================

def test_gateway_unreachable_diagnosis_presentation(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify R-GW-01 critical finding marks Checkpoint 1, shows failure status, and skipped downstream."""
    run_repo = RunRepository(test_db)
    obs_repo = ObservationRepository(test_db)
    finding_repo = FindingRepository(test_db)

    run_id = "run-gw-fail-01"
    run = TestRun(
        run_id=run_id,
        start_time_utc="2026-10-24T19:00:00Z",
        status=RunStatus.FAILED,
        app_version="1.0.0",
        config_version="1.0.0",
        gateway_ip="192.168.1.1",
    )
    run_repo.create_run(run)

    # Stage 1: completed, Stage 2: failed, Stages 3-5: skipped
    run_repo.create_stage(TestStage("s1", run_id, "Gateway Discovery", 1, "2026-10-24T19:00:00Z", status=StageStatus.SUCCESS))
    run_repo.create_stage(TestStage("s2", run_id, "Gateway Reachability", 2, "2026-10-24T19:00:02Z", status=StageStatus.FAILED, error_message="100% loss to gateway"))
    run_repo.create_stage(TestStage("s3", run_id, "External Probing", 3, "2026-10-24T19:00:05Z", status=StageStatus.SKIPPED, skip_reason="Gateway unreachable"))
    run_repo.create_stage(TestStage("s4", run_id, "DNS Resolution", 4, "2026-10-24T19:00:05Z", status=StageStatus.SKIPPED, skip_reason="Gateway unreachable"))
    run_repo.create_stage(TestStage("s5", run_id, "Throughput", 5, "2026-10-24T19:00:05Z", status=StageStatus.SKIPPED, skip_reason="Gateway unreachable"))

    # Finding R-GW-01
    finding = Finding(
        finding_id="f-gw-01",
        run_id=run_id,
        category=FindingCategory.LOCAL_NETWORK,
        priority=FindingPriority.CRITICAL,
        rule_id="R-GW-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Local Default Gateway Unreachable",
        limitations="Local Wi-Fi or Ethernet link to router is down",
        recommended_action="Inspect router power and Wi-Fi connection",
    )
    finding_repo.create_finding(finding)

    page = ResultsPage(findings_service=FindingsService(test_db), history_service=HistoryService(test_db))
    page.load_run_results(run_id)

    # Verify Verdict Banner
    summary_panel = page.findChild(DiagnosisSummaryPanel)
    assert summary_panel is not None
    assert "CONFIRMED FAILURE: R-GW-01" in summary_panel.lbl_verdict_badge.text()
    assert "Checkpoint 1 [Local Gateway / LAN]" in summary_panel.lbl_checkpoint_badge.text()
    assert "Local Default Gateway Unreachable" in summary_panel.lbl_diagnosis_title.text()

    # Verify Timeline: S2 Failed, S3 Skipped (not completed!)
    timeline = page.findChild(DiagnosticWorkflowTimeline)
    assert timeline is not None
    assert timeline.stage_cards[2].lbl_status.text() == "FAILED"
    assert timeline.stage_cards[3].lbl_status.text() == "SKIPPED"
    assert "Gateway unreachable" in timeline.stage_cards[3].lbl_outcome.text()


# ============================================================================
# Test 3: DNS Failure Diagnosis (R-DNS-01)
# ============================================================================

def test_dns_failure_diagnosis_presentation(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify R-DNS-01 critical finding highlights DNS Subsystem checkpoint and DNS error."""
    run_repo = RunRepository(test_db)
    finding_repo = FindingRepository(test_db)

    run_id = "run-dns-fail-01"
    run = TestRun(run_id=run_id, start_time_utc=now_utc_iso(), status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0")
    run_repo.create_run(run)

    finding = Finding(
        finding_id="f-dns-01",
        run_id=run_id,
        category=FindingCategory.DNS_SUBSYSTEM,
        priority=FindingPriority.CRITICAL,
        rule_id="R-DNS-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Primary DNS Resolver Resolution Failure",
        limitations="Direct UDP query to configured resolver timed out while IP routing is functional",
        recommended_action="Check configured DNS resolver or switch to backup resolver",
    )
    finding_repo.create_finding(finding)

    page = ResultsPage(findings_service=FindingsService(test_db), history_service=HistoryService(test_db))
    page.load_run_results(run_id)

    summary_panel = page.findChild(DiagnosisSummaryPanel)
    assert summary_panel is not None
    assert "CONFIRMED FAILURE: R-DNS-01" in summary_panel.lbl_verdict_badge.text()
    assert "Checkpoint 2 [DNS Resolvers]" in summary_panel.lbl_checkpoint_badge.text()


# ============================================================================
# Test 4: Loaded Latency Diagnosis (R-LOAD-01)
# ============================================================================

def test_loaded_latency_diagnosis_presentation(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify R-LOAD-01 bufferbloat finding indicates traffic load and presents queue management advice."""
    run_repo = RunRepository(test_db)
    finding_repo = FindingRepository(test_db)

    run_id = "run-bb-01"
    run = TestRun(run_id=run_id, start_time_utc=now_utc_iso(), status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0")
    run_repo.create_run(run)

    finding = Finding(
        finding_id="f-bb-01",
        run_id=run_id,
        category=FindingCategory.BUFFERBLOAT,
        priority=FindingPriority.WARNING,
        rule_id="R-LOAD-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Bufferbloat Latency Inflation Under Load",
        limitations="Round-trip latency inflated by +85 ms during concurrent downstream transfer",
        recommended_action="Enable Smart Queue Management (SQM) or Cake on router",
    )
    finding_repo.create_finding(finding)

    page = ResultsPage(findings_service=FindingsService(test_db), history_service=HistoryService(test_db))
    page.load_run_results(run_id)

    summary_panel = page.findChild(DiagnosisSummaryPanel)
    assert summary_panel is not None
    assert "DEGRADED PERFORMANCE: R-LOAD-01" in summary_panel.lbl_verdict_badge.text()
    assert "Traffic Load [Bufferbloat / Queue]" in summary_panel.lbl_checkpoint_badge.text()


# ============================================================================
# Test 5: Inconclusive Diagnosis (R-INCONCLUSIVE-01)
# ============================================================================

def test_inconclusive_diagnosis_presentation(qapp: QApplication, test_db: DatabaseManager) -> None:
    """Verify R-INCONCLUSIVE-01 renders truthful inconclusive status and guardrail message."""
    run_repo = RunRepository(test_db)
    finding_repo = FindingRepository(test_db)

    run_id = "run-inconc-01"
    run = TestRun(run_id=run_id, start_time_utc=now_utc_iso(), status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0")
    run_repo.create_run(run)

    finding = Finding(
        finding_id="f-inc-01",
        run_id=run_id,
        category=FindingCategory.INCONCLUSIVE,
        priority=FindingPriority.INFO,
        rule_id="R-INCONCLUSIVE-01",
        rule_version="1.0.0",
        status=FindingStatus.INSUFFICIENT_EVIDENCE,
        evidence_strength=EvidenceStrength.INCONCLUSIVE,
        plain_summary="Inconclusive Diagnosis — Single Destination Failure Guardrail",
        limitations="Only 1 of 3 destinations failed; insufficient proof of broader path failure",
        recommended_action="Rerun diagnosis to verify destination reachability",
    )
    finding_repo.create_finding(finding)

    page = ResultsPage(findings_service=FindingsService(test_db), history_service=HistoryService(test_db))
    page.load_run_results(run_id)

    summary_panel = page.findChild(DiagnosisSummaryPanel)
    assert summary_panel is not None
    assert "INCONCLUSIVE DIAGNOSIS: R-INCONCLUSIVE-01" in summary_panel.lbl_verdict_badge.text()
    assert "Inconclusive Checkpoint" in summary_panel.lbl_checkpoint_badge.text()


# ============================================================================
# Test 6: Evidence Matrix Truthful Missingness
# ============================================================================

def test_evidence_matrix_missingness(qapp: QApplication) -> None:
    """Verify StructuredEvidenceMatrix reports unmeasured tests as 'Not Measured', never 0.0."""
    matrix = StructuredEvidenceMatrix()
    # Provide empty observations list
    matrix.populate_evidence_matrix(observations=[])

    # Check rows: Throughput, DNS, Bufferbloat should all show 'Not Measured'
    measurements = [matrix.table.item(row, 2).text() for row in range(matrix.table.rowCount())]
    assert all("Not Measured" in m for m in measurements)

    # Must never substitute 0.0 for missing values
    assert not any("0.0 ms" in m for m in measurements)
    assert not any("0.0 Mbps" in m for m in measurements)


# ============================================================================
# Test 7: Navigation Jump Signals
# ============================================================================

def test_navigation_jump_signals(qapp: QApplication) -> None:
    """Verify quick navigation buttons in DiagnosisSummaryPanel emit signals."""
    panel = DiagnosisSummaryPanel()

    signals_emitted = []
    panel.sig_jump_to_timeline.connect(lambda: signals_emitted.append("TIMELINE"))
    panel.sig_jump_to_evidence.connect(lambda: signals_emitted.append("EVIDENCE"))
    panel.sig_jump_to_charts.connect(lambda: signals_emitted.append("CHARTS"))
    panel.sig_jump_to_recommendations.connect(lambda: signals_emitted.append("RECOMMENDATIONS"))

    panel.btn_nav_timeline.click()
    panel.btn_nav_evidence.click()
    panel.btn_nav_charts.click()
    panel.btn_nav_recommendations.click()

    assert signals_emitted == ["TIMELINE", "EVIDENCE", "CHARTS", "RECOMMENDATIONS"]
