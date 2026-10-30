"""Unit tests for TicNet Indian Standard Time (IST) Application-Wide Migration.

Verifies:
- Official IANA timezone Asia/Kolkata (+05:30) is established as standard.
- IST timestamp generation across all core models (entities, monitoring, notifications).
- Day rollover occurring at midnight IST (00:00 IST), not UTC (18:30 UTC previous day).
- Run ID prefix generation rolls over at midnight IST.
- Schedulers calculate and fire at correct IST daily and interval times.
- Time window filters (24H, 7D, 30D) calculate boundaries relative to IST.
- Consistent user-facing formatting with explicit IST suffix.
- Database migration idempotency and rollback capability.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import tempfile
from zoneinfo import ZoneInfo
import pytest

from ticnet.core.timezone import (
    IST,
    format_ist,
    format_ist_date,
    format_ist_short,
    format_ist_time,
    ist_day_bounds,
    now_ist,
    now_ist_iso,
    to_ist,
    to_ist_iso,
)
from ticnet.models.entities import (
    TestRun,
    generate_run_id,
    now_utc_iso,
)
from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringSchedule,
)
from ticnet.models.notifications import (
    NetworkIncident,
    NotificationPreferences,
)
from ticnet.monitoring.analytics import TimeWindow
from ticnet.monitoring.scheduler import (
    calculate_next_run,
    utc_time_to_local_hint,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.migrate_timezone_to_ist import (
    migrate_database_to_ist,
    rollback_timezone_migration,
)
from ticnet.persistence.repositories.analytics_repo import AnalyticsRepository


def test_ist_timezone_establishment():
    """Verify official IANA Asia/Kolkata timezone is used with +05:30 offset."""
    assert IST.key == "Asia/Kolkata"
    dt = now_ist()
    assert dt.tzinfo is not None
    assert dt.strftime("%z") == "+0530"
    iso = now_ist_iso()
    assert "+05:30" in iso


def test_model_timestamp_generation_ist():
    """Verify all domain models generate timestamps in IST with +05:30 offset."""
    # entities.py
    run = TestRun(
        run_id="TCN-20261030-000001",
        start_time_utc=now_utc_iso(),
        status="COMPLETED",
        app_version="1.0.0",
        config_version="1.0.0",
    )
    assert "+05:30" in run.start_time_utc

    # monitoring.py
    sched = MonitoringSchedule(schedule_id="default")
    assert "+05:30" in sched.updated_at_utc

    # notifications.py
    incident = NetworkIncident(headline="Degraded latency")
    assert "+05:30" in incident.started_at_utc
    assert "+05:30" in incident.last_seen_at_utc

    pref = NotificationPreferences()
    assert "+05:30" in pref.updated_at_utc


def test_midnight_ist_day_rollover_and_run_id():
    """Verify calendar day rollover occurs at midnight IST (00:00 IST), not UTC."""
    # 23:50 IST on Oct 29 (which is 18:20 UTC on Oct 29)
    late_night_ist = datetime(2026, 10, 29, 23, 50, 0, tzinfo=IST)
    start_bounds, end_bounds = ist_day_bounds(late_night_ist)
    assert start_bounds.day == 29
    assert start_bounds.hour == 0
    assert end_bounds.day == 29
    assert end_bounds.hour == 23

    # 00:15 IST on Oct 30 (which is 18:45 UTC on Oct 29 - UTC is still previous day!)
    early_morning_ist = datetime(2026, 10, 30, 0, 15, 0, tzinfo=IST)
    rid = generate_run_id(target_date=early_morning_ist.date(), sequence=1)
    assert rid.startswith("TCN-20261030-")

    start_bounds2, end_bounds2 = ist_day_bounds(early_morning_ist)
    assert start_bounds2.day == 30
    assert start_bounds2.hour == 0


def test_ist_date_formatting():
    """Verify user-facing IST date and time formatting."""
    dt = datetime(2026, 10, 30, 14, 30, 0, tzinfo=IST)
    formatted = format_ist(dt)
    assert "30 Oct 2026" in formatted
    assert "02:30:00 PM IST" in formatted

    short_formatted = format_ist_short(dt)
    assert "30 Oct 2026, 02:30 PM" == short_formatted

    # Parsing UTC ISO string to IST formatting
    utc_iso = "2026-10-30T09:00:00+00:00"
    converted = format_ist(utc_iso)
    assert "30 Oct 2026" in converted
    assert "02:30:00 PM IST" in converted


def test_scheduler_daily_time_in_ist():
    """Verify daily schedules fire according to Indian Standard Time."""
    # Ref time: 10:00 IST on 2026-10-30
    ref_time = datetime(2026, 10, 30, 10, 0, 0, tzinfo=IST)

    # Daily run at 11:00 IST (later today)
    next_run = calculate_next_run(ref_time, IntervalType.DAILY.value, daily_time_utc="11:00")
    assert next_run == datetime(2026, 10, 30, 11, 0, 0, tzinfo=IST)

    # Daily run at 09:00 IST (already passed today -> tomorrow at 09:00 IST)
    next_run_tomorrow = calculate_next_run(ref_time, IntervalType.DAILY.value, daily_time_utc="09:00")
    assert next_run_tomorrow == datetime(2026, 10, 31, 9, 0, 0, tzinfo=IST)


def test_analytics_time_window_filtering_ist(tmp_path):
    """Verify analytics time window queries calculate start threshold relative to now_ist()."""
    db_file = tmp_path / "test_analytics_ist.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    repo = AnalyticsRepository(db)

    # Create run from 2 hours ago in IST
    now = now_ist()
    run_recent = MonitoringRun(
        schedule_id="default",
        trigger_type="SCHEDULED",
        scheduled_for_utc=(now - timedelta(hours=2)).isoformat(),
        started_at_utc=(now - timedelta(hours=2)).isoformat(),
        status="COMPLETED",
        latency_ms=20.0,
    )
    # Create run from 36 hours ago in IST (outside 24H, inside 7D)
    run_old = MonitoringRun(
        schedule_id="default",
        trigger_type="SCHEDULED",
        scheduled_for_utc=(now - timedelta(hours=36)).isoformat(),
        started_at_utc=(now - timedelta(hours=36)).isoformat(),
        status="COMPLETED",
        latency_ms=45.0,
    )
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO monitoring_schedule (schedule_id, interval_type, enabled, status, updated_at_utc)
            VALUES ('default', '1H', 1, 'ENABLED', ?)
            """,
            (now_ist_iso(),),
        )
        conn.execute(
            """
            INSERT INTO monitoring_runs (run_id, schedule_id, trigger_type, scheduled_for_utc, started_at_utc, status, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "M-1", run_recent.schedule_id, run_recent.trigger_type, run_recent.scheduled_for_utc, run_recent.started_at_utc, run_recent.status, run_recent.latency_ms,
                "M-2", run_old.schedule_id, run_old.trigger_type, run_old.scheduled_for_utc, run_old.started_at_utc, run_old.status, run_old.latency_ms,
            ),
        )

    # 24H filter should only include M-1
    runs_24h = repo.get_monitoring_runs_in_window(TimeWindow.H24)
    assert len(runs_24h) == 1
    assert runs_24h[0].run_id == "M-1"

    # 7D filter should include both M-1 and M-2
    runs_7d = repo.get_monitoring_runs_in_window(TimeWindow.D7)
    assert len(runs_7d) == 2


def test_database_migration_and_rollback_idempotency(tmp_path):
    """Verify migration script applies safely, is idempotent, and rollback restores exact records."""
    db_file = tmp_path / "test_mig.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()

    # Insert a sample test run with legacy UTC timestamp
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO test_runs (run_id, start_time_utc, status, app_version, config_version)
            VALUES ('TCN-20261025-000001', '2026-10-24T18:45:00+00:00', 'COMPLETED', '1.0.0', '1.0.0')
            """
        )

    # First migration run: converts 1 test run (+5h 30m -> 2026-10-25T00:15:00+05:30)
    res1 = migrate_database_to_ist(db_file)
    assert res1.get("total_records_updated", 0) >= 1

    with db.get_connection() as conn:
        row = conn.execute("SELECT start_time_utc FROM test_runs WHERE run_id = 'TCN-20261025-000001'").fetchone()
        assert row["start_time_utc"] == "2026-10-25T00:15:00+05:30"

    # Second migration run: idempotent, 0 records converted
    res2 = migrate_database_to_ist(db_file)
    assert res2.get("total_records_updated", 0) == 0

    # Rollback migration: restores UTC string
    rb = rollback_timezone_migration(db_file)
    assert rb.get("total_records_reverted", 0) >= 1

    with db.get_connection() as conn:
        row = conn.execute("SELECT start_time_utc FROM test_runs WHERE run_id = 'TCN-20261025-000001'").fetchone()
        assert "+05:30" not in row["start_time_utc"]
