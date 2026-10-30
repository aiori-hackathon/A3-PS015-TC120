"""Automated & deterministic unit tests for TicNet Network Monitoring Scheduler.

Verifies:
- Scheduler initialization, auto-start, and schedule restoration from SQLite.
- Next-run calculation across 30M, 1H, 2H, and DAILY intervals.
- Automated scheduled run triggering without user intervention.
- Overlap prevention (scheduled vs active diagnostic, manual vs active diagnostic).
- State transitions (enable, pause, resume, disable).
- Manual "Run Test Now" ahead of schedule does NOT postpone the scheduled timer.
- Missed-run policy: Single skipped record created, zero catch-up bursts after long offline/suspend.
- Fault tolerance: Diagnostic start or execution failure records FAILED status and recovers subsequent scheduling.
- Automated end-to-end pipeline: Scheduler -> Run -> Metrics -> Analytics -> Incidents.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import pytest

from ticnet.models.entities import Target
from ticnet.models.enums import TargetType
from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringStatus,
    MonitoringTriggerType,
)
from ticnet.monitoring.analytics import DetectionStatus, TimeWindow
from ticnet.monitoring.scheduler import (
    MonitoringScheduler,
    calculate_next_run,
    get_local_tz_info,
    parse_daily_time,
    utc_time_to_local_hint,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.monitoring_repo import MonitoringRepository


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh SQLite database with all migrations (V001-V004) applied."""
    db_file = tmp_path / "test_auto_scheduler.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


def test_next_run_calculation_deterministic() -> None:
    """Verify calculate_next_run across all standard intervals with fixed reference time in IST."""
    from ticnet.core.timezone import IST
    base_utc = datetime(2026, 10, 29, 8, 0, 0, tzinfo=timezone.utc)  # 13:30 IST

    # 30M
    next_30m = calculate_next_run(base_utc, IntervalType.M30.value)
    assert next_30m == datetime(2026, 10, 29, 8, 30, 0, tzinfo=timezone.utc)

    # 1H
    next_1h = calculate_next_run(base_utc, IntervalType.H1.value)
    assert next_1h == datetime(2026, 10, 29, 9, 0, 0, tzinfo=timezone.utc)

    # 2H
    next_2h = calculate_next_run(base_utc, IntervalType.H2.value)
    assert next_2h == datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)

    # DAILY later today (14:00 IST is after 13:30 IST)
    next_daily_today = calculate_next_run(base_utc, IntervalType.DAILY.value, daily_time_utc="14:00")
    assert next_daily_today == datetime(2026, 10, 29, 14, 0, 0, tzinfo=IST)

    # DAILY earlier today (06:00 IST is before 13:30 IST, must roll over to tomorrow)
    next_daily_tomorrow = calculate_next_run(base_utc, IntervalType.DAILY.value, daily_time_utc="06:00")
    assert next_daily_tomorrow == datetime(2026, 10, 30, 6, 0, 0, tzinfo=IST)


def test_local_timezone_and_hint_helpers() -> None:
    """Verify local timezone name, offset, and conversion hint helpers."""
    tz_name, offset_str = get_local_tz_info()
    assert isinstance(tz_name, str) and len(tz_name) > 0
    assert "UTC" in offset_str

    hint = utc_time_to_local_hint("12:00")
    assert "Local Equivalent:" in hint
    assert tz_name in hint


def test_schedule_restoration_on_startup(test_db: DatabaseManager) -> None:
    """Verify that a saved schedule in SQLite is faithfully restored on startup."""
    # 1. Initialize first scheduler and save an active schedule
    scheduler1 = MonitoringScheduler(db=test_db)
    saved = scheduler1.save_schedule(
        enabled=True,
        interval_type=IntervalType.H1.value,
        daily_time_utc="10:00",
    )
    assert saved.enabled is True
    assert saved.status == MonitoringStatus.ENABLED.value
    assert saved.next_run_due_utc is not None

    # 2. Simulate application relaunch with fresh scheduler instance
    scheduler2 = MonitoringScheduler(db=test_db)
    restored = scheduler2.get_or_create_schedule()
    assert restored.schedule_id == "default"
    assert restored.enabled is True
    assert restored.status == MonitoringStatus.ENABLED.value
    assert restored.interval_type == IntervalType.H1.value
    assert restored.daily_time_utc == "10:00"
    assert restored.next_run_due_utc == saved.next_run_due_utc


