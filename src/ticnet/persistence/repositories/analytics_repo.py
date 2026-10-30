"""Repository for historical analytics queries and SlowdownEvent persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from ticnet.core.timezone import now_ist, now_ist_iso, to_ist, to_ist_iso
import math
import sqlite3
from typing import Any, List, Optional

from ticnet.models.monitoring import MonitoringRun
from ticnet.monitoring.analytics import (
    DetectionStatus,
    SlowdownEvent,
    TimeWindow,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.monitoring_repo import MonitoringRepository


class AnalyticsRepository:
    """Provides time-window historical queries and slowdown audit logging."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_monitoring_runs_in_window(
        self,
        window: TimeWindow,
        now_utc: Optional[datetime] = None,
    ) -> List[MonitoringRun]:
        """Fetch historical monitoring runs within a specified window in ascending chronological order."""
        now_ist_dt = to_ist(now_utc) if now_utc is not None else now_ist()
        cutoff_ist = now_ist_dt - window.timedelta
        cutoff_iso = cutoff_ist.isoformat()

        sql = """
            SELECT * FROM monitoring_runs 
            WHERE scheduled_for_utc >= ? 
            ORDER BY scheduled_for_utc ASC;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (cutoff_iso,))
            return [MonitoringRepository._row_to_run(row) for row in cursor.fetchall()]

    def save_slowdown_event(self, event: SlowdownEvent) -> SlowdownEvent:
        """Persist a detected slowdown, suspected spike, or recovery event.
        
        If an event already exists for (monitoring_run_id, metric_name, time_window),
        updates the existing event to prevent duplicate records.
        """
        if event.monitoring_run_id:
            check_sql = """
                SELECT event_id FROM slowdown_events 
                WHERE monitoring_run_id = ? AND metric_name = ? AND time_window = ?
                LIMIT 1;
            """
            with self.db.connection() as conn:
                cursor = conn.execute(
                    check_sql, 
                    (event.monitoring_run_id, event.metric_name, event.time_window),
                )
                existing = cursor.fetchone()
                if existing:
                    update_sql = """
                        UPDATE slowdown_events SET
                            detection_status = ?,
                            baseline_value = ?,
                            observed_value = ?,
                            deviation_pct = ?,
                            threshold_applied = ?,
                            consecutive_breaches = ?,
                            explanation = ?,
                            detected_at_utc = ?
                        WHERE event_id = ?;
                    """
                    with self.db.transaction() as tx_conn:
                        tx_conn.execute(
                            update_sql,
                            (
                                event.detection_status,
                                event.baseline_value,
                                event.observed_value,
                                event.deviation_pct,
                                event.threshold_applied,
                                event.consecutive_breaches,
                                event.explanation,
                                event.detected_at_utc,
                                existing["event_id"],
                            ),
                        )
                    event.event_id = existing["event_id"]
                    return event

        sql = """
            INSERT INTO slowdown_events (
                event_id, monitoring_run_id, metric_name, detection_status,
                baseline_value, observed_value, deviation_pct, threshold_applied,
                time_window, consecutive_breaches, explanation, detected_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    event.event_id,
                    event.monitoring_run_id,
                    event.metric_name,
                    event.detection_status,
                    event.baseline_value,
                    event.observed_value,
                    event.deviation_pct,
                    event.threshold_applied,
                    event.time_window,
                    event.consecutive_breaches,
                    event.explanation,
                    event.detected_at_utc,
                ),
            )
        return event

    def get_recent_slowdown_events(self, limit: int = 20) -> List[SlowdownEvent]:
        """Retrieve recent slowdown detection events in descending chronological order."""
        sql = """
            SELECT * FROM slowdown_events 
            ORDER BY detected_at_utc DESC 
            LIMIT ?;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (limit,))
            return [self._row_to_event(row) for row in cursor.fetchall()]

    def get_latest_slowdown_status(self, metric_name: str = "overall") -> Optional[DetectionStatus]:
        """Retrieve the most recent detection status for a metric."""
        sql = """
            SELECT detection_status FROM slowdown_events 
            WHERE metric_name = ? 
            ORDER BY detected_at_utc DESC 
            LIMIT 1;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (metric_name,))
            row = cursor.fetchone()
            if not row:
                return None
            try:
                return DetectionStatus(row["detection_status"])
            except Exception:
                return None

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
    def _safe_int(val: Any, default: int = 0) -> int:
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> SlowdownEvent:
        return SlowdownEvent(
            event_id=row["event_id"],
            monitoring_run_id=row["monitoring_run_id"],
            metric_name=row["metric_name"],
            detection_status=row["detection_status"],
            baseline_value=AnalyticsRepository._safe_float(row["baseline_value"]),
            observed_value=AnalyticsRepository._safe_float(row["observed_value"]),
            deviation_pct=AnalyticsRepository._safe_float(row["deviation_pct"]),
            threshold_applied=row["threshold_applied"] or "",
            time_window=row["time_window"] or "24H",
            consecutive_breaches=AnalyticsRepository._safe_int(row["consecutive_breaches"]),
            explanation=row["explanation"] or "",
            detected_at_utc=row["detected_at_utc"],
        )

