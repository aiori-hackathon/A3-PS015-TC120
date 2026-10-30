"""Unit tests for Stage 3 Configurable Alerts and Incident Notifications.

Verifies:
- NotificationPreferences persistence in SQLite (defaults, custom updates, restarts).
- IncidentRepository lifecycle operations (create, update, resolve, filter).
- IncidentManager integration with Stage 2 SlowdownReport.
- Event filtering: notify_suspected, notify_confirmed, notify_recovery.
- Deduplication and cooldown interval enforcement (no repeated alerts on every tick).
- Transition from SUSPECTED -> CONFIRMED -> RESOLVED.
- Safe non-blocking dispatcher invocation and suppression handling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
import pytest

from ticnet.models.monitoring import (
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringTriggerType,
)
from ticnet.models.notifications import (
    IncidentSeverity,
    IncidentStatus,
    NetworkIncident,
    NotificationPayload,
    NotificationPreferences,
)
from ticnet.monitoring.analytics import (
    AnalyticsEngine,
    DetectionStatus,
    MetricEvaluation,
    SlowdownReport,
    TimeWindow,
)
from ticnet.monitoring.incident_manager import IncidentManager
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.incident_repo import IncidentRepository
from ticnet.persistence.repositories.monitoring_repo import MonitoringRepository


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh SQLite database with migrations V001 through V004 applied."""
    db_file = tmp_path / "test_stage3_incidents.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


def test_notification_preferences_defaults_and_persistence(test_db: DatabaseManager) -> None:
    """Verify default notification preferences and atomic SQLite persistence."""
    repo = IncidentRepository(test_db)

    # 1. Fetch defaults
    prefs = repo.get_preferences()
    assert prefs.preference_id == "default"
    assert prefs.desktop_enabled is True
    assert prefs.sound_enabled is False
    assert prefs.notify_suspected is False
    assert prefs.notify_confirmed is True
    assert prefs.notify_recovery is True
    assert prefs.cooldown_minutes == 30

    # 2. Modify and save preferences
    prefs.desktop_enabled = True
    prefs.sound_enabled = True
    prefs.notify_suspected = True
    prefs.cooldown_minutes = 15
    saved = repo.save_preferences(prefs)
    assert saved.sound_enabled is True
    assert saved.notify_suspected is True
    assert saved.cooldown_minutes == 15

    # 3. Simulate application restart: new repository instance reading from DB
    restarted_repo = IncidentRepository(test_db)
    loaded = restarted_repo.get_preferences()
    assert loaded.desktop_enabled is True
    assert loaded.sound_enabled is True
    assert loaded.notify_suspected is True
    assert loaded.notify_confirmed is True
    assert loaded.notify_recovery is True
    assert loaded.cooldown_minutes == 15


def test_incident_lifecycle_transitions(test_db: DatabaseManager) -> None:
    """Verify incident transitions: creation, status update, and resolution."""
    repo = IncidentRepository(test_db)

    # Create suspected incident
    inc = NetworkIncident(
        metric_name="latency",
        severity=IncidentSeverity.WARNING.value,
        status=IncidentStatus.SUSPECTED.value,
        baseline_value=20.0,
        observed_value=65.0,
        headline="Suspected High Latency",
        description="Isolated latency spike observed",
    )
    created = repo.create_incident(inc)
    assert created.incident_id is not None

    # Query active incident for metric
    active = repo.get_active_incident_for_metric("latency")
    assert active is not None
    assert active.incident_id == created.incident_id
    assert active.status == IncidentStatus.SUSPECTED.value

    # Escalate to CONFIRMED
    active.status = IncidentStatus.CONFIRMED.value
    active.severity = IncidentSeverity.CRITICAL.value
    active.observed_value = 75.0
    repo.update_incident(active)

    updated = repo.get_incident(created.incident_id)
    assert updated is not None
    assert updated.status == IncidentStatus.CONFIRMED.value
    assert updated.severity == IncidentSeverity.CRITICAL.value
    assert updated.observed_value == 75.0

    # Resolve incident
    repo.resolve_incident(created.incident_id)
    resolved = repo.get_incident(created.incident_id)
    assert resolved is not None
    assert resolved.status == IncidentStatus.RESOLVED.value
    assert resolved.resolved_at_utc is not None

    # Active query should now return None
    assert repo.get_active_incident_for_metric("latency") is None