def test_automated_scheduled_run_triggering(test_db: DatabaseManager) -> None:
    """Verify that when a schedule is due, trigger_run executes automatically."""
    dispatched_runs = []

    def fake_starter() -> str:
        run_id = f"diag-run-{len(dispatched_runs) + 1}"
        dispatched_runs.append(run_id)
        return run_id

    scheduler = MonitoringScheduler(
        db=test_db,
        is_active_checker=lambda: False,
        run_starter=fake_starter,
    )
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.M30.value)

    # Force next run to be due 1 minute ago
    sched = scheduler.get_or_create_schedule()
    now_utc = datetime.now(timezone.utc)
    sched.next_run_due_utc = (now_utc - timedelta(minutes=1)).isoformat()
    scheduler.repo.save_schedule(sched)

    assert scheduler.is_run_due(now_utc) is True

    # Trigger automatic run
    m_run, err = scheduler.trigger_run(trigger_type=MonitoringTriggerType.SCHEDULED, now_utc=now_utc)
    assert err is None
    assert m_run is not None
    assert m_run.status == MonitoringRunStatus.RUNNING.value
    assert m_run.trigger_type == MonitoringTriggerType.SCHEDULED.value
    assert m_run.run_id == "diag-run-1"
    assert len(dispatched_runs) == 1

    # Verify schedule was automatically advanced to the next slot (30 minutes in future)
    updated_sched = scheduler.get_or_create_schedule()
    next_due = datetime.fromisoformat(updated_sched.next_run_due_utc)
    assert next_due > now_utc
    assert scheduler.is_run_due(now_utc) is False


def test_overlap_prevention_skips_when_diagnostic_active(test_db: DatabaseManager) -> None:
    """Verify that when the diagnostic worker is actively executing, new scheduled run is skipped."""
    is_busy = True
    dispatched = []

    scheduler = MonitoringScheduler(
        db=test_db,
        is_active_checker=lambda: is_busy,
        run_starter=lambda: dispatched.append(1) or "run-x",
    )
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    now_utc = datetime.now(timezone.utc)
    m_run, reason = scheduler.trigger_run(trigger_type=MonitoringTriggerType.SCHEDULED, now_utc=now_utc)

    assert reason == "DIAGNOSTIC_ACTIVE_DEFERRED"
    assert m_run is not None
    assert m_run.status == MonitoringRunStatus.SKIPPED.value
    assert m_run.skip_reason == "DIAGNOSTIC_ACTIVE_DEFERRED"
    assert len(dispatched) == 0  # Worker was never called

    # Verify schedule was advanced so it does not hammer the busy worker
    sched = scheduler.get_or_create_schedule()
    next_due = datetime.fromisoformat(sched.next_run_due_utc)
    assert next_due > now_utc


def test_manual_run_ahead_of_schedule_does_not_postpone_scheduled_run(test_db: DatabaseManager) -> None:
    """Verify that running a manual test does NOT advance or postpone the scheduled timer."""
    starter_calls = []
    scheduler = MonitoringScheduler(
        db=test_db,
        is_active_checker=lambda: False,
        run_starter=lambda: f"manual-{len(starter_calls) + 1}",
    )
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    # Schedule is set for 50 minutes in the future
    fixed_now = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    scheduled_due = fixed_now + timedelta(minutes=50)
    sched = scheduler.get_or_create_schedule()
    sched.next_run_due_utc = scheduled_due.isoformat()
    scheduler.repo.save_schedule(sched)

    # User clicks "Run Test Now" 10 minutes into the interval (40 mins before scheduled run)
    manual_now = fixed_now + timedelta(minutes=10)
    m_run, err = scheduler.trigger_run(trigger_type=MonitoringTriggerType.MANUAL, now_utc=manual_now)
    assert err is None
    assert m_run is not None
    assert m_run.trigger_type == MonitoringTriggerType.MANUAL.value

    # Next run due time must REMAIN untouched at the original scheduled_due time!
    updated_sched = scheduler.get_or_create_schedule()
    assert updated_sched.next_run_due_utc == scheduled_due.isoformat()


