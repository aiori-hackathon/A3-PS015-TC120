"""Unit tests for OrchestrationWorker (Phase 7).

Verifies background thread execution, single-run concurrency enforcement,
cancellation propagation, orphan thread detection, exception recovery, and queue events.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional
import pytest

from ticnet.configuration.loader import load_app_config
from ticnet.models.enums import RunMode, RunStatus, StageStatus, Status
from ticnet.persistence.database import DatabaseManager
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType
from ticnet.workflow.orchestrator import WorkflowOrchestrator
from ticnet.workflow.worker import ConcurrentRunError, OrchestrationWorker
from tests.unit.test_orchestrator import (
    MockDNSMeasurementAdapter,
    MockGatewayDiscoveryAdapter,
    MockLoadedLatencyAdapter,
    MockPingMeasurementAdapter,
    MockThroughputAdapter,
)


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test_worker.db"
    db = DatabaseManager(str(p))
    db.run_migrations()
    return db


@pytest.fixture
def default_config():
    return load_app_config()


def create_mock_orchestrator(db: DatabaseManager, delay_s: float = 0.0) -> WorkflowOrchestrator:
    """Create an orchestrator with fast deterministic mocks and optional delay."""
    class DelayedGatewayAdapter(MockGatewayDiscoveryAdapter):
        def execute(self, *args, **kwargs):
            if delay_s > 0:
                time.sleep(delay_s)
            return super().execute(*args, **kwargs)

    return WorkflowOrchestrator(
        db=db,
        gateway_adapter=DelayedGatewayAdapter(),
        ping_adapter=MockPingMeasurementAdapter(),
        dns_adapter=MockDNSMeasurementAdapter(),
        throughput_adapter=MockThroughputAdapter(),
        load_adapter=MockLoadedLatencyAdapter(),
    )


class TestOrchestrationWorkerLifecycle:
    """Tests worker thread launch, state tracking, and clean completion."""

    def test_worker_start_executes_run_in_background(self, db_path, default_config):
        orch = create_mock_orchestrator(db_path, delay_s=0.05)
        event_q = queue.Queue()
        worker = OrchestrationWorker(orchestrator=orch, event_queue=event_q)

        assert not worker.is_running
        assert worker.active_run_id is None

        run_id = worker.start(config=default_config)
        assert run_id is not None
        assert worker.active_run_id == run_id

        # Wait for thread to finish
        terminated = worker.join(timeout_s=3.0)
        assert terminated is True
        assert not worker.is_running
        assert worker.active_run_id is None
        assert worker.last_error is None

        # Verify run result
        last_run = worker.last_run
        assert last_run is not None
        assert last_run.run_id == run_id
        assert last_run.status == RunStatus.COMPLETED

        # Verify events received in queue
        events: list[WorkflowEvent] = []
        while not event_q.empty():
            events.append(event_q.get_nowait())

        types = [e.event_type for e in events]
        assert WorkflowEventType.RUN_STARTED in types
        assert WorkflowEventType.RUN_COMPLETED in types

    def test_worker_concurrency_prevention_rejects_duplicate_run(self, db_path, default_config):
        # Create an orchestrator with deliberate delay so thread stays alive
        orch = create_mock_orchestrator(db_path, delay_s=0.5)
        worker = OrchestrationWorker(orchestrator=orch)

        run_id1 = worker.start(config=default_config)
        assert worker.is_running

        # Second start() call while running MUST raise ConcurrentRunError
        with pytest.raises(ConcurrentRunError) as exc_info:
            worker.start(config=default_config)

        assert run_id1 in str(exc_info.value)
        assert "already active" in str(exc_info.value)

        # Cleanup
        worker.cancel()
        worker.join(timeout_s=2.0)

    def test_worker_cancellation_propagates_promptly(self, db_path, default_config):
        orch = create_mock_orchestrator(db_path, delay_s=0.3)
        worker = OrchestrationWorker(orchestrator=orch)

        run_id = worker.start(config=default_config)
        assert worker.is_running

        # Cancel worker while running
        start_cancel_t = time.perf_counter()
        worker.cancel()
        elapsed_cancel = time.perf_counter() - start_cancel_t

        # Cancellation signal and abort must execute within 200ms
        assert elapsed_cancel < 0.2

        terminated = worker.join(timeout_s=2.0)
        assert terminated is True
        assert not worker.is_running

        # Run must be CANCELLED
        last_run = worker.last_run
        assert last_run is not None
        assert last_run.status == RunStatus.CANCELLED

    def test_worker_repeated_cancel_is_idempotent(self, db_path, default_config):
        orch = create_mock_orchestrator(db_path)
        worker = OrchestrationWorker(orchestrator=orch)

        # Cancel before starting
        worker.cancel()
        worker.cancel()  # Should not raise

        run_id = worker.start(config=default_config)
        worker.cancel()
        worker.cancel()  # Multiple cancellations during/after run

        worker.join(timeout_s=2.0)
        assert not worker.is_running

    def test_worker_thread_crashes_updates_db_run_to_failed(self, db_path, default_config, monkeypatch):
        orch = create_mock_orchestrator(db_path)
        # Force orchestrator.execute_run to throw an unexpected exception
        def crash_execute_run(*args, **kwargs):
            # Create a run in DB first to simulate mid-run crash
            from ticnet.models.entities import TestRun, now_utc_iso
            r = TestRun(
                run_id=kwargs.get("run_id") or "crash-run-id",
                start_time_utc=now_utc_iso(),
                status=RunStatus.RUNNING,
                app_version="1.0.0",
                config_version="1.0.0",
            )
            orch.run_repo.create_run(r)
            raise SystemError("Fatal unhandled worker crash")

        monkeypatch.setattr(orch, "execute_run", crash_execute_run)
        event_q = queue.Queue()
        worker = OrchestrationWorker(orchestrator=orch, event_queue=event_q)

        run_id = worker.start(config=default_config)
        worker.join(timeout_s=2.0)

        assert not worker.is_running
        assert worker.last_error is not None
        assert "Fatal unhandled worker crash" in str(worker.last_error)

        # Ensure run record in database was marked FAILED, not left RUNNING
        db_run = orch.run_repo.get_run(run_id)
        assert db_run is not None
        assert db_run.status == RunStatus.FAILED
        assert "Fatal unhandled worker crash" in (db_run.overall_error or "")

    def test_worker_join_detects_and_reports_orphaned_thread(self, db_path, default_config, monkeypatch):
        orch = create_mock_orchestrator(db_path, delay_s=0.2)
        worker = OrchestrationWorker(orchestrator=orch, cleanup_timeout_s=0.1)

        run_id = worker.start(config=default_config)

        # Monkeypatch thread.is_alive on the active thread to simulate a thread that won't die
        monkeypatch.setattr(worker._thread, "is_alive", lambda: True)

        # join() must return False and not falsely report success
        real_thread = worker._thread
        terminated = worker.join(timeout_s=0.1)
        assert terminated is False

        # Cleanup: cancel and join the real background thread so it does not outlive pytest output capture
        worker.cancel()
        if real_thread is not None:
            real_thread.join(timeout=2.0)

