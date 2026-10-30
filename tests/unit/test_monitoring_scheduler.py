"""Unit tests for TicNet Network Monitoring Scheduler and Persistence (Stage 1).

Verifies:
- Schedule creation, editing, persistence, and disabling.
- Correct next-run calculation for 30M, 1H, 2H, and DAILY intervals.
- Missed-run detection on application startup and skipped run recording.
- Preventing overlapping runs when diagnostic engine is actively executing.
- Execution completion, status updates, and honest empirical metric extraction.
- Pause and resume state transitions and next-run recalculations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from ticnet.models.entities import Observation, Summary, Target
from ticnet.models.enums import MeasurementStatus, TargetType
from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringStatus,
    MonitoringTriggerType,
)
from ticnet.monitoring.scheduler import MonitoringScheduler, calculate_next_run
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.monitoring_repo import MonitoringRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh SQLite database with migrations applied."""
    db_file = tmp_path / "test_monitoring.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


def test_calculate_next_run_intervals() -> None:
    """Verify next run calculation across all supported intervals in IST."""
    from ticnet.core.timezone import IST
    base_time = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)  # 15:30 IST

    # 1. 30 Minutes
    next_30m = calculate_next_run(base_time, IntervalType.M30.value)
    assert next_30m == base_time + timedelta(minutes=30)

    # 2. 1 Hour
    next_1h = calculate_next_run(base_time, IntervalType.H1.value)
    assert next_1h == base_time + timedelta(hours=1)

    # 3. 2 Hours
    next_2h = calculate_next_run(base_time, IntervalType.H2.value)
    assert next_2h == base_time + timedelta(hours=2)

    # 4. Daily IST (later today at 18:00 IST)
    next_daily_today = calculate_next_run(base_time, IntervalType.DAILY.value, daily_time_utc="18:00")
    assert next_daily_today == datetime(2026, 10, 29, 18, 0, 0, tzinfo=IST)

    # 5. Daily IST (already passed today at 08:00 IST -> scheduled for tomorrow at 08:00 IST)
    next_daily_tomorrow = calculate_next_run(base_time, IntervalType.DAILY.value, daily_time_utc="08:00")
    assert next_daily_tomorrow == datetime(2026, 10, 30, 8, 0, 0, tzinfo=IST)


def test_schedule_persistence_and_editing(test_db: DatabaseManager) -> None:
    """Verify schedule creation, editing, persistence, and state transitions."""
    scheduler = MonitoringScheduler(db=test_db)

    # Initial state: default not configured
    sched = scheduler.get_or_create_schedule()
    assert sched.schedule_id == "default"
    assert sched.enabled is False
    assert sched.status == MonitoringStatus.NOT_CONFIGURED.value
    assert sched.next_run_due_utc is None

    # Save enabled schedule (1H)
    updated = scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)
    assert updated.enabled is True
    assert updated.status == MonitoringStatus.ENABLED.value
    assert updated.interval_type == IntervalType.H1.value
    assert updated.next_run_due_utc is not None

    # Check persistence in fresh repository
    repo = MonitoringRepository(test_db)
    persisted = repo.get_schedule("default")
    assert persisted is not None
    assert persisted.enabled is True
    assert persisted.status == MonitoringStatus.ENABLED.value
    assert persisted.next_run_due_utc == updated.next_run_due_utc

    # Disable schedule
    disabled = scheduler.save_schedule(enabled=False, interval_type=IntervalType.H1.value)
    assert disabled.enabled is False
    assert disabled.status == MonitoringStatus.NOT_CONFIGURED.value
    assert disabled.next_run_due_utc is None


def test_pause_and_resume_schedule(test_db: DatabaseManager) -> None:
    """Verify pause and resume controls and next run recalculation."""
    scheduler = MonitoringScheduler(db=test_db)
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H2.value)

    # Pause
    paused = scheduler.pause_monitoring()
    assert paused.status == MonitoringStatus.PAUSED.value
    assert scheduler.is_run_due() is False

    # Resume
    resumed = scheduler.resume_monitoring()
    assert resumed.status == MonitoringStatus.ENABLED.value
    assert resumed.next_run_due_utc is not None


