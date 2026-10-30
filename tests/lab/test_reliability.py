"""Tests for Laboratory Reliability, Repeatability, and Resource Hygiene (TC-REL-01, TC-REL-02).

Covers:
- TC-REL-01: Repeatability across repeated trials (100% deterministic diagnostic accuracy across full scenario matrix).
- TC-REL-02: Zero residual network impairment, zero database corruption, zero background worker / thread leaks.
"""

from __future__ import annotations

import threading
import pytest

from ticnet.configuration.loader import load_app_config
from ticnet.models.enums import TrialCleanupStatus, TrialVerificationStatus
from ticnet.persistence.database import DatabaseManager
from lab.runner import LabTrialRunner


@pytest.fixture
def temp_db(tmp_path):
    """Provide temporary test SQLite database with initial schema applied."""
    db_file = tmp_path / "test_lab_reliability.db"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def app_config():
    """Load default validated application configuration."""
    return load_app_config()


class TestLabReliability:
    """Verifies TC-REL-01 and TC-REL-02."""

    def test_tc_rel_01_matrix_repeatability_and_reproducibility(
        self, temp_db: DatabaseManager, app_config
    ) -> None:
        """TC-REL-01: Verify 100% repeatable diagnostic outcomes across repeated multi-scenario runs."""
        runner = LabTrialRunner(
            db=temp_db,
            config=app_config,
            operator_confirmed=True,
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
        )

        # Run 2 repetitions per scenario configuration (10 total trials)
        matrix_result = runner.run_full_matrix(repetitions_per_scenario=2)

        assert matrix_result["total_trials"] == 10
        assert matrix_result["matches"] == 10
        assert matrix_result["mismatches"] == 0
        assert matrix_result["inconclusives"] == 0
        assert matrix_result["accuracy_pct"] == 100.0

        for trial_dict in matrix_result["trials"]:
            assert trial_dict["matched"] is True
            assert trial_dict["verification_status"] == TrialVerificationStatus.MATCH.value
            assert trial_dict["cleanup_status"] == TrialCleanupStatus.CLEANED_VERIFIED.value

    def test_tc_rel_02_resource_hygiene_and_integrity(
        self, temp_db: DatabaseManager, app_config
    ) -> None:
        """TC-REL-02: Verify zero residual impairment, zero database corruption, and zero thread leaks.

        Verifies:
        1. Thread count before and after execution remains strictly clean (no leaked threads).
        2. SQLite database integrity check passes without corruption.
        3. All trial records in SQLite lab_trials indicate CLEANED_VERIFIED.
        """
        # Snapshot initial threads
        threads_before = {t.name for t in threading.enumerate()}

        runner = LabTrialRunner(
            db=temp_db,
            config=app_config,
            operator_confirmed=True,
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
        )

        # Execute matrix
        matrix_result = runner.run_full_matrix(repetitions_per_scenario=1)
        assert matrix_result["total_trials"] == 5

        # 1. Thread hygiene check
        threads_after = {t.name for t in threading.enumerate()}
        leaked_threads = threads_after - threads_before
        # Exclude any pytest/internal background threads
        diagnostic_leaks = [t for t in leaked_threads if "ticnet" in t.lower() or "worker" in t.lower()]
        assert len(diagnostic_leaks) == 0, f"Thread leak detected: {diagnostic_leaks}"

        # 2. Database integrity check
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "ok"

        # 3. Cleanup status verification in SQLite
        trials = runner.ground_truth_mgr.list_all_trials()
        assert len(trials) == 5
        for t in trials:
            assert t.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED
