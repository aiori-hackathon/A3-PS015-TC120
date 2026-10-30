"""Repository for Target and Observation persistence."""

import sqlite3
from typing import List, Optional

from ticnet.models.entities import Target, Observation
from ticnet.models.enums import TargetType, MeasurementStatus
from ticnet.persistence.database import DatabaseManager


class ObservationRepository:
    """Manages CRUD operations for Target definitions and raw Observation telemetry."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_target(self, target: Target) -> Target:
        """Insert or update a network probe target."""
        sql = """
            INSERT INTO targets (
                target_id, target_type, hostname, ip_address, port,
                provider_label, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                target_type = excluded.target_type,
                hostname = excluded.hostname,
                ip_address = excluded.ip_address,
                port = excluded.port,
                provider_label = excluded.provider_label,
                is_active = excluded.is_active;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    target.target_id,
                    target.target_type.value,
                    target.hostname,
                    target.ip_address,
                    target.port,
                    target.provider_label,
                    1 if target.is_active else 0,
                ),
            )
        return target

    def get_target(self, target_id: str) -> Optional[Target]:
        """Retrieve target by its identifier."""
        sql = """
            SELECT target_id, target_type, hostname, ip_address, port,
                   provider_label, is_active
            FROM targets
            WHERE target_id = ?;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (target_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_target(row)

    def list_targets(self, active_only: bool = True) -> List[Target]:
        """List configured targets."""
        if active_only:
            sql = "SELECT * FROM targets WHERE is_active = 1 ORDER BY target_id ASC;"
            params = ()
        else:
            sql = "SELECT * FROM targets ORDER BY target_id ASC;"
            params = ()

        with self.db.connection() as conn:
            cursor = conn.execute(sql, params)
            return [self._row_to_target(row) for row in cursor.fetchall()]

    def create_observation(self, obs: Observation) -> Observation:
        """Persist a single raw measurement observation."""
        sql = """
            INSERT INTO observations (
                observation_id, run_id, stage_id, target_id, sample_index,
                timestamp_utc, duration_ms, metric_name, value_numeric, unit,
                status, error_message, method_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    obs.observation_id,
                    obs.run_id,
                    obs.stage_id,
                    obs.target_id,
                    obs.sample_index,
                    obs.timestamp_utc,
                    obs.duration_ms,
                    obs.metric_name,
                    obs.value_numeric,
                    obs.unit,
                    obs.status.value,
                    obs.error_message,
                    obs.method_metadata,
                ),
            )
        return obs

    def create_observations_batch(self, observations: List[Observation]) -> List[Observation]:
        """Persist multiple raw measurement observations in a single atomic transaction."""
        if not observations:
            return []

        sql = """
            INSERT INTO observations (
                observation_id, run_id, stage_id, target_id, sample_index,
                timestamp_utc, duration_ms, metric_name, value_numeric, unit,
                status, error_message, method_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = [
            (
                obs.observation_id,
                obs.run_id,
                obs.stage_id,
                obs.target_id,
                obs.sample_index,
                obs.timestamp_utc,
                obs.duration_ms,
                obs.metric_name,
                obs.value_numeric,
                obs.unit,
                obs.status.value,
                obs.error_message,
                obs.method_metadata,
            )
            for obs in observations
        ]
        with self.db.transaction() as conn:
            conn.executemany(sql, params)
        return observations

    def get_observation(self, observation_id: str) -> Optional[Observation]:
        """Retrieve an observation by its identifier."""
        sql = "SELECT * FROM observations WHERE observation_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (observation_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_observation(row)

    def get_observations_for_run(self, run_id: str) -> List[Observation]:
        """Retrieve all observations for a test run ordered by sample index."""
        sql = """
            SELECT * FROM observations
            WHERE run_id = ?
            ORDER BY sample_index ASC, timestamp_utc ASC;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (run_id,))
            return [self._row_to_observation(row) for row in cursor.fetchall()]

    def get_observations_for_stage(self, stage_id: str) -> List[Observation]:
        """Retrieve all observations for a specific stage."""
        sql = """
            SELECT * FROM observations
            WHERE stage_id = ?
            ORDER BY sample_index ASC, timestamp_utc ASC;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (stage_id,))
            return [self._row_to_observation(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_target(row: sqlite3.Row) -> Target:
        return Target(
            target_id=row["target_id"],
            target_type=TargetType(row["target_type"]),
            hostname=row["hostname"],
            ip_address=row["ip_address"],
            port=row["port"],
            provider_label=row["provider_label"],
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> Observation:
        return Observation(
            observation_id=row["observation_id"],
            run_id=row["run_id"],
            sample_index=row["sample_index"],
            timestamp_utc=row["timestamp_utc"],
            duration_ms=float(row["duration_ms"]),
            metric_name=row["metric_name"],
            unit=row["unit"],
            status=MeasurementStatus(row["status"]),
            value_numeric=float(row["value_numeric"]) if row["value_numeric"] is not None else None,
            stage_id=row["stage_id"],
            target_id=row["target_id"],
            error_message=row["error_message"],
            method_metadata=row["method_metadata"],
        )