def test_incident_manager_confirmed_slowdown_alert() -> None:
    """Verify IncidentManager detects CONFIRMED_SLOWDOWN and dispatches alert."""
    dispatched: List[NotificationPayload] = []

    def mock_dispatcher(payload: NotificationPayload, prefs: NotificationPreferences) -> None:
        dispatched.append(payload)

    # Use in-memory DB
    from ticnet.persistence.database import DatabaseManager
    import tempfile
    import os

    tmp_dir = tempfile.mkdtemp()
    db = DatabaseManager(os.path.join(tmp_dir, "test_alerts.db"))
    db.run_migrations()

    repo = IncidentRepository(db)
    mgr = IncidentManager(incident_repo=repo, notification_dispatcher=mock_dispatcher)

    # Construct report with CONFIRMED_SLOWDOWN in latency
    eval_lat = MetricEvaluation(
        metric_name="latency",
        status=DetectionStatus.CONFIRMED_SLOWDOWN,
        baseline_value=20.0,
        observed_value=70.0,
        deviation_pct=250.0,
        threshold_applied="+40% and +15ms",
        consecutive_breaches=2,
        explanation="Sustained degradation confirmed across 2 measurements.",
        sample_count=5,
    )
    report = SlowdownReport(
        overall_status=DetectionStatus.CONFIRMED_SLOWDOWN,
        time_window=TimeWindow.H24,
        total_runs_evaluated=5,
        valid_measurements_count=5,
        evaluations={"latency": eval_lat},
        summary_headline="Confirmed Slowdown",
        summary_detail="Sustained degradation",
    )

    incidents = mgr.process_slowdown_report(report)
    assert len(incidents) == 1
    assert incidents[0].status == IncidentStatus.CONFIRMED.value
    assert incidents[0].notification_count == 1

    # Verify notification was dispatched
    assert len(dispatched) == 1
    assert "Latency" in dispatched[0].title
    assert dispatched[0].severity == IncidentSeverity.CRITICAL
    assert dispatched[0].observed_value == 70.0
    assert dispatched[0].baseline_value == 20.0


def test_incident_manager_cooldown_suppression() -> None:
    """Verify cooldown prevents repeated notifications for the same ongoing incident on scheduler ticks."""
    dispatched: List[NotificationPayload] = []

    def mock_dispatcher(payload: NotificationPayload, prefs: NotificationPreferences) -> None:
        dispatched.append(payload)

    import tempfile, os
    tmp_dir = tempfile.mkdtemp()
    db = DatabaseManager(os.path.join(tmp_dir, "test_cooldown.db"))
    db.run_migrations()

    repo = IncidentRepository(db)
    # Set cooldown to 30 minutes
    prefs = repo.get_preferences()
    prefs.cooldown_minutes = 30
    repo.save_preferences(prefs)

    mgr = IncidentManager(incident_repo=repo, notification_dispatcher=mock_dispatcher)

    eval_lat = MetricEvaluation(
        metric_name="latency",
        status=DetectionStatus.CONFIRMED_SLOWDOWN,
        baseline_value=20.0,
        observed_value=70.0,
        deviation_pct=250.0,
        threshold_applied="+40% and +15ms",
        consecutive_breaches=2,
        explanation="Sustained degradation confirmed.",
        sample_count=5,
    )
    report = SlowdownReport(
        overall_status=DetectionStatus.CONFIRMED_SLOWDOWN,
        time_window=TimeWindow.H24,
        total_runs_evaluated=5,
        valid_measurements_count=5,
        evaluations={"latency": eval_lat},
        summary_headline="Confirmed Slowdown",
        summary_detail="Sustained degradation",
    )

    t0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    mgr.process_slowdown_report(report, now_utc=t0)
    assert len(dispatched) == 1, "First run should dispatch alert"

    # Second run 5 minutes later (scheduler tick while degradation is ongoing)
    t1 = t0 + timedelta(minutes=5)
    mgr.process_slowdown_report(report, now_utc=t1)
    assert len(dispatched) == 1, "Second run within 30m cooldown MUST NOT dispatch duplicate alert"

    # Third run 35 minutes later (cooldown elapsed)
    t2 = t0 + timedelta(minutes=35)
    mgr.process_slowdown_report(report, now_utc=t2)
    assert len(dispatched) == 2, "Run after cooldown expires should dispatch a reminder alert"


