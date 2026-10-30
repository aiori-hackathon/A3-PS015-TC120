"""Repository for MonitoringSchedule and MonitoringRun persistence in SQLite."""

from __future__ import annotations

import math
import sqlite3
from typing import Any, List, Optional

from ticnet.models.monitoring import MonitoringRun, MonitoringSchedule
from ticnet.persistence.database import DatabaseManager


class MonitoringRepository:
    """Manages persistence and retrieval of monitoring schedules and run records."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_schedule(self, schedule_id: str = "default") -> Optional[MonitoringSchedule]:
        """Retrieve monitoring schedule by its identifier."""
        sql = "SELECT * FROM monitoring_schedule WHERE schedule_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (schedule_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_schedule(row)

    def save_schedule(self, schedule: MonitoringSchedule) -> MonitoringSchedule:
        """Create or update a monitoring schedule record atomically."""
        sql = """
            INSERT INTO monitoring_schedule (
                schedule_id, enabled, interval_type, daily_time_utc,
                status, next_run_due_utc, last_run_at_utc, last_run_id, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schedule_id) DO UPDATE SET
                enabled = excluded.enabled,
                interval_type = excluded.interval_type,
                daily_time_utc = excluded.daily_time_utc,
                status = excluded.status,
                next_run_due_utc = excluded.next_run_due_utc,
                last_run_at_utc = excluded.last_run_at_utc,
                last_run_id = excluded.last_run_id,
                updated_at_utc = excluded.updated_at_utc;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    schedule.schedule_id,
                    1 if schedule.enabled else 0,
                    schedule.interval_type,
                    schedule.daily_time_utc,
                    schedule.status,
                    schedule.next_run_due_utc,
                    schedule.last_run_at_utc,
                    schedule.last_run_id,
                    schedule.updated_at_utc,
                ),
            )
        return schedule

    def create_monitoring_run(self, run: MonitoringRun) -> MonitoringRun:
        """Persist a new monitoring run record."""
        sql = """
            INSERT INTO monitoring_runs (
                monitoring_run_id, run_id, schedule_id, trigger_type,
                scheduled_for_utc, started_at_utc, completed_at_utc,
                status, skip_reason, error_message,
                download_mbps, upload_mbps, latency_ms, jitter_ms, packet_loss_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    run.monitoring_run_id,
                    run.run_id,
                    run.schedule_id,
                    run.trigger_type,
                    run.scheduled_for_utc,
                    run.started_at_utc,
                    run.completed_at_utc,
                    run.status,
                    run.skip_reason,
                    run.error_message,
                    run.download_mbps,
                    run.upload_mbps,
                    run.latency_ms,
                    run.jitter_ms,
                    run.packet_loss_pct,
                ),
            )
        return run

    def update_monitoring_run(self, run: MonitoringRun) -> None:
        """Update an existing monitoring run with execution results."""
        sql = """
            UPDATE monitoring_runs SET
                run_id = ?,
                started_at_utc = ?,
                completed_at_utc = ?,
                status = ?,
                skip_reason = ?,
                error_message = ?,
                download_mbps = ?,
                upload_mbps = ?,
                latency_ms = ?,
                jitter_ms = ?,
                packet_loss_pct = ?
            WHERE monitoring_run_id = ?;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    run.run_id,
                    run.started_at_utc,
                    run.completed_at_utc,
                    run.status,
                    run.skip_reason,
                    run.error_message,
                    run.download_mbps,
                    run.upload_mbps,
                    run.latency_ms,
                    run.jitter_ms,
                    run.packet_loss_pct,
                    run.monitoring_run_id,
                ),
            )

    def get_monitoring_run(self, monitoring_run_id: str) -> Optional[MonitoringRun]:
        """Fetch a specific monitoring run by its primary key."""
        sql = "SELECT * FROM monitoring_runs WHERE monitoring_run_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (monitoring_run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_run(row)

    def list_monitoring_runs(self, limit: int = 50, offset: int = 0) -> List[MonitoringRun]:
        """Retrieve historical monitoring runs in reverse chronological order."""
        sql = "SELECT * FROM monitoring_runs ORDER BY scheduled_for_utc DESC LIMIT ? OFFSET ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (limit, offset))
            return [self._row_to_run(row) for row in cursor.fetchall()]

    def get_latest_monitoring_run(self) -> Optional[MonitoringRun]:
        """Retrieve the most recent monitoring run (if any)."""
        sql = "SELECT * FROM monitoring_runs ORDER BY scheduled_for_utc DESC LIMIT 1;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql)
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_run(row)

    @staticmethod
    def _row_to_schedule(row: sqlite3.Row) -> MonitoringSchedule:
        return MonitoringSchedule(
            schedule_id=row["schedule_id"],
            enabled=bool(row["enabled"]),
            interval_type=row["interval_type"],
            daily_time_utc=row["daily_time_utc"],
            status=row["status"],
            next_run_due_utc=row["next_run_due_utc"],
            last_run_at_utc=row["last_run_at_utc"],
            last_run_id=row["last_run_id"],
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        """Convert a database value to float safely; returns None for null, NaN, or malformed data."""
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) else f
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> MonitoringRun:
        return MonitoringRun(
            monitoring_run_id=row["monitoring_run_id"],
            schedule_id=row["schedule_id"],
            trigger_type=row["trigger_type"],
            scheduled_for_utc=row["scheduled_for_utc"],
            started_at_utc=row["started_at_utc"],
            completed_at_utc=row["completed_at_utc"],
            status=row["status"],
            run_id=row["run_id"],
            skip_reason=row["skip_reason"],
            error_message=row["error_message"],
            download_mbps=MonitoringRepository._safe_float(row["download_mbps"]),
            upload_mbps=MonitoringRepository._safe_float(row["upload_mbps"]),
            latency_ms=MonitoringRepository._safe_float(row["latency_ms"]),
            jitter_ms=MonitoringRepository._safe_float(row["jitter_ms"]),
            packet_loss_pct=MonitoringRepository._safe_float(row["packet_loss_pct"]),
        )