def test_missed_run_handling(test_db: DatabaseManager) -> None:
    """Verify scheduler detects missed schedules on startup and records skipped run."""
    scheduler = MonitoringScheduler(db=test_db)
    repo = MonitoringRepository(test_db)

    # Simulate schedule configured for 1 hour ago
    past_due = datetime.now(timezone.utc) - timedelta(hours=1)
    sched = MonitoringSchedule(
        schedule_id="default",
        enabled=True,
        interval_type=IntervalType.H1.value,
        daily_time_utc="09:00",
        status=MonitoringStatus.ENABLED.value,
        next_run_due_utc=past_due.isoformat(),
        updated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_schedule(sched)

    # Trigger missed run handler
    now_utc = datetime.now(timezone.utc)
    skipped = scheduler.handle_missed_runs(now_utc=now_utc)
    assert skipped is not None
    assert skipped.status == MonitoringRunStatus.SKIPPED.value
    assert skipped.skip_reason == "APPLICATION_OFFLINE_MISSED"

    # Verify schedule was advanced to a future time
    refreshed_sched = scheduler.get_or_create_schedule()
    next_due = datetime.fromisoformat(refreshed_sched.next_run_due_utc)
    assert next_due > now_utc


def test_prevent_overlapping_runs(test_db: DatabaseManager) -> None:
    """Verify scheduler skips run and records skip reason when diagnostic engine is active."""
    diagnostic_active = True
    scheduler = MonitoringScheduler(
        db=test_db,
        is_active_checker=lambda: diagnostic_active,
    )
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.M30.value)

    # Trigger scheduled run while diagnostic is active
    m_run, err = scheduler.trigger_run(trigger_type=MonitoringTriggerType.SCHEDULED)
    assert m_run is not None
    assert err == "DIAGNOSTIC_ACTIVE_DEFERRED"
    assert m_run.status == MonitoringRunStatus.SKIPPED.value
    assert m_run.skip_reason == "DIAGNOSTIC_ACTIVE_DEFERRED"

    # Verify run is persisted in history table
    runs = scheduler.repo.list_monitoring_runs()
    assert len(runs) == 1
    assert runs[0].status == MonitoringRunStatus.SKIPPED.value