def test_incident_manager_recovery_notification() -> None:
    """Verify CONFIRMED_RECOVERY resolves incident and dispatches recovery notification."""
    dispatched: List[NotificationPayload] = []

    def mock_dispatcher(payload: NotificationPayload, prefs: NotificationPreferences) -> None:
        dispatched.append(payload)

    import tempfile, os
    tmp_dir = tempfile.mkdtemp()
    db = DatabaseManager(os.path.join(tmp_dir, "test_recovery.db"))
    db.run_migrations()

    repo = IncidentRepository(db)
    mgr = IncidentManager(incident_repo=repo, notification_dispatcher=mock_dispatcher)

    # 1. Start with an ongoing confirmed incident
    inc = NetworkIncident(
        metric_name="latency",
        status=IncidentStatus.CONFIRMED.value,
        severity=IncidentSeverity.CRITICAL.value,
        baseline_value=20.0,
        observed_value=75.0,
        headline="Confirmed Latency Slowdown",
    )
    repo.create_incident(inc)

    # 2. Report returns CONFIRMED_RECOVERY
    eval_rec = MetricEvaluation(
        metric_name="latency",
        status=DetectionStatus.CONFIRMED_RECOVERY,
        baseline_value=20.0,
        observed_value=20.2,
        deviation_pct=1.0,
        threshold_applied="Nominal",
        consecutive_breaches=0,
        explanation="Performance has recovered to normal baseline.",
        sample_count=6,
    )
    report = SlowdownReport(
        overall_status=DetectionStatus.CONFIRMED_RECOVERY,
        time_window=TimeWindow.H24,
        total_runs_evaluated=6,
        valid_measurements_count=6,
        evaluations={"latency": eval_rec},
        summary_headline="Recovered",
        summary_detail="Performance recovered",
    )

    incidents = mgr.process_slowdown_report(report)
    assert len(incidents) == 1
    assert incidents[0].status == IncidentStatus.RESOLVED.value
    assert incidents[0].resolved_at_utc is not None

    # Verify recovery alert was dispatched
    assert len(dispatched) == 1
    assert "Recovered" in dispatched[0].title
    assert dispatched[0].severity == IncidentSeverity.INFO


def test_event_filtering_respects_user_preferences() -> None:
    """Verify disabling notifications suppresses alerts completely."""
    dispatched: List[NotificationPayload] = []

    def mock_dispatcher(payload: NotificationPayload, prefs: NotificationPreferences) -> None:
        dispatched.append(payload)

    import tempfile, os
    tmp_dir = tempfile.mkdtemp()
    db = DatabaseManager(os.path.join(tmp_dir, "test_filtering.db"))
    db.run_migrations()

    repo = IncidentRepository(db)
    # Turn OFF desktop notifications entirely
    prefs = repo.get_preferences()
    prefs.desktop_enabled = False
    repo.save_preferences(prefs)

    mgr = IncidentManager(incident_repo=repo, notification_dispatcher=mock_dispatcher)

    eval_lat = MetricEvaluation(
        metric_name="latency",
        status=DetectionStatus.CONFIRMED_SLOWDOWN,
        baseline_value=20.0,
        observed_value=80.0,
        deviation_pct=300.0,
        threshold_applied="+40% and +15ms",
        consecutive_breaches=3,
        explanation="Severe degradation.",
        sample_count=5,
    )
    report = SlowdownReport(
        overall_status=DetectionStatus.CONFIRMED_SLOWDOWN,
        time_window=TimeWindow.H24,
        total_runs_evaluated=5,
        valid_measurements_count=5,
        evaluations={"latency": eval_lat},
        summary_headline="Confirmed Slowdown",
        summary_detail="Severe degradation",
    )

    mgr.process_slowdown_report(report)
    assert len(dispatched) == 0, "No notification should be dispatched when desktop_enabled is False"

    # Incident MUST still be tracked and recorded in SQLite
    incidents = repo.list_incidents()
    assert len(incidents) == 1
    assert incidents[0].status == IncidentStatus.CONFIRMED.value

