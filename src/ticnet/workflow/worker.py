"""Thread-safe background worker for TicNet diagnostic orchestration (Phase 7).

Manages the lifecycle of asynchronous diagnostic runs:
- Executes WorkflowOrchestrator in a dedicated daemon worker thread.
- Enforces single active run concurrency: Prevents duplicate concurrent executions.
- Thread-safe cancellation propagation with immediate adapter abort (<200ms).
- Bounded thread join with explicit is_alive() verification (no false termination assumptions).
- Structured event streaming via thread-safe queue.Queue.
- Guarantees error capture and database state finalization on unhandled worker exceptions.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

from ticnet.configuration.validator import AppConfig
from ticnet.models.entities import TestRun, now_utc_iso
from ticnet.models.enums import RunMode, RunStatus
from ticnet.utils.logging import get_logger
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType
from ticnet.workflow.orchestrator import WorkflowOrchestrator

logger = get_logger("workflow.worker")


class ConcurrentRunError(RuntimeError):
    """Raised when an attempt is made to start a diagnostic run while another is active."""
    pass


class OrchestrationWorker:
    """Manages asynchronous execution of WorkflowOrchestrator in a background thread."""

    def __init__(
        self,
        orchestrator: WorkflowOrchestrator,
        event_queue: Optional[queue.Queue] = None,
        cleanup_timeout_s: float = 3.0,
    ) -> None:
        """Initialize the orchestration worker.

        Args:
            orchestrator: Configured WorkflowOrchestrator instance.
            event_queue: Optional bounded queue.Queue for progress events (created if None).
            cleanup_timeout_s: Maximum duration in seconds to wait for worker termination.
        """
        self.orchestrator = orchestrator
        self.event_queue = event_queue if event_queue is not None else queue.Queue(maxsize=1000)
        self.cleanup_timeout_s = max(0.5, float(cleanup_timeout_s))

        # Tie orchestrator event queue to worker's event queue if not already set
        if self.orchestrator.event_queue is None:
            self.orchestrator.event_queue = self.event_queue

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel_event: threading.Event = threading.Event()
        self._active_run_id: Optional[str] = None
        self._is_running: bool = False
        self._last_run: Optional[TestRun] = None
        self._last_error: Optional[Exception] = None

    @property
    def is_running(self) -> bool:
        """Return True if the background worker thread is currently active."""
        with self._lock:
            return self._is_running and (self._thread is not None and self._thread.is_alive())

    @property
    def active_run_id(self) -> Optional[str]:
        """Return the run_id of the currently active diagnostic session, or None."""
        with self._lock:
            return self._active_run_id

    @property
    def last_run(self) -> Optional[TestRun]:
        """Return the TestRun entity resulting from the most recent execution."""
        with self._lock:
            return self._last_run

    @property
    def last_error(self) -> Optional[Exception]:
        """Return the most recent unhandled exception from the worker, if any."""
        with self._lock:
            return self._last_error

    def start(
        self,
        config: AppConfig,
        run_id: Optional[str] = None,
        run_mode: RunMode = RunMode.OPERATIONAL,
    ) -> str:
        """Start a diagnostic run asynchronously in the background.

        Args:
            config: Validated AppConfig instance.
            run_id: Optional run identifier.
            run_mode: Execution mode (OPERATIONAL or LAB).

        Returns:
            The run_id of the dispatched diagnostic session.

        Raises:
            ConcurrentRunError: If another diagnostic run is already active.
        """
        with self._lock:
            if self._is_running and self._thread is not None and self._thread.is_alive():
                raise ConcurrentRunError(
                    f"A diagnostic run is already active (run_id: {self._active_run_id})"
                )

            self._cancel_event.clear()
            self._is_running = True
            self._last_error = None
            self._last_run = None

            # Generate or use provided run_id
            target_run_id = run_id or self.orchestrator.run_repo.generate_next_run_id()
            self._active_run_id = target_run_id

            def _thread_target():
                try:
                    logger.info("Worker thread started for run %s", target_run_id)
                    result_run = self.orchestrator.execute_run(
                        config=config,
                        run_id=target_run_id,
                        cancel_event=self._cancel_event,
                        run_mode=run_mode,
                    )
                    with self._lock:
                        self._last_run = result_run
                except Exception as exc:
                    logger.error("Unhandled exception in orchestration worker thread: %s", exc, exc_info=True)
                    with self._lock:
                        self._last_error = exc

                    # Ensure run record in database is marked FAILED rather than left in RUNNING
                    try:
                        db_run = self.orchestrator.run_repo.get_run(target_run_id)
                        if db_run and db_run.status == RunStatus.RUNNING:
                            db_run.status = RunStatus.FAILED
                            db_run.overall_error = f"Worker thread crashed: {exc}"
                            db_run.end_time_utc = now_utc_iso()
                            self.orchestrator.run_repo.update_run(db_run)
                            with self._lock:
                                self._last_run = db_run
                    except Exception as db_err:
                        logger.error("Failed to mark crashed run as FAILED in database: %s", db_err)

                    self.orchestrator.emit_event(
                        WorkflowEvent(
                            event_type=WorkflowEventType.RUN_FAILED,
                            run_id=target_run_id,
                            status=RunStatus.FAILED.value,
                            message=f"Worker thread error: {exc}",
                        )
                    )
                finally:
                    with self._lock:
                        self._is_running = False
                        self._active_run_id = None
                    logger.info("Worker thread finished for run %s", target_run_id)

            self._thread = threading.Thread(
                target=_thread_target,
                name=f"ticnet-worker-{target_run_id}",
                daemon=True,
            )
            self._thread.start()
            return target_run_id

    def cancel(self) -> None:
        """Signal cancellation to the active diagnostic run and abort active adapters.

        Idempotent: May be called multiple times safely.
        """
        logger.info("Cancellation requested for worker (active_run_id: %s)", self._active_run_id)
        self._cancel_event.set()
        # Forcibly abort in-flight throughput / socket operations for <200ms responsiveness
        self.orchestrator.abort_active_operations()

    def join(self, timeout_s: Optional[float] = None) -> bool:
        """Wait for the worker thread to terminate cleanly within timeout.

        Safety: Never assumes a timed join implies termination. Explicitly verifies
        self._thread.is_alive() post-join.

        Args:
            timeout_s: Maximum duration to wait (defaults to self.cleanup_timeout_s).

        Returns:
            True if worker thread has completely terminated; False if still alive.
        """
        thread = self._thread
        if thread is None:
            return True

        timeout = timeout_s if timeout_s is not None else self.cleanup_timeout_s
        thread.join(timeout=timeout)

        thread_still_alive = thread.is_alive()
        if thread_still_alive:
            logger.error(
                "CRITICAL: Worker thread %s failed to terminate within %ss cleanup timeout",
                thread.name,
                timeout,
            )
            return False

        with self._lock:
            self._is_running = False
        return True
