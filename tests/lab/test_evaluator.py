"""Tests for Automated Laboratory Diagnostic Evaluator.

Verifies deterministic outcome classification:
- MATCH: Engine fired expected rule and category matching ground truth.
- MISMATCH: Engine fired wrong rule or zero findings.
- INCONCLUSIVE: Engine emitted inconclusive findings when a specific fault was injected.
- Serialization via to_dict().
"""

from __future__ import annotations

import pytest

from ticnet.models.entities import Finding, TestRun
from ticnet.models.enums import (
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    LabScenario,
    RunMode,
    RunStatus,
    TrialCleanupStatus,
    TrialVerificationStatus,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from lab.evaluator import LabEvaluator
from lab.ground_truth import LabGroundTruthManager


@pytest.fixture
def temp_db(tmp_path):
    """Provide temporary test SQLite database with initial schema applied."""
    db_file = tmp_path / "test_lab_evaluator.db"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


class TestLabEvaluator:
    """Verifies LabEvaluator evaluation logic and trial record synchronization."""

    def test_evaluate_match_outcome(self, temp_db: DatabaseManager) -> None:
        """Verify MATCH is returned when engine finding matches expected rule and category."""
        run_repo = RunRepository(temp_db)
        finding_repo = FindingRepository(temp_db)
        gt_mgr = LabGroundTruthManager(temp_db)
        evaluator = LabEvaluator(temp_db)

        # 1. Create Run & Quarantined Ground Truth
        run_id = "run-eval-001"
        run_repo.create_run(TestRun(run_id=run_id, start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        trial = gt_mgr.record_trial(
            run_id=run_id,
            scenario_id=LabScenario.SCENARIO_A_GW,
            ground_truth_label="Gateway Unreachable",
            injection_mechanism="test",
            injection_parameters={},
        )

        # 2. Persist matching finding from engine
        finding = Finding(
            finding_id="f-001",
            run_id=run_id,
            rule_id="R-GW-01",
            rule_version="1.0.0",
            category=FindingCategory.LOCAL_NETWORK,
            priority=FindingPriority.CRITICAL,
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            plain_summary="Gateway unreachable",
            limitations="Local probe only",
            recommended_action="Check router power",
        )
        finding_repo.create_finding(finding)

        # 3. Evaluate
        result = evaluator.evaluate_trial(
            trial_id=trial.trial_id,
            expected_rule_id="R-GW-01",
            expected_category=FindingCategory.LOCAL_NETWORK,
            cleanup_verified=True,
        )

        assert result.matched is True
        assert result.verification_status == TrialVerificationStatus.MATCH
        assert result.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED
        assert "Ground truth match" in result.explanation

        # Check DB update
        persisted_trial = gt_mgr.get_trial(trial.trial_id)
        assert persisted_trial is not None
        assert persisted_trial.verification_status == TrialVerificationStatus.MATCH

    def test_evaluate_mismatch_outcome(self, temp_db: DatabaseManager) -> None:
        """Verify MISMATCH is returned when engine emits conflicting or unexpected rule."""
        run_repo = RunRepository(temp_db)
        finding_repo = FindingRepository(temp_db)
        gt_mgr = LabGroundTruthManager(temp_db)
        evaluator = LabEvaluator(temp_db)

        run_id = "run-eval-002"
        run_repo.create_run(TestRun(run_id=run_id, start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        trial = gt_mgr.record_trial(
            run_id=run_id,
            scenario_id=LabScenario.SCENARIO_B_DNS,
            ground_truth_label="DNS Failure",
            injection_mechanism="test",
            injection_parameters={},
        )

        # Engine emitted R-LOCAL-01 instead of R-DNS-01
        finding = Finding(
            finding_id="f-002",
            run_id=run_id,
            rule_id="R-LOCAL-01",
            rule_version="1.0.0",
            category=FindingCategory.LOCAL_NETWORK,
            priority=FindingPriority.WARNING,
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.MODERATE,
            plain_summary="Local latency degradation",
            limitations="Single target",
            recommended_action="Check Wi-Fi",
        )
        finding_repo.create_finding(finding)

        result = evaluator.evaluate_trial(
            trial_id=trial.trial_id,
            expected_rule_id="R-DNS-01",
            expected_category=FindingCategory.DNS_SUBSYSTEM,
            cleanup_verified=True,
        )

        assert result.matched is False
        assert result.verification_status == TrialVerificationStatus.MISMATCH
        assert "Diagnostic mismatch" in result.explanation

    def test_evaluate_inconclusive_outcome(self, temp_db: DatabaseManager) -> None:
        """Verify INCONCLUSIVE is returned when engine fails to detect fault and emits INCONCLUSIVE."""
        run_repo = RunRepository(temp_db)
        finding_repo = FindingRepository(temp_db)
        gt_mgr = LabGroundTruthManager(temp_db)
        evaluator = LabEvaluator(temp_db)

        run_id = "run-eval-003"
        run_repo.create_run(TestRun(run_id=run_id, start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        trial = gt_mgr.record_trial(
            run_id=run_id,
            scenario_id=LabScenario.SCENARIO_C_LOSS,
            ground_truth_label="Multi-Target Loss",
            injection_mechanism="test",
            injection_parameters={},
        )

        # Engine emitted R-INCONCLUSIVE-01 instead of expected R-LOSS-01
        finding = Finding(
            finding_id="f-003",
            run_id=run_id,
            rule_id="R-INCONCLUSIVE-01",
            rule_version="1.0.0",
            category=FindingCategory.INCONCLUSIVE,
            priority=FindingPriority.INFO,
            status=FindingStatus.INSUFFICIENT_EVIDENCE,
            evidence_strength=EvidenceStrength.INCONCLUSIVE,
            plain_summary="Inconclusive evidence",
            limitations="Conflicting probes",
            recommended_action="Rerun diagnosis",
        )
        finding_repo.create_finding(finding)

        result = evaluator.evaluate_trial(
            trial_id=trial.trial_id,
            expected_rule_id="R-LOSS-01",
            expected_category=FindingCategory.EXTERNAL_PATH,
            cleanup_verified=True,
        )

        assert result.matched is False
        assert result.verification_status == TrialVerificationStatus.INCONCLUSIVE
        assert "Inconclusive outcome" in result.explanation

    def test_evaluation_result_to_dict(self, temp_db: DatabaseManager) -> None:
        """Verify serialization of EvaluationResult to dict."""
        run_repo = RunRepository(temp_db)
        gt_mgr = LabGroundTruthManager(temp_db)
        evaluator = LabEvaluator(temp_db)

        run_id = "run-eval-004"
        run_repo.create_run(TestRun(run_id=run_id, start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        trial = gt_mgr.record_trial(
            run_id=run_id,
            scenario_id=LabScenario.SCENARIO_D_LOAD,
            ground_truth_label="Bufferbloat",
            injection_mechanism="test",
            injection_parameters={},
        )

        result = evaluator.evaluate_trial(
            trial_id=trial.trial_id,
            expected_rule_id="R-LOAD-01",
            expected_category=FindingCategory.BUFFERBLOAT,
            cleanup_verified=True,
        )

        d = result.to_dict()
        assert d["trial_id"] == trial.trial_id
        assert d["run_id"] == run_id
        assert d["scenario_id"] == "SCENARIO_D_LOAD"
        assert d["expected_rule_id"] == "R-LOAD-01"
        assert d["expected_category"] == "BUFFERBLOAT"
        assert "actual_rules_emitted" in d
