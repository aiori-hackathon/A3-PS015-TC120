"""Tests for Independent Laboratory Ground Truth Management and Quarantine (TC-LAB-06, AC-LAB-06).

Verifies independent persistence in SQLite lab_trials table, status updates,
retrieval, and strict quarantine integrity assertions.
"""

from __future__ import annotations

import pytest

from ticnet.models.entities import TestRun
from ticnet.models.enums import LabScenario, RunMode, RunStatus, TrialCleanupStatus, TrialVerificationStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.run_repo import RunRepository
from lab.ground_truth import LabGroundTruthManager


@pytest.fixture
def temp_db(tmp_path):
    """Provide temporary test SQLite database with initial schema applied."""
    db_file = tmp_path / "test_lab_gt.db"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


class TestLabGroundTruthManager:
    """Verifies TC-LAB-06 and AC-LAB-06 requirements."""

    def test_record_and_retrieve_trial(self, temp_db: DatabaseManager) -> None:
        """Verify ground truth records can be persisted and retrieved independently."""
        run_repo = RunRepository(temp_db)
        run_repo.create_run(TestRun(run_id="run-test-001", start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        mgr = LabGroundTruthManager(temp_db)

        trial = mgr.record_trial(
            run_id="run-test-001",
            scenario_id=LabScenario.SCENARIO_A_GW,
            ground_truth_label="Default Gateway Unreachable (100% Packet Loss)",
            injection_mechanism="socket_drop_or_link_down",
            injection_parameters={"packet_loss_pct": 100.0, "target_gateway_ip": "192.168.1.1"},
            operator_notes="Automated test ground truth record",
        )

        assert trial is not None
        assert trial.trial_id.startswith("trial-")
        assert trial.run_id == "run-test-001"
        assert trial.scenario_id == LabScenario.SCENARIO_A_GW
        assert trial.ground_truth_label == "Default Gateway Unreachable (100% Packet Loss)"
        assert trial.verification_status == TrialVerificationStatus.INCONCLUSIVE
        assert trial.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED

        # Retrieve by trial_id
        fetched = mgr.get_trial(trial.trial_id)
        assert fetched is not None
        assert fetched.trial_id == trial.trial_id
        assert fetched.scenario_id == LabScenario.SCENARIO_A_GW
        assert "packet_loss_pct" in fetched.injection_parameters

        # Retrieve by run_id
        fetched_run = mgr.get_trial_for_run("run-test-001")
        assert fetched_run is not None
        assert fetched_run.trial_id == trial.trial_id

    def test_update_trial_status(self, temp_db: DatabaseManager) -> None:
        """Verify verification and cleanup statuses can be updated after diagnostic evaluation."""
        run_repo = RunRepository(temp_db)
        run_repo.create_run(TestRun(run_id="run-test-002", start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        mgr = LabGroundTruthManager(temp_db)

        trial = mgr.record_trial(
            run_id="run-test-002",
            scenario_id=LabScenario.SCENARIO_B_DNS,
            ground_truth_label="DNS Resolution Failure",
            injection_mechanism="iptables_drop",
            injection_parameters={"resolver": "1.1.1.1"},
        )

        mgr.update_trial_status(
            trial_id=trial.trial_id,
            verification_status=TrialVerificationStatus.MATCH,
            cleanup_status=TrialCleanupStatus.CLEANED_VERIFIED,
            operator_notes="Evaluated: R-DNS-01 matched perfectly",
        )

        updated = mgr.get_trial(trial.trial_id)
        assert updated is not None
        assert updated.verification_status == TrialVerificationStatus.MATCH
        assert updated.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED
        assert "R-DNS-01 matched perfectly" in (updated.operator_notes or "")

    def test_list_all_trials(self, temp_db: DatabaseManager) -> None:
        """Verify all recorded trials are retrievable in audit list."""
        run_repo = RunRepository(temp_db)
        run_repo.create_run(TestRun(run_id="run-1", start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        run_repo.create_run(TestRun(run_id="run-2", start_time_utc="2026-10-24T12:00:00Z", status=RunStatus.COMPLETED, app_version="1.0.0", config_version="1.0.0", run_mode=RunMode.LAB))
        mgr = LabGroundTruthManager(temp_db)
        mgr.record_trial(
            run_id="run-1",
            scenario_id=LabScenario.SCENARIO_A_GW,
            ground_truth_label="GW failure",
            injection_mechanism="test",
            injection_parameters={},
        )
        mgr.record_trial(
            run_id="run-2",
            scenario_id=LabScenario.SCENARIO_B_DNS,
            ground_truth_label="DNS failure",
            injection_mechanism="test",
            injection_parameters={},
        )

        trials = mgr.list_all_trials()
        assert len(trials) == 2
        scenario_ids = {t.scenario_id for t in trials}
        assert LabScenario.SCENARIO_A_GW in scenario_ids
        assert LabScenario.SCENARIO_B_DNS in scenario_ids

    def test_quarantine_integrity_assertion(self) -> None:
        """TC-LAB-06 / AC-LAB-06: Verify operational engine has zero references to lab_trials."""
        assert LabGroundTruthManager.assert_quarantine_integrity() is True
