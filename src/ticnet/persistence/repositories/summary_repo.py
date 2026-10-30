"""Repository for statistical Summary persistence."""

import sqlite3
from typing import List, Optional

from ticnet.models.entities import Summary
from ticnet.persistence.database import DatabaseManager


class SummaryRepository:
    """Manages persistence and retrieval of derived statistical Summaries."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_summary(self, summary: Summary) -> Summary:
        """Persist a single statistical summary record."""
        sql = """
            INSERT INTO summaries (
                summary_id, run_id, target_id, metric_name, statistic_name,
                value_numeric, unit, sample_count_total, sample_count_valid,
                calculation_method, calculation_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    summary.summary_id,
                    summary.run_id,
                    summary.target_id,
                    summary.metric_name,
                    summary.statistic_name,
                    summary.value_numeric,
                    summary.unit,
                    summary.sample_count_total,
                    summary.sample_count_valid,
                    summary.calculation_method,
                    summary.calculation_version,
                ),
            )
        return summary

    def create_summaries_batch(self, summaries: List[Summary]) -> List[Summary]:
        """Persist multiple statistical summaries in a single atomic transaction."""
        if not summaries:
            return []

        sql = """
            INSERT INTO summaries (
                summary_id, run_id, target_id, metric_name, statistic_name,
                value_numeric, unit, sample_count_total, sample_count_valid,
                calculation_method, calculation_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = [
            (
                s.summary_id,
                s.run_id,
                s.target_id,
                s.metric_name,
                s.statistic_name,
                s.value_numeric,
                s.unit,
                s.sample_count_total,
                s.sample_count_valid,
                s.calculation_method,
                s.calculation_version,
            )
            for s in summaries
        ]
        with self.db.transaction() as conn:
            conn.executemany(sql, params)
        return summaries

    def get_summary(self, summary_id: str) -> Optional[Summary]:
        """Retrieve a summary by its identifier."""
        sql = "SELECT * FROM summaries WHERE summary_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (summary_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_summary(row)

    def get_summaries_for_run(self, run_id: str) -> List[Summary]:
        """Retrieve all statistical summaries computed for a test run."""
        sql = "SELECT * FROM summaries WHERE run_id = ? ORDER BY metric_name, statistic_name;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (run_id,))
            return [self._row_to_summary(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> Summary:
        return Summary(
            summary_id=row["summary_id"],
            run_id=row["run_id"],
            target_id=row["target_id"],
            metric_name=row["metric_name"],
            statistic_name=row["statistic_name"],
            unit=row["unit"],
            sample_count_total=int(row["sample_count_total"]),
            sample_count_valid=int(row["sample_count_valid"]),
            calculation_method=row["calculation_method"],
            calculation_version=row["calculation_version"],
            value_numeric=float(row["value_numeric"]) if row["value_numeric"] is not None else None,
        )