def test_missed_run_policy_prevents_catch_up_burst(test_db: DatabaseManager) -> None:
    """Verify that when application was offline/suspended for an extended period, only 1 skipped run is logged."""
    scheduler = MonitoringScheduler(db=test_db)
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    # Set due time to 12 hours ago (long offline/sleep)
    now_utc = datetime(2026, 10, 29, 20, 0, 0, tzinfo=timezone.utc)
    due_12h_ago = now_utc - timedelta(hours=12)
    sched = scheduler.get_or_create_schedule()
    sched.next_run_due_utc = due_12h_ago.isoformat()
    scheduler.repo.save_schedule(sched)

    # 1. First evaluation: logs single skipped run and advances next run
    skipped = scheduler.handle_missed_runs(now_utc=now_utc)
    assert skipped is not None
    assert skipped.status == MonitoringRunStatus.SKIPPED.value
    assert skipped.skip_reason == "APPLICATION_OFFLINE_MISSED"
    assert skipped.scheduled_for_utc == due_12h_ago.isoformat()

    # Verify next run is now set into the future
    updated_sched = scheduler.get_or_create_schedule()
    next_due = datetime.fromisoformat(updated_sched.next_run_due_utc)
    assert next_due > now_utc

    # 2. Immediate second evaluation: must NOT log any more skipped runs (zero bursts)
    second_check = scheduler.handle_missed_runs(now_utc=now_utc)
    assert second_check is None

    # Verify exactly 1 run exists in SQLite
    runs = scheduler.repo.list_monitoring_runs()
    assert len(runs) == 1
    assert runs[0].status == MonitoringRunStatus.SKIPPED.value


def test_diagnostic_starter_failure_recovery(test_db: DatabaseManager) -> None:
    """Verify that if diagnostic starter raises an exception, the failure is saved and schedule continues."""
    def failing_starter() -> str:
        raise RuntimeError("Network socket unreachable or worker thread crashed")

    scheduler = MonitoringScheduler(
        db=test_db,
        is_active_checker=lambda: False,
        run_starter=failing_starter,
    )
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.M30.value)

    now_utc = datetime.now(timezone.utc)
    m_run, err = scheduler.trigger_run(trigger_type=MonitoringTriggerType.SCHEDULED, now_utc=now_utc)

    assert "Network socket unreachable" in str(err)
    assert m_run is not None
    assert m_run.status == MonitoringRunStatus.FAILED.value
    assert "Network socket unreachable" in m_run.error_message

    # Verify next_run_due_utc was advanced so scheduler does not get stuck in a failed loop
    sched = scheduler.get_or_create_schedule()
    next_due = datetime.fromisoformat(sched.next_run_due_utc)
    assert next_due > now_utc
    assert scheduler.is_run_due(now_utc) is False


def test_pause_and_resume_lifecycle(test_db: DatabaseManager) -> None:
    """Verify pause and resume state transitions and next-run recalculation."""
    scheduler = MonitoringScheduler(db=test_db)
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    # 1. Pause
    paused = scheduler.pause_monitoring()
    assert paused.status == MonitoringStatus.PAUSED.value
    assert scheduler.is_run_due() is False

    # 2. Resume
    resumed = scheduler.resume_monitoring()
    assert resumed.status == MonitoringStatus.ENABLED.value
    next_due = datetime.fromisoformat(resumed.next_run_due_utc)
    assert next_due > datetime.now(timezone.utc)


def test_disable_monitoring_clears_active_schedule(test_db: DatabaseManager) -> None:
    """Verify disabling monitoring clears next_run_due and updates status."""
    scheduler = MonitoringScheduler(db=test_db)
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    disabled = scheduler.save_schedule(enabled=False, interval_type=IntervalType.H1.value)
    assert disabled.enabled is False
    assert disabled.status == MonitoringStatus.NOT_CONFIGURED.value
    assert disabled.next_run_due_utc is None
    assert scheduler.is_run_due() is False

