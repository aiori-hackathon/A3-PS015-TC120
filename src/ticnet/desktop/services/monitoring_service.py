"""Desktop service adapter for Scheduled Network Monitoring.

Coordinates between the PySide6 UI event loop, MonitoringScheduler engine,
and DiagnosticService background worker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

from ticnet.desktop.services.diagnostic_service import DiagnosticService
from ticnet.desktop.services.live_telemetry_service import (
    LiveTelemetrySample,
    LiveTelemetryService,
)
from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringStatus,
    MonitoringTriggerType,
)
from ticnet.models.notifications import (
    NetworkIncident,
    NotificationPreferences,
)
from ticnet.monitoring.analytics import (
    AnalyticsEngine,
    AnalyticsTrendPoint,
    DetectionStatus,
    SlowdownEvent,
    SlowdownReport,
    TimeWindow,
)
from ticnet.monitoring.incident_manager import IncidentManager
from ticnet.monitoring.scheduler import MonitoringScheduler
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.analytics_repo import AnalyticsRepository
from ticnet.persistence.repositories.incident_repo import IncidentRepository
from ticnet.utils.logging import get_logger

logger = get_logger("desktop.service.monitoring")


class MonitoringService(QObject):
    """Coordinates periodic network monitoring schedules and UI signals."""

    sig_schedule_updated = Signal(object)           # MonitoringSchedule
    sig_status_changed = Signal(str)                # "ENABLED", "PAUSED", "NOT_CONFIGURED"
    sig_monitoring_run_started = Signal(str, str)   # monitoring_run_id, trigger_type
    sig_monitoring_run_completed = Signal(str, str) # monitoring_run_id, status
    sig_history_refreshed = Signal()
    sig_analytics_updated = Signal(object)          # SlowdownReport
    sig_incident_updated = Signal(object)           # List[NetworkIncident]
    sig_preferences_updated = Signal(object)        # NotificationPreferences
    sig_live_telemetry_updated = Signal(object)     # LiveTelemetrySample

    def __init__(
        self,
        db: DatabaseManager,
        diagnostic_service: Optional[DiagnosticService] = None,
        telemetry_service: Optional[LiveTelemetryService] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.diagnostic_service = diagnostic_service
        self.telemetry_service = telemetry_service

        # Bind live telemetry signal if service provided
        if self.telemetry_service:
            self.telemetry_service.sig_telemetry_updated.connect(self._on_live_telemetry_sample)

        # Initialize scheduler engine, analytics repository & incident manager
        self.scheduler = MonitoringScheduler(
            db=self.db,
            is_active_checker=self._is_diagnostic_active,
            run_starter=self._start_diagnostic_run,
        )
        self.analytics_repo = AnalyticsRepository(self.db)
        self.incident_repo = IncidentRepository(self.db)
        self.incident_manager = IncidentManager(self.incident_repo)

        self._active_monitoring_run_id: Optional[str] = None

        # Bind diagnostic completion signal if service provided
        if self.diagnostic_service:
            self.diagnostic_service.sig_run_finished.connect(self._on_diagnostic_finished)

        # Periodic tick timer to evaluate due schedules (runs on GUI thread)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(5000)  # Check every 5 seconds
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        # Check for missed runs immediately on startup
        self._check_startup_missed_runs()

    def _is_diagnostic_active(self) -> bool:
        if self.diagnostic_service:
            return self.diagnostic_service.is_running
        return False

    def _start_diagnostic_run(self) -> str:
        if not self.diagnostic_service:
            raise RuntimeError("DiagnosticService not available to execute scheduled run")
        return self.diagnostic_service.start_diagnosis()

    def _check_startup_missed_runs(self) -> None:
        """Handle missed runs after application relaunch."""
        try:
            missed = self.scheduler.handle_missed_runs()
            if missed:
                self.sig_history_refreshed.emit()
                self.sig_schedule_updated.emit(self.get_schedule())
        except Exception as exc:
            logger.error("Error checking startup missed runs: %s", exc)

    def _on_tick(self) -> None:
        """Evaluate schedule every tick interval."""
        try:
            # 1. Evaluate missed run catch-up policy (handles sleep/hibernate or clock skew)
            missed = self.scheduler.handle_missed_runs()
            if missed:
                self.sig_history_refreshed.emit()
                self.sig_schedule_updated.emit(self.get_schedule())
                return

            # 2. Check if run is due
            if not self.scheduler.is_run_due():
                return

            logger.info("Monitoring schedule is due for execution; triggering run...")
            m_run, err = self.scheduler.trigger_run(trigger_type=MonitoringTriggerType.SCHEDULED)
            if m_run:
                if m_run.status == MonitoringRunStatus.RUNNING.value:
                    self._active_monitoring_run_id = m_run.monitoring_run_id
                    self.sig_monitoring_run_started.emit(m_run.monitoring_run_id, m_run.trigger_type)
                elif m_run.status == MonitoringRunStatus.SKIPPED.value:
                    logger.info("Scheduled run was skipped: %s", m_run.skip_reason)
                    self.sig_history_refreshed.emit()
                elif m_run.status == MonitoringRunStatus.FAILED.value:
                    logger.warning("Scheduled run failed to start: %s", m_run.error_message)
                    self._active_monitoring_run_id = None
                    self.sig_monitoring_run_completed.emit(m_run.monitoring_run_id, m_run.status)
                    self.sig_history_refreshed.emit()

                self.sig_schedule_updated.emit(self.get_schedule())
        except Exception as exc:
            logger.error("Error in scheduler tick evaluation: %s", exc, exc_info=True)

    def _on_diagnostic_finished(self, run_id: str, status: str, message: str) -> None:
        """Handle diagnostic run completion and record metrics."""
        if self._active_monitoring_run_id:
            m_id = self._active_monitoring_run_id
            self._active_monitoring_run_id = None
            logger.info("Recording completion for monitoring run %s (run_id: %s)", m_id, run_id)
            self.scheduler.record_run_completion(
                monitoring_run_id=m_id,
                run_id=run_id,
                status=status,
                error_message=message if status != "COMPLETED" else None,
            )
            self.sig_monitoring_run_completed.emit(m_id, status)
            self.sig_history_refreshed.emit()
            self.sig_schedule_updated.emit(self.get_schedule())

            # Evaluate slowdown and log audit event
            try:
                report = self.evaluate_slowdown(window=TimeWindow.H24)
                if report.overall_status != DetectionStatus.INSUFFICIENT_DATA:
                    event = SlowdownEvent(
                        monitoring_run_id=m_id,
                        metric_name="overall",
                        detection_status=report.overall_status.value,
                        time_window=report.time_window.value,
                        explanation=report.summary_headline,
                    )
                    self.analytics_repo.save_slowdown_event(event)
                self.sig_analytics_updated.emit(report)

                # Process Stage 3 incidents & alerts
                incidents = self.incident_manager.process_slowdown_report(report, monitoring_run_id=m_id)
                self.sig_incident_updated.emit(incidents)
            except Exception as exc:
                logger.error("Error evaluating slowdown and incidents on run completion: %s", exc, exc_info=True)

    def get_analytics_trends(self, window: TimeWindow = TimeWindow.H24) -> List[AnalyticsTrendPoint]:
        """Fetch historical monitoring runs in window and format as trend points."""
        runs = self.analytics_repo.get_monitoring_runs_in_window(window=window)
        points: List[AnalyticsTrendPoint] = []
        for r in runs:
            points.append(
                AnalyticsTrendPoint(
                    timestamp_utc=r.started_at_utc or r.scheduled_for_utc,
                    monitoring_run_id=r.monitoring_run_id,
                    run_id=r.run_id,
                    trigger_type=r.trigger_type,
                    status=r.status,
                    latency_ms=r.latency_ms,
                    jitter_ms=r.jitter_ms,
                    packet_loss_pct=r.packet_loss_pct,
                    download_mbps=r.download_mbps,
                )
            )
        return points

    def evaluate_slowdown(self, window: TimeWindow = TimeWindow.H24) -> SlowdownReport:
        """Evaluate network performance and sustained slowdown across historical runs."""
        runs = self.analytics_repo.get_monitoring_runs_in_window(window=window)
        prev_status = self.analytics_repo.get_latest_slowdown_status(metric_name="overall")
        return AnalyticsEngine.evaluate_runs(runs=runs, window=window, previous_status=prev_status)

    def get_schedule(self) -> MonitoringSchedule:
        """Retrieve current schedule configuration."""
        return self.scheduler.get_or_create_schedule()

    def save_schedule(
        self,
        enabled: bool,
        interval_type: str,
        daily_time_utc: Optional[str] = "09:00",
    ) -> MonitoringSchedule:
        """Save schedule settings and notify listeners."""
        schedule = self.scheduler.save_schedule(
            enabled=enabled,
            interval_type=interval_type,
            daily_time_utc=daily_time_utc,
        )
        self.sig_schedule_updated.emit(schedule)
        self.sig_status_changed.emit(schedule.status)
        return schedule

    def pause_monitoring(self) -> MonitoringSchedule:
        """Pause monitoring schedule."""
        schedule = self.scheduler.pause_monitoring()
        self.sig_schedule_updated.emit(schedule)
        self.sig_status_changed.emit(schedule.status)
        return schedule

    def resume_monitoring(self) -> MonitoringSchedule:
        """Resume monitoring schedule."""
        schedule = self.scheduler.resume_monitoring()
        self.sig_schedule_updated.emit(schedule)
        self.sig_status_changed.emit(schedule.status)
        return schedule

    def run_test_now(self) -> Tuple[Optional[MonitoringRun], Optional[str]]:
        """Trigger an immediate monitoring run on demand."""
        m_run, err = self.scheduler.trigger_run(trigger_type=MonitoringTriggerType.MANUAL)
        if m_run:
            if m_run.status == MonitoringRunStatus.RUNNING.value:
                self._active_monitoring_run_id = m_run.monitoring_run_id
                self.sig_monitoring_run_started.emit(m_run.monitoring_run_id, m_run.trigger_type)
            elif m_run.status == MonitoringRunStatus.FAILED.value:
                self._active_monitoring_run_id = None
                self.sig_monitoring_run_completed.emit(m_run.monitoring_run_id, m_run.status)
        self.sig_history_refreshed.emit()
        self.sig_schedule_updated.emit(self.get_schedule())
        return m_run, err

    @property
    def is_diagnosing(self) -> bool:
        """Return True if an on-demand or scheduled diagnostic is currently running."""
        return self._is_diagnostic_active()

    def is_scheduler_running(self) -> bool:
        """Return True if the scheduler tick timer is active and schedule is enabled."""
        sched = self.get_schedule()
        return bool(
            self._tick_timer.isActive()
            and sched.enabled
            and sched.status == MonitoringStatus.ENABLED.value
        )

    def get_active_monitoring_run_id(self) -> Optional[str]:
        """Return the active monitoring run ID if one is currently in-flight."""
        return self._active_monitoring_run_id

    def list_runs(self, limit: int = 50) -> List[MonitoringRun]:
        """Fetch list of historical monitoring runs."""
        return self.scheduler.repo.list_monitoring_runs(limit=limit)

    def _on_live_telemetry_sample(self, sample: object) -> None:
        """Forward real-time telemetry observation to UI listeners."""
        self.sig_live_telemetry_updated.emit(sample)

    def get_latest_run(self) -> Optional[MonitoringRun]:
        """Fetch the most recent monitoring run."""
        runs = self.list_runs(limit=1)
        return runs[0] if runs else None

    def get_latest_completed_run(self) -> Optional[MonitoringRun]:
        """Fetch the most recent completed monitoring run with measured metrics."""
        runs = self.list_runs(limit=50)
        for r in runs:
            if r.status in (MonitoringRunStatus.COMPLETED.value, "COMPLETED") and (
                r.latency_ms is not None or r.download_mbps is not None
            ):
                return r
        return None

    def get_notification_preferences(self) -> NotificationPreferences:
        """Fetch active notification preferences."""
        return self.incident_repo.get_preferences()

    def save_notification_preferences(self, prefs: NotificationPreferences) -> NotificationPreferences:
        """Save updated notification preferences and notify listeners."""
        saved = self.incident_repo.save_preferences(prefs)
        self.sig_preferences_updated.emit(saved)
        return saved

    def list_incidents(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
    ) -> List[NetworkIncident]:
        """Retrieve network incidents in reverse chronological order."""
        return self.incident_repo.list_incidents(limit=limit, offset=offset, status_filter=status_filter)

    def shutdown(self) -> None:
        """Cleanly stop timers on application shutdown."""
        self._tick_timer.stop()

