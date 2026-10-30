"""Repository for TestRun and TestStage persistence."""

import sqlite3
import threading
from datetime import datetime, timezone, date
from ticnet.core.timezone import now_ist, to_ist
from typing import Dict, List, Optional, Set, Union

from ticnet.models.entities import TestRun, TestStage
from ticnet.models.enums import RunStatus, RunMode, StageStatus
from ticnet.persistence.database import DatabaseManager


class RunRepository:
    """Manages CRUD operations for TestRun and TestStage entities."""

    _gen_lock = threading.Lock()
    _allocated_by_db: Dict[str, Set[str]] = {}

    def __init__(self, db: DatabaseManager):
        self.db = db

    def generate_next_run_id(
        self, target_date: Optional[Union[datetime, date, str]] = None
    ) -> str:
        """Generate a globally unique, permanent Run ID conforming to TCN-YYYYMMDD-XXXXXX.

        Generation Requirements:
        - Format: TCN-YYYYMMDD-XXXXXX (e.g. TCN-20261024-000001).
        - Source of Truth: Queried from SQLite test_runs table to persist numbering across restarts.
        - Concurrency Safe: Uses class-level thread lock and in-memory allocated set to guarantee uniqueness.
        - Non-Reusing: Suffix sequentially increments from maximum existing sequence for the target date.
        """
        if target_date is None:
            date_str = now_ist().strftime("%Y%m%d")
        elif isinstance(target_date, datetime):
            date_str = to_ist(target_date).strftime("%Y%m%d")
        elif isinstance(target_date, date):
            date_str = target_date.strftime("%Y%m%d")
        elif isinstance(target_date, str):
            cleaned = "".join(c for c in target_date if c.isdigit())[:8]
            date_str = cleaned if len(cleaned) == 8 else now_ist().strftime("%Y%m%d")
        else:
            date_str = now_ist().strftime("%Y%m%d")

        prefix = f"TCN-{date_str}-"
        db_key = str(getattr(self.db, "db_path", "default"))

        with self._gen_lock:
            if db_key not in self._allocated_by_db:
                self._allocated_by_db[db_key] = set()
            allocated = self._allocated_by_db[db_key]

            max_seq = 0
            with self.db.connection() as conn:
                cursor = conn.execute(
                    "SELECT run_id FROM test_runs WHERE run_id LIKE ? ORDER BY run_id DESC",
                    (f"{prefix}%",),
                )
                for row in cursor.fetchall():
                    rid = row[0]
                    parts = rid.split("-")
                    if len(parts) == 3 and parts[2].isdigit():
                        seq = int(parts[2])
                        if seq > max_seq:
                            max_seq = seq

            candidate_seq = max_seq + 1
            candidate_id = f"{prefix}{candidate_seq:06d}"
            while candidate_id in allocated:
                candidate_seq += 1
                candidate_id = f"{prefix}{candidate_seq:06d}"

            allocated.add(candidate_id)
            return candidate_id

    @classmethod
    def reset_allocated_cache(cls) -> None:
        """Clear allocated ID tracking cache across all databases."""
        with cls._gen_lock:
            cls._allocated_by_db.clear()

    def create_run(self, run: TestRun) -> TestRun:
        """Insert a new test run record into the database."""
        sql = """
            INSERT INTO test_runs (
                run_id, start_time_utc, end_time_utc, status, run_mode,
                app_version, config_version, interface_name, interface_type,
                gateway_ip, overall_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    run.run_id,
                    run.start_time_utc,
                    run.end_time_utc,
                    run.status.value,
                    run.run_mode.value,
                    run.app_version,
                    run.config_version,
                    run.interface_name,
                    run.interface_type,
                    run.gateway_ip,
                    run.overall_error,
                ),
            )
        db_key = str(getattr(self.db, "db_path", "default"))
        with self._gen_lock:
            if db_key not in self._allocated_by_db:
                self._allocated_by_db[db_key] = set()
            self._allocated_by_db[db_key].add(run.run_id)
        return run

    def get_run(self, run_id: str) -> Optional[TestRun]:
        """Retrieve a test run by its unique identifier."""
        sql = """
            SELECT run_id, start_time_utc, end_time_utc, status, run_mode,
                   app_version, config_version, interface_name, interface_type,
                   gateway_ip, overall_error
            FROM test_runs
            WHERE run_id = ?;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_run(row)

    def list_runs(self, limit: int = 100, offset: int = 0) -> List[TestRun]:
        """List historical diagnostic runs in reverse chronological order."""
        sql = """
            SELECT run_id, start_time_utc, end_time_utc, status, run_mode,
                   app_version, config_version, interface_name, interface_type,
                   gateway_ip, overall_error
            FROM test_runs
            ORDER BY start_time_utc DESC
            LIMIT ? OFFSET ?;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (limit, offset))
            return [self._row_to_run(row) for row in cursor.fetchall()]

    def update_run(self, run: TestRun) -> None:
        """Update mutable fields of an existing test run."""
        sql = """
            UPDATE test_runs
            SET end_time_utc = ?, status = ?, overall_error = ?,
                interface_name = ?, interface_type = ?, gateway_ip = ?
            WHERE run_id = ?;
        """
        with self.db.transaction() as conn:
            cursor = conn.execute(
                sql,
                (
                    run.end_time_utc,
                    run.status.value,
                    run.overall_error,
                    run.interface_name,
                    run.interface_type,
                    run.gateway_ip,
                    run.run_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"TestRun with id {run.run_id} does not exist.")

    def create_stage(self, stage: TestStage) -> TestStage:
        """Insert a new test stage record for a test run."""
        sql = """
            INSERT INTO test_stages (
                stage_id, run_id, stage_name, stage_order, start_time_utc,
                end_time_utc, status, skip_reason, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    stage.stage_id,
                    stage.run_id,
                    stage.stage_name,
                    stage.stage_order,
                    stage.start_time_utc,
                    stage.end_time_utc,
                    stage.status.value,
                    stage.skip_reason,
                    stage.error_message,
                ),
            )
        return stage

    def get_stage(self, stage_id: str) -> Optional[TestStage]:
        """Retrieve a test stage by its unique identifier."""
        sql = """
            SELECT stage_id, run_id, stage_name, stage_order, start_time_utc,
                   end_time_utc, status, skip_reason, error_message
            FROM test_stages
            WHERE stage_id = ?;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (stage_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_stage(row)

    def get_stages_for_run(self, run_id: str) -> List[TestStage]:
        """Retrieve all stages for a run, ordered by execution sequence."""
        sql = """
            SELECT stage_id, run_id, stage_name, stage_order, start_time_utc,
                   end_time_utc, status, skip_reason, error_message
            FROM test_stages
            WHERE run_id = ?
            ORDER BY stage_order ASC;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (run_id,))
            return [self._row_to_stage(row) for row in cursor.fetchall()]

    def update_stage(self, stage: TestStage) -> None:
        """Update mutable fields of a test stage."""
        sql = """
            UPDATE test_stages
            SET end_time_utc = ?, status = ?, skip_reason = ?, error_message = ?
            WHERE stage_id = ?;
        """
        with self.db.transaction() as conn:
            cursor = conn.execute(
                sql,
                (
                    stage.end_time_utc,
                    stage.status.value,
                    stage.skip_reason,
                    stage.error_message,
                    stage.stage_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"TestStage with id {stage.stage_id} does not exist.")

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and all cascading stages/observations/findings."""
        sql = "DELETE FROM test_runs WHERE run_id = ?;"
        with self.db.transaction() as conn:
            cursor = conn.execute(sql, (run_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> TestRun:
        return TestRun(
            run_id=row["run_id"],
            start_time_utc=row["start_time_utc"],
            end_time_utc=row["end_time_utc"],
            status=RunStatus(row["status"]),
            run_mode=RunMode(row["run_mode"]),
            app_version=row["app_version"],
            config_version=row["config_version"],
            interface_name=row["interface_name"],
            interface_type=row["interface_type"],
            gateway_ip=row["gateway_ip"],
            overall_error=row["overall_error"],
        )

    @staticmethod
    def _row_to_stage(row: sqlite3.Row) -> TestStage:
        return TestStage(
            stage_id=row["stage_id"],
            run_id=row["run_id"],
            stage_name=row["stage_name"],
            stage_order=row["stage_order"],
            start_time_utc=row["start_time_utc"],
            end_time_utc=row["end_time_utc"],
            status=StageStatus(row["status"]),
            skip_reason=row["skip_reason"],
            error_message=row["error_message"],
        )
