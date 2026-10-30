"""Repository for NotificationPreferences and NetworkIncident persistence in SQLite."""

from __future__ import annotations

import math
import sqlite3
from typing import Any, List, Optional

from ticnet.models.notifications import (
    IncidentSeverity,
    IncidentStatus,
    NetworkIncident,
    NotificationPreferences,
    now_utc_iso,
)
from ticnet.persistence.database import DatabaseManager


class IncidentRepository:
    """Manages persistence and retrieval of notification preferences and incidents."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    # -------------------------------------------------------------------------
    # Notification Preferences
    # -------------------------------------------------------------------------

    def get_preferences(self, preference_id: str = "default") -> NotificationPreferences:
        """Fetch notification preferences, initializing with defaults if none exist."""
        sql = "SELECT * FROM notification_preferences WHERE preference_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (preference_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_preferences(row)

        # Initialize default preferences atomically
        defaults = NotificationPreferences(preference_id=preference_id)
        return self.save_preferences(defaults)

    def save_preferences(self, prefs: NotificationPreferences) -> NotificationPreferences:
        """Atomically insert or update notification preferences."""
        prefs.updated_at_utc = now_utc_iso()
        sql = """
            INSERT INTO notification_preferences (
                preference_id, desktop_enabled, sound_enabled,
                notify_suspected, notify_confirmed, notify_recovery,
                cooldown_minutes, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(preference_id) DO UPDATE SET
                desktop_enabled = excluded.desktop_enabled,
                sound_enabled = excluded.sound_enabled,
                notify_suspected = excluded.notify_suspected,
                notify_confirmed = excluded.notify_confirmed,
                notify_recovery = excluded.notify_recovery,
                cooldown_minutes = excluded.cooldown_minutes,
                updated_at_utc = excluded.updated_at_utc;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    prefs.preference_id,
                    1 if prefs.desktop_enabled else 0,
                    1 if prefs.sound_enabled else 0,
                    1 if prefs.notify_suspected else 0,
                    1 if prefs.notify_confirmed else 0,
                    1 if prefs.notify_recovery else 0,
                    prefs.cooldown_minutes,
                    prefs.updated_at_utc,
                ),
            )
        return prefs

    # -------------------------------------------------------------------------
    # Network Incidents
    # -------------------------------------------------------------------------

    def get_incident(self, incident_id: str) -> Optional[NetworkIncident]:
        """Fetch a specific incident by ID."""
        sql = "SELECT * FROM network_incidents WHERE incident_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (incident_id,))
            row = cursor.fetchone()
            return self._row_to_incident(row) if row else None

    def get_active_incident_for_metric(self, metric_name: str) -> Optional[NetworkIncident]:
        """Find an ongoing (SUSPECTED or CONFIRMED) incident for a specific metric."""
        sql = """
            SELECT * FROM network_incidents 
            WHERE metric_name = ? AND status IN ('SUSPECTED', 'CONFIRMED')
            ORDER BY started_at_utc DESC
            LIMIT 1;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (metric_name,))
            row = cursor.fetchone()
            return self._row_to_incident(row) if row else None

    def create_incident(self, incident: NetworkIncident) -> NetworkIncident:
        """Persist a new network incident."""
        sql = """
            INSERT INTO network_incidents (
                incident_id, monitoring_run_id, metric_name, severity, status,
                baseline_value, observed_value, deviation_pct, headline, description,
                started_at_utc, last_seen_at_utc, resolved_at_utc, last_notified_at_utc,
                notification_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    incident.incident_id,
                    incident.monitoring_run_id,
                    incident.metric_name,
                    incident.severity,
                    incident.status,
                    incident.baseline_value,
                    incident.observed_value,
                    incident.deviation_pct,
                    incident.headline,
                    incident.description,
                    incident.started_at_utc,
                    incident.last_seen_at_utc,
                    incident.resolved_at_utc,
                    incident.last_notified_at_utc,
                    incident.notification_count,
                ),
            )
        return incident

    def update_incident(self, incident: NetworkIncident) -> None:
        """Update an ongoing incident with new observed values or status."""
        sql = """
            UPDATE network_incidents SET
                monitoring_run_id = ?,
                severity = ?,
                status = ?,
                baseline_value = ?,
                observed_value = ?,
                deviation_pct = ?,
                headline = ?,
                description = ?,
                last_seen_at_utc = ?,
                resolved_at_utc = ?,
                last_notified_at_utc = ?,
                notification_count = ?
            WHERE incident_id = ?;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    incident.monitoring_run_id,
                    incident.severity,
                    incident.status,
                    incident.baseline_value,
                    incident.observed_value,
                    incident.deviation_pct,
                    incident.headline,
                    incident.description,
                    incident.last_seen_at_utc,
                    incident.resolved_at_utc,
                    incident.last_notified_at_utc,
                    incident.notification_count,
                    incident.incident_id,
                ),
            )

    def resolve_incident(self, incident_id: str, resolved_at_utc: Optional[str] = None) -> None:
        """Mark an incident as RESOLVED."""
        if not resolved_at_utc:
            resolved_at_utc = now_utc_iso()
        sql = """
            UPDATE network_incidents SET
                status = 'RESOLVED',
                resolved_at_utc = ?
            WHERE incident_id = ?;
        """
        with self.db.transaction() as conn:
            conn.execute(sql, (resolved_at_utc, incident_id))

    def list_incidents(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
    ) -> List[NetworkIncident]:
        """Fetch list of incidents in reverse chronological order."""
        if status_filter:
            sql = """
                SELECT * FROM network_incidents 
                WHERE status = ? 
                ORDER BY started_at_utc DESC 
                LIMIT ? OFFSET ?;
            """
            params: tuple[Any, ...] = (status_filter, limit, offset)
        else:
            sql = """
                SELECT * FROM network_incidents 
                ORDER BY started_at_utc DESC 
                LIMIT ? OFFSET ?;
            """
            params = (limit, offset)

        with self.db.connection() as conn:
            cursor = conn.execute(sql, params)
            return [self._row_to_incident(row) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Helper Deserializers
    # -------------------------------------------------------------------------

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
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
    def _row_to_preferences(row: sqlite3.Row) -> NotificationPreferences:
        return NotificationPreferences(
            preference_id=row["preference_id"],
            desktop_enabled=bool(row["desktop_enabled"]),
            sound_enabled=bool(row["sound_enabled"]),
            notify_suspected=bool(row["notify_suspected"]),
            notify_confirmed=bool(row["notify_confirmed"]),
            notify_recovery=bool(row["notify_recovery"]),
            cooldown_minutes=IncidentRepository._safe_int(row["cooldown_minutes"], default=30),
            updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> NetworkIncident:
        return NetworkIncident(
            incident_id=row["incident_id"],
            monitoring_run_id=row["monitoring_run_id"],
            metric_name=row["metric_name"],
            severity=row["severity"],
            status=row["status"],
            baseline_value=IncidentRepository._safe_float(row["baseline_value"]),
            observed_value=IncidentRepository._safe_float(row["observed_value"]),
            deviation_pct=IncidentRepository._safe_float(row["deviation_pct"]),
            headline=row["headline"] or "",
            description=row["description"] or "",
            started_at_utc=row["started_at_utc"],
            last_seen_at_utc=row["last_seen_at_utc"],
            resolved_at_utc=row["resolved_at_utc"],
            last_notified_at_utc=row["last_notified_at_utc"],
            notification_count=IncidentRepository._safe_int(row["notification_count"]),
        )

