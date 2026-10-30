"""Repository for LabTrial persistence.

QUARANTINE NOTICE:
This repository manages ground truth records for the isolated laboratory testbed.
The operational DiagnosisEngine must NEVER query or access this repository.
"""

import sqlite3
from typing import List, Optional

from ticnet.models.entities import LabTrial
from ticnet.models.enums import LabScenario, TrialVerificationStatus, TrialCleanupStatus
from ticnet.persistence.database import DatabaseManager


class LabTrialRepository:
    """Manages persistence of laboratory trial ground-truth records."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_trial(self, trial: LabTrial) -> LabTrial:
        """Persist a new laboratory trial record."""
        sql = """
            INSERT INTO lab_trials (
                trial_id, run_id, scenario_id, ground_truth_label,
                injection_mechanism, injection_parameters, verification_status,
                cleanup_status, operator_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    trial.trial_id,
                    trial.run_id,
                    trial.scenario_id.value,
                    trial.ground_truth_label,
                    trial.injection_mechanism,
                    trial.injection_parameters,
                    trial.verification_status.value,
                    trial.cleanup_status.value,
                    trial.operator_notes,
                ),
            )
        return trial

    def get_trial(self, trial_id: str) -> Optional[LabTrial]:
        """Retrieve a lab trial by its identifier."""
        sql = "SELECT * FROM lab_trials WHERE trial_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (trial_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_trial(row)

    def get_trial_for_run(self, run_id: str) -> Optional[LabTrial]:
        """Retrieve the lab trial associated with a specific test run."""
        sql = "SELECT * FROM lab_trials WHERE run_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_trial(row)

    def list_trials(self) -> List[LabTrial]:
        """List all recorded lab trials."""
        sql = "SELECT * FROM lab_trials ORDER BY trial_id ASC;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql)
            return [self._row_to_trial(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_trial(row: sqlite3.Row) -> LabTrial:
        return LabTrial(
            trial_id=row["trial_id"],
            run_id=row["run_id"],
            scenario_id=LabScenario(row["scenario_id"]),
            ground_truth_label=row["ground_truth_label"],
            injection_mechanism=row["injection_mechanism"],
            injection_parameters=row["injection_parameters"],
            verification_status=TrialVerificationStatus(row["verification_status"]),
            cleanup_status=TrialCleanupStatus(row["cleanup_status"]),
            operator_notes=row["operator_notes"],
        )
