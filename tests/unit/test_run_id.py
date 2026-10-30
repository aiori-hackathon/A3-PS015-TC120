"""Comprehensive unit and integration tests for Unified Professional Run ID.

Verifies all requirements:
1. Approved Run ID format: TCN-YYYYMMDD-XXXXXX (e.g. TCN-20261024-000001).
2. Sequential incrementing from persistent SQLite database.
3. Persistence across application restarts (independent repository instantiations).
4. Concurrency safety: multi-threaded generation never produces duplicate IDs.
5. Date transition: different creation dates produce distinct YYYYMMDD prefixes and reset sequence.
6. Failed and interrupted/cancelled diagnostic runs retain their assigned Run IDs.
7. Backward compatibility: existing database records (including UUIDs) remain fully intact and accessible.
8. Copy-to-clipboard badge behavior in the UI.
9. Zero user-facing 'Session ID' terminology in desktop presentation layer.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone, date
import os
import tempfile
import threading
from typing import List

import pytest
from PySide6.QtWidgets import QApplication

from ticnet.models.entities import (
    TestRun,
    generate_run_id,
    now_utc_iso,
)
from ticnet.models.enums import RunStatus, RunMode
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.desktop.widgets.run_id_badge import RunIdBadge
from ticnet.desktop.pages.overview_page import OverviewPage
from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.pages.history_page import HistoryPage
from ticnet.desktop.pages.reports_page import ReportsPage
from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.services.history_service import HistoryService
from ticnet.workflow.orchestrator import WorkflowOrchestrator
from ticnet.workflow.worker import OrchestrationWorker
from ticnet.configuration.validator import AppConfig


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with initial schema applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = DatabaseManager(db_path=path)
    db.run_migrations()
    yield db
    try:
        if os.path.exists(path):
            os.remove(path)
    except PermissionError:
        pass


def test_approved_run_id_format():
    """Verify that generated Run IDs strictly adhere to TCN-YYYYMMDD-XXXXXX."""
    rid = generate_run_id(target_date="2026-10-24", sequence=1)
    assert rid == "TCN-20261024-000001"

    rid_seq = generate_run_id(target_date=date(2026, 10, 24), sequence=42)
    assert rid_seq == "TCN-20261024-000042"

    today_utc = datetime.now(timezone.utc).strftime("%Y%m%d")
    rid_default = generate_run_id()
    assert rid_default.startswith(f"TCN-{today_utc}-")
    assert len(rid_default) == 19
    parts = rid_default.split("-")
    assert len(parts) == 3
    assert parts[0] == "TCN"
    assert len(parts[1]) == 8 and parts[1].isdigit()
    assert len(parts[2]) == 6 and parts[2].isdigit()


def test_sequential_run_id_generation(temp_db):
    """Verify RunRepository.generate_next_run_id increments sequentially."""
    repo = RunRepository(temp_db)
    target_dt = "20261024"

    id1 = repo.generate_next_run_id(target_date=target_dt)
    assert id1 == "TCN-20261024-000001"

    # Persist run 1
    r1 = TestRun(
        run_id=id1,
        start_time_utc=now_utc_iso(),
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
    )
    repo.create_run(r1)

    # Next run should be 000002
    id2 = repo.generate_next_run_id(target_date=target_dt)
    assert id2 == "TCN-20261024-000002"

    r2 = TestRun(
        run_id=id2,
        start_time_utc=now_utc_iso(),
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
    )
    repo.create_run(r2)

    id3 = repo.generate_next_run_id(target_date=target_dt)
    assert id3 == "TCN-20261024-000003"


def test_numbering_persists_across_application_restarts(temp_db):
    """Verify numbering resumes from the persistent database after app restart."""
    target_dt = "20261024"

    # Instance 1: Before restart
    repo1 = RunRepository(temp_db)
    id1 = repo1.generate_next_run_id(target_date=target_dt)
    repo1.create_run(
        TestRun(
            run_id=id1,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )
    assert id1 == "TCN-20261024-000001"

    id2 = repo1.generate_next_run_id(target_date=target_dt)
    repo1.create_run(
        TestRun(
            run_id=id2,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )
    assert id2 == "TCN-20261024-000002"

    # Simulate Application Restart: clear class-level in-memory state and create new repo
    RunRepository.reset_allocated_cache()
    repo2 = RunRepository(temp_db)

    # Must query SQLite and detect max sequence is 2, producing 000003
    id3 = repo2.generate_next_run_id(target_date=target_dt)
    assert id3 == "TCN-20261024-000003"


def test_date_transition_behavior(temp_db):
    """Verify runs on different dates receive corresponding YYYYMMDD and independent sequence."""
    repo = RunRepository(temp_db)

    # Date A: 2026-10-24
    id_d1_1 = repo.generate_next_run_id(target_date="20261024")
    repo.create_run(
        TestRun(
            run_id=id_d1_1,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )
    assert id_d1_1 == "TCN-20261024-000001"

    id_d1_2 = repo.generate_next_run_id(target_date="20261024")
    repo.create_run(
        TestRun(
            run_id=id_d1_2,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )
    assert id_d1_2 == "TCN-20261024-000002"

    # Date B: 2026-10-25
    id_d2_1 = repo.generate_next_run_id(target_date="20261025")
    repo.create_run(
        TestRun(
            run_id=id_d2_1,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )
    assert id_d2_1 == "TCN-20261025-000001"

    # Back to Date A
    id_d1_3 = repo.generate_next_run_id(target_date="20261024")
    assert id_d1_3 == "TCN-20261024-000003"


def test_concurrency_prevention_of_duplicate_ids(temp_db):
    """Verify concurrent run creation across multiple threads never produces duplicate IDs."""
    repo = RunRepository(temp_db)
    generated_ids: List[str] = []
    lock = threading.Lock()
    target_dt = "20261024"

    def _worker():
        rid = repo.generate_next_run_id(target_date=target_dt)
        with lock:
            generated_ids.append(rid)

    # Spawn 30 concurrent threads requesting Run IDs simultaneously
    threads = [threading.Thread(target=_worker) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(generated_ids) == 30
    assert len(set(generated_ids)) == 30, "Duplicate Run IDs detected under concurrent generation!"
    # Verify sequence continuity
    sequences = sorted([int(rid.split("-")[2]) for rid in generated_ids])
    assert sequences == list(range(1, 31))


def test_failed_and_interrupted_runs_retain_assigned_ids(temp_db):
    """Verify failed and cancelled diagnostic runs persist their assigned Run IDs in SQLite."""
    repo = RunRepository(temp_db)
    target_dt = "20261024"

    # Run 1: Failed
    id1 = repo.generate_next_run_id(target_date=target_dt)
    failed_run = TestRun(
        run_id=id1,
        start_time_utc=now_utc_iso(),
        end_time_utc=now_utc_iso(),
        status=RunStatus.FAILED,
        app_version="1.0.0",
        config_version="1.0.0",
        overall_error="Gateway unreachable",
    )
    repo.create_run(failed_run)

    # Run 2: Cancelled
    id2 = repo.generate_next_run_id(target_date=target_dt)
    cancelled_run = TestRun(
        run_id=id2,
        start_time_utc=now_utc_iso(),
        end_time_utc=now_utc_iso(),
        status=RunStatus.CANCELLED,
        app_version="1.0.0",
        config_version="1.0.0",
        overall_error="User aborted",
    )
    repo.create_run(cancelled_run)

    # Run 3: Completed
    id3 = repo.generate_next_run_id(target_date=target_dt)
    assert id3 == "TCN-20261024-000003"

    # Retrieve from DB and verify statuses and retained IDs
    r1_db = repo.get_run(id1)
    assert r1_db is not None
    assert r1_db.run_id == "TCN-20261024-000001"
    assert r1_db.status == RunStatus.FAILED

    r2_db = repo.get_run(id2)
    assert r2_db is not None
    assert r2_db.run_id == "TCN-20261024-000002"
    assert r2_db.status == RunStatus.CANCELLED


def test_backward_compatibility_with_existing_uuid_records(temp_db):
    """Verify database safely preserves historical records that may use older UUID formats."""
    repo = RunRepository(temp_db)

    # Insert historical run with UUID format
    legacy_uuid = "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"
    legacy_run = TestRun(
        run_id=legacy_uuid,
        start_time_utc="2026-10-20T10:00:00Z",
        status=RunStatus.COMPLETED,
        app_version="0.9.0",
        config_version="0.9.0",
    )
    repo.create_run(legacy_run)

    # Now generate new Run ID with format TCN-YYYYMMDD-XXXXXX
    new_id = repo.generate_next_run_id(target_date="20261024")
    assert new_id == "TCN-20261024-000001"
    repo.create_run(
        TestRun(
            run_id=new_id,
            start_time_utc="2026-10-24T10:00:00Z",
            status=RunStatus.COMPLETED,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )

    # Both must be queryable without errors
    assert repo.get_run(legacy_uuid) is not None
    assert repo.get_run(new_id) is not None
    all_runs = repo.list_runs()
    assert len(all_runs) == 2


def test_run_id_badge_and_copy_functionality(qapp: QApplication):
    """Verify RunIdBadge displays formatted text, monospace styling, and copies to clipboard."""
    badge = RunIdBadge(run_id="TCN-20261024-000001")
    assert badge.get_run_id() == "TCN-20261024-000001"
    assert "TCN-20261024-000001" in badge.lbl_id.text()
    assert "RUN ID:" in badge.lbl_prefix.text()
    assert badge.btn_copy.isEnabled() is True

    # Test copy action
    badge.btn_copy.click()
    clipboard = QApplication.clipboard()
    if clipboard and clipboard.text():
        assert clipboard.text() == "TCN-20261024-000001"
    assert badge.btn_copy.text() == "✓ Copied"

    # Test reset text timer
    badge._reset_copy_label()
    assert badge.btn_copy.text() == "Copy"

    # Test empty / unset state
    badge.set_run_id(None)
    assert badge.lbl_id.text() == "--"
    assert badge.btn_copy.isEnabled() is False


def test_user_facing_ui_audit_no_session_id_terminology(qapp: QApplication, temp_db):
    """Verify all 5 core UI pages display 'Run ID' or 'Run' and ZERO 'Session ID' terminology."""
    history_svc = HistoryService(temp_db)

    pages = [
        OverviewPage(history_service=history_svc),
        DiagnosisPage(),
        ResultsPage(history_service=history_svc),
        HistoryPage(history_service=history_svc),
        ReportsPage(history_service=history_svc),
    ]

    forbidden_terms = ["session id", "session_id", "session:"]

    for page in pages:
        # Check all child labels, buttons, combobox items, and titles
        for child in page.findChildren(object):
            text = ""
            if hasattr(child, "text") and callable(child.text):
                text = child.text()
            elif hasattr(child, "title") and callable(child.title):
                text = child.title()

            if isinstance(text, str) and text:
                lower = text.lower()
                for term in forbidden_terms:
                    assert term not in lower, (
                        f"Forbidden term '{term}' found in {page.__class__.__name__} widget: '{text}'"
                    )


def test_orchestrator_and_worker_run_id_generation(temp_db):
    """Verify Orchestrator and Worker automatically generate TCN-YYYYMMDD-XXXXXX when run_id is None."""
    orch = WorkflowOrchestrator(db=temp_db)

    # Execute with None config to trigger early pre-flight run creation and persistence
    result_run = orch.execute_run(config=None, run_id=None)
    assert result_run.run_id.startswith("TCN-")
    assert len(result_run.run_id) == 19
    assert result_run.status == RunStatus.FAILED

    # Verify persisted in DB
    db_run = orch.run_repo.get_run(result_run.run_id)
    assert db_run is not None
    assert db_run.run_id == result_run.run_id

    # Next run via worker should receive next sequential ID
    worker = OrchestrationWorker(orchestrator=orch)
    assigned_id = worker.start(config=None)
    assert assigned_id.startswith("TCN-")
    assert len(assigned_id) == 19
    parts1 = result_run.run_id.split("-")
    parts2 = assigned_id.split("-")
    assert int(parts2[2]) == int(parts1[2]) + 1