def test_normal_run_execution_and_metric_extraction(test_db: DatabaseManager) -> None:
    """Verify run execution dispatch, completion recording, and honest metric extraction."""
    dispatched_run_id = "TCN-20261029-000001"

    scheduler = MonitoringScheduler(
        db=test_db,
        is_active_checker=lambda: False,
        run_starter=lambda: dispatched_run_id,
    )
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    # 1. Dispatch manual run
    m_run, err = scheduler.trigger_run(trigger_type=MonitoringTriggerType.MANUAL)
    assert err is None
    assert m_run is not None
    assert m_run.status == MonitoringRunStatus.RUNNING.value
    assert m_run.run_id == dispatched_run_id
    assert m_run.trigger_type == MonitoringTriggerType.MANUAL.value

    # 2. Insert genuine test run and summary metrics in database
    with test_db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO test_runs (run_id, start_time_utc, status, run_mode, app_version, config_version)
            VALUES (?, ?, 'COMPLETED', 'OPERATIONAL', '1.0', '1.0');
            """,
            (dispatched_run_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            """
            INSERT INTO targets (target_id, target_type, hostname, ip_address, is_active)
            VALUES ('ext-1', 'EXTERNAL_IP', '1.1.1.1', '1.1.1.1', 1);
            """
        )

    summary_repo = SummaryRepository(test_db)
    summary_repo.create_summaries_batch([
        Summary(
            summary_id="s1",
            run_id=dispatched_run_id,
            target_id="ext-1",
            metric_name="round_trip_time",
            statistic_name="median",
            value_numeric=18.4,
            unit="ms",
            sample_count_total=10,
            sample_count_valid=10,
            calculation_method="MEDIAN",
            calculation_version="1.0",
        ),
        Summary(
            summary_id="s2",
            run_id=dispatched_run_id,
            target_id="ext-1",
            metric_name="delay_variation",
            statistic_name="mean",
            value_numeric=1.85,
            unit="ms",
            sample_count_total=10,
            sample_count_valid=10,
            calculation_method="MEAN",
            calculation_version="1.0",
        ),
        Summary(
            summary_id="s3",
            run_id=dispatched_run_id,
            target_id="ext-1",
            metric_name="packet_loss",
            statistic_name="loss_percentage",
            value_numeric=0.0,
            unit="percent",
            sample_count_total=10,
            sample_count_valid=10,
            calculation_method="PERCENTAGE",
            calculation_version="1.0",
        ),
        Summary(
            summary_id="s4",
            run_id=dispatched_run_id,
            target_id=None,
            metric_name="downstream_throughput",
            statistic_name="mean",
            value_numeric=88.5,
            unit="Mbps",
            sample_count_total=1,
            sample_count_valid=1,
            calculation_method="MEAN",
            calculation_version="1.0",
        ),
    ])

    # 3. Record completion
    completed_run = scheduler.record_run_completion(
        monitoring_run_id=m_run.monitoring_run_id,
        run_id=dispatched_run_id,
        status="COMPLETED",
    )
    assert completed_run is not None
    assert completed_run.status == "COMPLETED"
    assert completed_run.latency_ms == 18.4
    assert completed_run.jitter_ms == 1.85
    assert completed_run.packet_loss_pct == 0.0
    assert completed_run.download_mbps == 88.5
    # Honest missing metrics: upload speed was not measured, must be None
    assert completed_run.upload_mbps is None


def test_get_latest_monitoring_run(test_db: DatabaseManager) -> None:
    """Verify repo.get_latest_monitoring_run returns the newest record."""
    repo = MonitoringRepository(test_db)
    sched = MonitoringSchedule(schedule_id="default")
    repo.save_schedule(sched)

    # Empty table returns None
    assert repo.get_latest_monitoring_run() is None

    # Insert older and newer runs
    t1 = "2026-10-29T10:00:00+00:00"
    t2 = "2026-10-29T11:00:00+00:00"
    r1 = MonitoringRun(schedule_id="default", scheduled_for_utc=t1)
    r2 = MonitoringRun(schedule_id="default", scheduled_for_utc=t2)
    repo.create_monitoring_run(r1)
    repo.create_monitoring_run(r2)

    latest = repo.get_latest_monitoring_run()
    assert latest is not None
    assert latest.scheduled_for_utc == t2


def test_invalid_daily_time_fallback() -> None:
    """Verify malformed daily time strings fallback gracefully to 09:00 UTC."""
    from ticnet.monitoring.scheduler import parse_daily_time

    assert parse_daily_time(None) == (9, 0)
    assert parse_daily_time("") == (9, 0)
    assert parse_daily_time("invalid") == (9, 0)
    assert parse_daily_time("abc:def") == (9, 0)
    assert parse_daily_time("14:30") == (14, 30)


def test_app_restart_persistence_and_due_check(test_db: DatabaseManager) -> None:
    """Verify schedule state persists across scheduler restarts and computes due state accurately."""
    # First instance: user configures schedule 2 hours from now
    now_utc = datetime.now(timezone.utc)
    s1 = MonitoringScheduler(db=test_db)
    s1.save_schedule(enabled=True, interval_type=IntervalType.H2.value)

    # Simulate app close and relaunch (fresh scheduler instance)
    s2 = MonitoringScheduler(db=test_db)
    reloaded = s2.get_or_create_schedule()
    assert reloaded.enabled is True
    assert reloaded.status == MonitoringStatus.ENABLED.value
    assert reloaded.interval_type == IntervalType.H2.value
    assert s2.is_run_due(now_utc=now_utc) is False

    # Simulate time advanced by 2 hours and 1 minute
    future_utc = now_utc + timedelta(hours=2, minutes=1)
    assert s2.is_run_due(now_utc=future_utc) is True


def test_trigger_run_with_failing_starter(test_db: DatabaseManager) -> None:
    """Verify scheduler records FAILED run and error message if background worker fails to launch."""
    def failing_starter():
        raise RuntimeError("Network interface unavailable")

    scheduler = MonitoringScheduler(
        db=test_db,
        is_active_checker=lambda: False,
        run_starter=failing_starter,
    )
    scheduler.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    m_run, err = scheduler.trigger_run(trigger_type=MonitoringTriggerType.SCHEDULED)
    assert m_run is not None
    assert m_run.status == MonitoringRunStatus.FAILED.value
    assert "Network interface unavailable" in (m_run.error_message or "")
    assert err is not None

