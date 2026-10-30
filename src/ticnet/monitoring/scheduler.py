"""Periodic network monitoring scheduling engine and execution coordinator.

Compliant with TicNet Stage 1 requirements:
- Reliable interval calculation (30m, 1h, 2h, Daily UTC).
- Overlap prevention (never overlaps active diagnostic worker).
- Missed schedule handling on application restart.
- SQLite persistence of schedule configuration and run history.
- Honest empirical metrics extraction (no fabricated data).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from ticnet.core.timezone import IST, UTC, now_ist, now_ist_iso, to_ist, format_ist
import logging
from typing import Callable, Dict, Optional, Tuple

from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringStatus,
    MonitoringTriggerType,
    now_utc_iso,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.monitoring_repo import MonitoringRepository
from ticnet.utils.logging import get_logger

logger = get_logger("monitoring.scheduler")


def parse_daily_time(time_str: Optional[str]) -> Tuple[int, int]:
    """Parse HH:MM formatted string into (hour, minute). Defaults to (9, 0)."""
    if not time_str or ":" not in time_str:
        return 9, 0
    try:
        parts = time_str.strip().split(":")
        h = int(parts[0]) % 24
        m = int(parts[1]) % 60
        return h, m
    except Exception:
        return 9, 0


def calculate_next_run(
    now_utc: datetime,
    interval_type: str,
    daily_time_utc: Optional[str] = "09:00",
) -> datetime:
    """Calculate the next scheduled execution timestamp in UTC.

    Args:
        now_utc: Current UTC timestamp (timezone-aware).
        interval_type: '30M', '1H', '2H', or 'DAILY'.
        daily_time_utc: 'HH:MM' string for DAILY schedules.

    Returns:
        Next scheduled UTC datetime.
    """
    now_ist_dt = to_ist(now_utc) or now_ist()

    if interval_type == IntervalType.M30.value:
        return now_ist_dt + timedelta(minutes=30)
    elif interval_type == IntervalType.H1.value:
        return now_ist_dt + timedelta(hours=1)
    elif interval_type == IntervalType.H2.value:
        return now_ist_dt + timedelta(hours=2)
    elif interval_type == IntervalType.DAILY.value:
        hour, minute = parse_daily_time(daily_time_utc)
        target_today = datetime(
            year=now_ist_dt.year,
            month=now_ist_dt.month,
            day=now_ist_dt.day,
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
            tzinfo=IST,
        )
        if target_today > now_ist_dt:
            return target_today
        else:
            return target_today + timedelta(days=1)
    else:
        return now_ist_dt + timedelta(hours=1)


def utc_time_to_local_hint(daily_time_utc: Optional[str]) -> str:
    """Format confirmation hint identifying Indian Standard Time."""
    h, m = parse_daily_time(daily_time_utc)
    tz_name, _ = get_local_tz_info()
    return f"(Local Equivalent: {h:02d}:{m:02d} {tz_name} (IST) • Asia/Kolkata, UTC+05:30)"


def get_local_tz_info() -> Tuple[str, str]:
    """Return (timezone_name, formatted_offset), e.g. ('IST', 'UTC+05:30')."""
    now_local = datetime.now().astimezone()
    name = now_local.tzname() or "Local"
    offset = now_local.strftime("%z")
    if len(offset) == 5:
        formatted_offset = f"UTC{offset[:3]}:{offset[3:]}"
    else:
        formatted_offset = "UTC"
    return name, formatted_offset


class MonitoringScheduler:
    """Manages monitoring schedule state, interval ticks, and non-overlapping executions."""

    def __init__(
        self,
        db: DatabaseManager,
        is_active_checker: Optional[Callable[[], bool]] = None,
        run_starter: Optional[Callable[[], str]] = None,
    ) -> None:
        """Initialize scheduler with database and diagnostic triggers.

        Args:
            db: DatabaseManager instance.
            is_active_checker: Callable returning True if diagnostic worker is busy.
            run_starter: Callable triggering a background diagnostic run and returning run_id.
        """
        self.db = db
        self.repo = MonitoringRepository(db)
        self.is_active_checker = is_active_checker or (lambda: False)
        self.run_starter = run_starter

    def get_or_create_schedule(self) -> MonitoringSchedule:
        """Retrieve existing schedule configuration or create default."""
        schedule = self.repo.get_schedule("default")
        if schedule is None:
            schedule = MonitoringSchedule(
                schedule_id="default",
                enabled=False,
                interval_type=IntervalType.H1.value,
                daily_time_utc="09:00",
                status=MonitoringStatus.NOT_CONFIGURED.value,
                next_run_due_utc=None,
                last_run_at_utc=None,
                last_run_id=None,
                updated_at_utc=now_utc_iso(),
            )
            self.repo.save_schedule(schedule)
        return schedule

    def save_schedule(
        self,
        enabled: bool,
        interval_type: str,
        daily_time_utc: Optional[str] = "09:00",
    ) -> MonitoringSchedule:
        """Update and persist schedule configuration, calculating next run time."""
        now_utc = now_ist()
        schedule = self.get_or_create_schedule()
        schedule.enabled = enabled
        schedule.interval_type = interval_type
        schedule.daily_time_utc = daily_time_utc or "09:00"

        if enabled:
            schedule.status = MonitoringStatus.ENABLED.value
            next_run = calculate_next_run(now_utc, interval_type, daily_time_utc)
            schedule.next_run_due_utc = next_run.isoformat()
        else:
            schedule.status = MonitoringStatus.NOT_CONFIGURED.value
            schedule.next_run_due_utc = None

        schedule.updated_at_utc = now_utc.isoformat()
        return self.repo.save_schedule(schedule)

    def pause_monitoring(self) -> MonitoringSchedule:
        """Pause active monitoring schedule."""
        now_utc = now_ist()
        schedule = self.get_or_create_schedule()
        if schedule.enabled and schedule.status == MonitoringStatus.ENABLED.value:
            schedule.status = MonitoringStatus.PAUSED.value
            schedule.updated_at_utc = now_utc.isoformat()
            self.repo.save_schedule(schedule)
            logger.info("Network monitoring schedule paused")
        return schedule

    def resume_monitoring(self) -> MonitoringSchedule:
        """Resume paused monitoring schedule and compute new next run time."""
        now_utc = now_ist()
        schedule = self.get_or_create_schedule()
        if schedule.enabled and schedule.status == MonitoringStatus.PAUSED.value:
            schedule.status = MonitoringStatus.ENABLED.value
            next_run = calculate_next_run(now_utc, schedule.interval_type, schedule.daily_time_utc)
            schedule.next_run_due_utc = next_run.isoformat()
            schedule.updated_at_utc = now_utc.isoformat()
            self.repo.save_schedule(schedule)
            logger.info("Network monitoring schedule resumed; next run due at %s", schedule.next_run_due_utc)
        return schedule

    def is_run_due(self, now_utc: Optional[datetime] = None) -> bool:
        """Check if a scheduled monitoring run is currently due."""
        schedule = self.get_or_create_schedule()
        if not schedule.enabled or schedule.status != MonitoringStatus.ENABLED.value:
            return False
        if not schedule.next_run_due_utc:
            return False

        if now_utc is None:
            now_utc = now_ist()
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=IST)

        try:
            due_dt = datetime.fromisoformat(schedule.next_run_due_utc)
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=IST)
            return now_utc >= due_dt
        except Exception:
            return False

    def handle_missed_runs(self, now_utc: Optional[datetime] = None) -> Optional[MonitoringRun]:
        """Check for missed schedules (e.g. while TicNet was closed or suspended) and log a skipped run.

        If the scheduled due time passed more than 15 minutes ago, record a single SKIPPED run
        to prevent bursting multiple catch-up runs, and advance to the next upcoming slot.
        """
        schedule = self.get_or_create_schedule()
        if not schedule.enabled or schedule.status != MonitoringStatus.ENABLED.value:
            return None
        if not schedule.next_run_due_utc:
            return None

        if now_utc is None:
            now_utc = now_ist()
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=IST)

        try:
            due_dt = datetime.fromisoformat(schedule.next_run_due_utc)
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=IST)

            if (now_utc - due_dt) > timedelta(minutes=15):
                logger.warning(
                    "Missed scheduled run due at %s (overdue by %s); recording single skipped run",
                    schedule.next_run_due_utc,
                    now_utc - due_dt,
                )
                skipped_run = MonitoringRun(
                    schedule_id=schedule.schedule_id,
                    trigger_type=MonitoringTriggerType.SCHEDULED.value,
                    scheduled_for_utc=schedule.next_run_due_utc,
                    started_at_utc=now_utc.isoformat(),
                    completed_at_utc=now_utc.isoformat(),
                    status=MonitoringRunStatus.SKIPPED.value,
                    skip_reason="APPLICATION_OFFLINE_MISSED",
                    error_message="Scheduled run missed while TicNet was closed, paused, or suspended",
                )
                self.repo.create_monitoring_run(skipped_run)

                # Advance next run to future slot to prevent catch-up burst
                next_run = calculate_next_run(now_utc, schedule.interval_type, schedule.daily_time_utc)
                schedule.next_run_due_utc = next_run.isoformat()
                schedule.updated_at_utc = now_utc.isoformat()
                self.repo.save_schedule(schedule)
                return skipped_run
        except Exception as exc:
            logger.error("Error evaluating missed runs: %s", exc, exc_info=True)
        return None

    def trigger_run(
        self,
        trigger_type: MonitoringTriggerType = MonitoringTriggerType.SCHEDULED,
        now_utc: Optional[datetime] = None,
    ) -> Tuple[Optional[MonitoringRun], Optional[str]]:
        """Trigger a monitoring test session with overlap prevention.

        Returns:
            Tuple of (MonitoringRun, error_message or skip_reason).
        """
        if now_utc is None:
            now_utc = now_ist()
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=IST)

        schedule = self.get_or_create_schedule()

        # 1. Overlap Prevention: Check if active diagnosis is executing
        if self.is_active_checker():
            logger.warning("Diagnostic engine is active; skipping overlapping monitoring run")
            skipped_run = MonitoringRun(
                schedule_id=schedule.schedule_id,
                trigger_type=trigger_type.value,
                scheduled_for_utc=schedule.next_run_due_utc or now_utc.isoformat(),
                started_at_utc=now_utc.isoformat(),
                completed_at_utc=now_utc.isoformat(),
                status=MonitoringRunStatus.SKIPPED.value,
                skip_reason="DIAGNOSTIC_ACTIVE_DEFERRED",
                error_message="Skipped because another diagnostic test was actively executing",
            )
            self.repo.create_monitoring_run(skipped_run)

            # Re-advance next scheduled due time only for scheduled runs or overdue runs
            if trigger_type == MonitoringTriggerType.SCHEDULED and schedule.enabled and schedule.status == MonitoringStatus.ENABLED.value:
                next_run = calculate_next_run(now_utc, schedule.interval_type, schedule.daily_time_utc)
                schedule.next_run_due_utc = next_run.isoformat()
                schedule.updated_at_utc = now_utc.isoformat()
                self.repo.save_schedule(schedule)

            return skipped_run, "DIAGNOSTIC_ACTIVE_DEFERRED"

        # 2. Dispatch run
        scheduled_for = schedule.next_run_due_utc if trigger_type == MonitoringTriggerType.SCHEDULED and schedule.next_run_due_utc else now_utc.isoformat()
        monitoring_run = MonitoringRun(
            schedule_id=schedule.schedule_id,
            trigger_type=trigger_type.value,
            scheduled_for_utc=scheduled_for,
            started_at_utc=now_utc.isoformat(),
            status=MonitoringRunStatus.RUNNING.value,
        )
        self.repo.create_monitoring_run(monitoring_run)

        # Trigger underlying diagnostic run if handler provided
        if self.run_starter:
            try:
                run_id = self.run_starter()
                monitoring_run.run_id = run_id
                self.repo.update_monitoring_run(monitoring_run)
            except Exception as exc:
                logger.error("Failed to start diagnostic session for monitoring: %s", exc, exc_info=True)
                monitoring_run.status = MonitoringRunStatus.FAILED.value
                monitoring_run.completed_at_utc = now_utc_iso()
                monitoring_run.error_message = str(exc)
                self.repo.update_monitoring_run(monitoring_run)

                # Advance next run due time so scheduler tick does not loop-fail forever
                if trigger_type == MonitoringTriggerType.SCHEDULED and schedule.enabled and schedule.status == MonitoringStatus.ENABLED.value:
                    next_run = calculate_next_run(now_utc, schedule.interval_type, schedule.daily_time_utc)
                    schedule.next_run_due_utc = next_run.isoformat()
                    schedule.updated_at_utc = now_utc.isoformat()
                    self.repo.save_schedule(schedule)

                return monitoring_run, str(exc)

        # 3. Advance schedule next run:
        # - Scheduled runs always advance next_run_due_utc.
        # - Manual runs only advance if the schedule was already due/overdue (fulfilling the scheduled run).
        # - A manual run ahead of schedule preserves next_run_due_utc.
        should_advance = False
        if schedule.enabled and schedule.status == MonitoringStatus.ENABLED.value:
            if trigger_type == MonitoringTriggerType.SCHEDULED:
                should_advance = True
            elif schedule.next_run_due_utc:
                try:
                    due_dt = datetime.fromisoformat(schedule.next_run_due_utc)
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.replace(tzinfo=IST)
                    if now_utc >= due_dt:
                        should_advance = True
                except Exception:
                    should_advance = True

        if should_advance:
            next_run = calculate_next_run(now_utc, schedule.interval_type, schedule.daily_time_utc)
            schedule.next_run_due_utc = next_run.isoformat()
            schedule.updated_at_utc = now_utc.isoformat()
            self.repo.save_schedule(schedule)

        return monitoring_run, None

    def record_run_completion(
        self,
        monitoring_run_id: str,
        run_id: Optional[str],
        status: str,
        error_message: Optional[str] = None,
    ) -> Optional[MonitoringRun]:
        """Record diagnostic completion, extract genuine metrics, and update schedule."""
        m_run = self.repo.get_monitoring_run(monitoring_run_id)
        if not m_run:
            logger.warning("Monitoring run %s not found for completion recording", monitoring_run_id)
            return None

        now_utc = now_ist()
        m_run.completed_at_utc = now_utc.isoformat()
        m_run.status = status
        m_run.error_message = error_message
        if run_id:
            m_run.run_id = run_id

        # Extract genuine empirical metrics from SQLite
        if run_id and status in (MonitoringRunStatus.COMPLETED.value, "COMPLETED", "PARTIAL"):
            metrics = self.extract_metrics(run_id)
            m_run.download_mbps = metrics.get("download_mbps")
            m_run.upload_mbps = metrics.get("upload_mbps")
            m_run.latency_ms = metrics.get("latency_ms")
            m_run.jitter_ms = metrics.get("jitter_ms")
            m_run.packet_loss_pct = metrics.get("packet_loss_pct")

        self.repo.update_monitoring_run(m_run)

        # Update schedule last_run
        schedule = self.get_or_create_schedule()
        schedule.last_run_at_utc = m_run.completed_at_utc
        if run_id:
            schedule.last_run_id = run_id
        schedule.updated_at_utc = now_utc.isoformat()
        self.repo.save_schedule(schedule)

        return m_run

    def extract_metrics(self, run_id: str) -> Dict[str, Optional[float]]:
        """Extract genuine empirical metrics from SQLite for a completed run."""
        metrics: Dict[str, Optional[float]] = {
            "download_mbps": None,
            "upload_mbps": None,  # Honestly unsupported in operational profile
            "latency_ms": None,
            "jitter_ms": None,
            "packet_loss_pct": None,
        }

        try:
            with self.db.connection() as conn:
                # 1. External latency (median/mean round_trip_time)
                cur = conn.execute(
                    """
                    SELECT value_numeric FROM summaries 
                    WHERE run_id = ? AND metric_name = 'round_trip_time' 
                      AND statistic_name IN ('median', 'mean') 
                      AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'EXTERNAL_IP')
                    LIMIT 1;
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if row and row["value_numeric"] is not None:
                    metrics["latency_ms"] = round(float(row["value_numeric"]), 1)
                else:
                    # Fallback to any external observation
                    cur2 = conn.execute(
                        """
                        SELECT AVG(value_numeric) as val FROM observations 
                        WHERE run_id = ? AND metric_name = 'round_trip_time' AND status = 'SUCCESS'
                          AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'EXTERNAL_IP');
                        """,
                        (run_id,),
                    )
                    r2 = cur2.fetchone()
                    if r2 and r2["val"] is not None:
                        metrics["latency_ms"] = round(float(r2["val"]), 1)

                # 2. Delay variation / jitter
                cur = conn.execute(
                    """
                    SELECT value_numeric FROM summaries 
                    WHERE run_id = ? AND metric_name = 'delay_variation'
                    LIMIT 1;
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if row and row["value_numeric"] is not None:
                    metrics["jitter_ms"] = round(float(row["value_numeric"]), 2)

                # 3. Packet loss percentage
                cur = conn.execute(
                    """
                    SELECT value_numeric FROM summaries 
                    WHERE run_id = ? AND metric_name = 'packet_loss' AND statistic_name = 'loss_percentage'
                    LIMIT 1;
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if row and row["value_numeric"] is not None:
                    metrics["packet_loss_pct"] = round(float(row["value_numeric"]), 1)
                else:
                    # Compute from observations
                    cur2 = conn.execute(
                        """
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) as lost
                        FROM observations
                        WHERE run_id = ? AND metric_name = 'round_trip_time';
                        """,
                        (run_id,),
                    )
                    r2 = cur2.fetchone()
                    if r2 and r2["total"] and r2["total"] > 0:
                        metrics["packet_loss_pct"] = round((r2["lost"] * 100.0) / r2["total"], 1)

                # 4. Downstream throughput
                cur = conn.execute(
                    """
                    SELECT value_numeric FROM summaries 
                    WHERE run_id = ? AND metric_name = 'downstream_throughput'
                    LIMIT 1;
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if row and row["value_numeric"] is not None:
                    metrics["download_mbps"] = round(float(row["value_numeric"]), 2)
                else:
                    cur2 = conn.execute(
                        """
                        SELECT value_numeric FROM observations 
                        WHERE run_id = ? AND metric_name = 'download_throughput' AND status = 'SUCCESS'
                        LIMIT 1;
                        """,
                        (run_id,),
                    )
                    r2 = cur2.fetchone()
                    if r2 and r2["value_numeric"] is not None:
                        metrics["download_mbps"] = round(float(r2["value_numeric"]), 2)

        except Exception as exc:
            logger.error("Failed to extract empirical metrics for run %s: %s", run_id, exc, exc_info=True)

        return metrics

