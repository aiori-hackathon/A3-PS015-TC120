"""Independent Laboratory Ground Truth Manager and Quarantine Assertion.

Ensures ground truth records are recorded independently in the SQLite lab_trials table
and strictly quarantined from the operational DiagnosisEngine.

Reference: 09_LAB_SAFETY_RUNBOOK.md, SRS-F-016, TC-LAB-06, AC-LAB-06
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from ticnet.models.entities import LabTrial
from ticnet.models.enums import LabScenario, TrialCleanupStatus, TrialVerificationStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.lab_repo import LabTrialRepository


class LabGroundTruthManager:
    """Manages creation, retrieval, and quarantine of lab trial ground truth."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.lab_repo = LabTrialRepository(db)

    def record_trial(
        self,
        run_id: str,
        scenario_id: LabScenario,
        ground_truth_label: str,
        injection_mechanism: str,
        injection_parameters: Dict[str, Any],
        operator_notes: Optional[str] = None,
        trial_id: Optional[str] = None,
    ) -> LabTrial:
        """Record an independent laboratory ground truth record in SQLite.

        This record is stored in lab_trials and is strictly quarantined from
        the operational diagnosis engine.
        """
        trial = LabTrial(
            trial_id=trial_id or f"trial-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            scenario_id=scenario_id,
            ground_truth_label=ground_truth_label,
            injection_mechanism=injection_mechanism,
            injection_parameters=json.dumps(injection_parameters, sort_keys=True),
            verification_status=TrialVerificationStatus.INCONCLUSIVE,  # Initial state prior to evaluation
            cleanup_status=TrialCleanupStatus.CLEANED_VERIFIED,
            operator_notes=operator_notes or "Recorded by LabGroundTruthManager",
        )
        return self.lab_repo.create_trial(trial)

    def get_trial(self, trial_id: str) -> Optional[LabTrial]:
        """Retrieve trial by trial_id."""
        return self.lab_repo.get_trial(trial_id)

    def get_trial_for_run(self, run_id: str) -> Optional[LabTrial]:
        """Retrieve trial for specific run_id."""
        return self.lab_repo.get_trial_for_run(run_id)

    def list_all_trials(self) -> List[LabTrial]:
        """List all recorded lab trials."""
        return self.lab_repo.list_trials()

    def update_trial_status(
        self,
        trial_id: str,
        verification_status: TrialVerificationStatus,
        cleanup_status: TrialCleanupStatus,
        operator_notes: Optional[str] = None,
    ) -> None:
        """Update verification and cleanup status following evaluator audit."""
        sql = """
            UPDATE lab_trials
            SET verification_status = ?, cleanup_status = ?, operator_notes = COALESCE(?, operator_notes)
            WHERE trial_id = ?;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    verification_status.value,
                    cleanup_status.value,
                    operator_notes,
                    trial_id,
                ),
            )

    @staticmethod
    def assert_quarantine_integrity() -> bool:
        """TC-LAB-06 / AC-LAB-06: Assert that operational DiagnosisEngine cannot access lab_trials.

        Inspects the DiagnosisEngine source and queries to confirm zero references
        to the lab_trials table or LabTrialRepository.
        """
        import inspect
        from ticnet.diagnosis import engine

        source = inspect.getsource(engine.DiagnosisEngine)
        if "lab_trials" in source or "LabTrialRepository" in source:
            raise AssertionError(
                "CRITICAL SECURITY VIOLATION: DiagnosisEngine contains references to lab_trials. "
                "Quarantine isolation is broken!"
            )
        return True
