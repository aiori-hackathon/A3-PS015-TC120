"""Diagnostic service adapter for TicNet desktop presentation layer.

Bridges the background OrchestrationWorker and DiagnosisEngine with PySide6 Qt
signals and event loops without running measurement probes on the GUI thread.
"""

from __future__ import annotations

import queue
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ticnet.configuration.loader import load_app_config
from ticnet.configuration.validator import AppConfig
from ticnet.diagnosis.engine import DiagnosisEngine
from ticnet.models.entities import TestRun
from ticnet.models.enums import RunMode, RunStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.utils.logging import get_logger
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType
from ticnet.workflow.orchestrator import WorkflowOrchestrator
from ticnet.workflow.worker import ConcurrentRunError, OrchestrationWorker

logger = get_logger("desktop.service.diagnostic")


class DiagnosticService(QObject):
    """Coordinates background diagnostic runs and streams progress to Qt UI widgets."""

    # Qt Signals for decoupled UI updates on the main thread
    sig_run_started = Signal(str)                          # run_id
    sig_stage_started = Signal(str, int, str)              # run_id, stage_order, stage_name
    sig_stage_progress = Signal(str, int, str, str)        # run_id, stage_order, stage_name, message
    sig_stage_completed = Signal(str, int, str, str)       # run_id, stage_order, stage_name, status
    sig_stage_skipped = Signal(str, int, str, str)         # run_id, stage_order, stage_name, reason
    sig_stage_failed = Signal(str, int, str, str)          # run_id, stage_order, stage_name, error
    sig_run_finished = Signal(str, str, str)               # run_id, status, error_message
    sig_diagnosis_completed = Signal(str, int)             # run_id, findings_count
    sig_error = Signal(str)                                # user-facing error message

    def __init__(
        self,
        db: DatabaseManager,
        config: Optional[AppConfig] = None,
        orchestrator: Optional[WorkflowOrchestrator] = None,
        worker: Optional[OrchestrationWorker] = None,
        diagnosis_engine: Optional[DiagnosisEngine] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        """Initialize the diagnostic service with backend dependencies.

        Args:
            db: Initialized DatabaseManager.
            config: Optional pre-loaded AppConfig.
            orchestrator: Optional WorkflowOrchestrator override.
            worker: Optional OrchestrationWorker override.
            diagnosis_engine: Optional DiagnosisEngine override.
            parent: Optional Qt QObject parent.
        """
        super().__init__(parent)
        self.db = db
        self._config = config
        self.orchestrator = orchestrator or WorkflowOrchestrator(db=self.db)
        self.worker = worker or OrchestrationWorker(orchestrator=self.orchestrator)
        self.diagnosis_engine = diagnosis_engine or DiagnosisEngine()

        # Non-blocking event poll timer running on the Qt GUI thread
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)  # 50ms polling tick
        self._poll_timer.timeout.connect(self._poll_events)

        self._current_run_id: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Return True if a diagnostic worker run is actively executing."""
        return self.worker.is_running

    @property
    def active_run_id(self) -> Optional[str]:
        """Return the run_id of the currently active run, or None."""
        return self._current_run_id if self.worker.is_running else None

    @property
    def last_run(self) -> Optional[TestRun]:
        """Return the most recent TestRun record from the worker."""
        return self.worker.last_run

    def get_config(self) -> AppConfig:
        """Retrieve validated AppConfig, loading defaults if not pre-initialized."""
        if self._config is None:
            self._config = load_app_config()
        return self._config

    def set_config(self, config: AppConfig) -> None:
        """Update active configuration."""
        self._config = config

    def start_diagnosis(
        self,
        config_override: Optional[AppConfig] = None,
        run_mode: RunMode = RunMode.OPERATIONAL,
    ) -> str:
        """Start a diagnostic measurement session asynchronously.

        Args:
            config_override: Optional configuration override.
            run_mode: Operational or Lab execution mode.

        Returns:
            The unique run_id of the dispatched diagnostic session.

        Raises:
            ConcurrentRunError: If another diagnostic run is already active.
        """
        if self.is_running:
            err_msg = f"A diagnostic run is already active (run_id: {self._current_run_id})"
            logger.warning(err_msg)
            self.sig_error.emit(err_msg)
            raise ConcurrentRunError(err_msg)

        try:
            cfg = config_override or self.get_config()
            run_id = self.worker.start(config=cfg, run_mode=run_mode)
            self._current_run_id = run_id
            logger.info("Diagnostic run %s dispatched to background worker", run_id)

            # Start event polling on Qt main thread
            if not self._poll_timer.isActive():
                self._poll_timer.start()

            self.sig_run_started.emit(run_id)
            return run_id
        except Exception as exc:
            logger.error("Failed to start diagnostic run: %s", exc, exc_info=True)
            self.sig_error.emit(f"Failed to start diagnosis: {exc}")
            raise

    def cancel_diagnosis(self) -> None:
        """Signal cooperative cancellation to the active background worker."""
        if not self.is_running:
            logger.info("Cancel requested but worker is not running")
            return

        logger.info("Cancelling active diagnostic run %s", self._current_run_id)
        self.worker.cancel()

    def _poll_events(self) -> None:
        """Drain event queue from worker thread and emit Qt signals on the main thread."""
        events_processed = 0
        while events_processed < 50:
            try:
                event: WorkflowEvent = self.worker.event_queue.get_nowait()
            except queue.Empty:
                break

            events_processed += 1
            self._dispatch_event(event)

        # Stop timer if worker finished and event queue is drained
        if not self.worker.is_running and self.worker.event_queue.empty():
            self._poll_timer.stop()

    def _dispatch_event(self, event: WorkflowEvent) -> None:
        """Translate a typed WorkflowEvent into appropriate Qt signal emissions."""
        event_type = event.event_type
        run_id = event.run_id
        stage_order = event.stage_order or 0
        stage_name = event.stage_name or ""
        msg = event.message or ""
        status = event.status or ""

        logger.debug("Dispatching workflow event to Qt: %s for run %s", event_type.value, run_id)

        if event_type == WorkflowEventType.RUN_STARTED:
            self.sig_run_started.emit(run_id)

        elif event_type == WorkflowEventType.STAGE_STARTED:
            self.sig_stage_started.emit(run_id, stage_order, stage_name)

        elif event_type == WorkflowEventType.STAGE_PROGRESS:
            self.sig_stage_progress.emit(run_id, stage_order, stage_name, msg)

        elif event_type == WorkflowEventType.STAGE_COMPLETED:
            self.sig_stage_completed.emit(run_id, stage_order, stage_name, status)

        elif event_type == WorkflowEventType.STAGE_SKIPPED:
            self.sig_stage_skipped.emit(run_id, stage_order, stage_name, msg)

        elif event_type in (WorkflowEventType.STAGE_FAILED, WorkflowEventType.STAGE_CANCELLED):
            self.sig_stage_failed.emit(run_id, stage_order, stage_name, msg)

        elif event_type in (WorkflowEventType.RUN_COMPLETED, WorkflowEventType.RUN_PARTIAL):
            self._handle_run_completion(run_id, status, msg)

        elif event_type in (WorkflowEventType.RUN_CANCELLED, WorkflowEventType.RUN_FAILED):
            self._poll_timer.stop()
            self.sig_run_finished.emit(run_id, status, msg)

    def _handle_run_completion(self, run_id: str, status: str, message: str) -> None:
        """Trigger deterministic diagnosis rule evaluation once measurements are committed."""
        self._poll_timer.stop()
        findings_count = 0

        try:
            logger.info("Executing DiagnosisEngine.evaluate_run for %s", run_id)
            findings = self.diagnosis_engine.evaluate_run(run_id=run_id, db=self.db)
            findings_count = len(findings)
            logger.info("Diagnosis evaluated: %d findings persisted for run %s", findings_count, run_id)
            self.sig_diagnosis_completed.emit(run_id, findings_count)
        except Exception as exc:
            logger.error("DiagnosisEngine evaluation failed for run %s: %s", run_id, exc, exc_info=True)
            self.sig_error.emit(f"Diagnosis evaluation failed: {exc}")

        self.sig_run_finished.emit(run_id, status, message)

    def shutdown(self, timeout_s: float = 3.0) -> bool:
        """Safely terminate the background worker and stop the event timer on app close."""
        self._poll_timer.stop()
        if self.worker.is_running:
            logger.info("Shutdown requested with active worker; cancelling and joining...")
            self.worker.cancel()
            return self.worker.join(timeout_s=timeout_s)
        return True
