"""Repository for ReportExport audit persistence."""

import sqlite3
from typing import List, Optional

from ticnet.models.entities import ReportExport
from ticnet.models.enums import ExportFormat, ExportStatus
from ticnet.persistence.database import DatabaseManager


class ExportRepository:
    """Manages persistence and retrieval of ReportExport audit records."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_export(self, export: ReportExport) -> ReportExport:
        """Insert a new report export audit record."""
        sql = """
            INSERT INTO report_exports (
                export_id, run_id, export_format, export_timestamp_utc,
                file_path_redacted, file_size_bytes, export_status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    export.export_id,
                    export.run_id,
                    export.export_format.value,
                    export.export_timestamp_utc,
                    export.file_path_redacted,
                    export.file_size_bytes,
                    export.export_status.value,
                    export.error_message,
                ),
            )
        return export

    def get_export(self, export_id: str) -> Optional[ReportExport]:
        """Retrieve a report export record by its unique identifier."""
        sql = """
            SELECT export_id, run_id, export_format, export_timestamp_utc,
                   file_path_redacted, file_size_bytes, export_status, error_message
            FROM report_exports
            WHERE export_id = ?;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (export_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_export(row)

    def get_exports_for_run(self, run_id: str) -> List[ReportExport]:
        """Retrieve all report export records for a test run, ordered chronologically."""
        sql = """
            SELECT export_id, run_id, export_format, export_timestamp_utc,
                   file_path_redacted, file_size_bytes, export_status, error_message
            FROM report_exports
            WHERE run_id = ?
            ORDER BY export_timestamp_utc ASC;
        """
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (run_id,))
            return [self._row_to_export(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_export(row: sqlite3.Row) -> ReportExport:
        return ReportExport(
            export_id=row["export_id"],
            run_id=row["run_id"],
            export_format=ExportFormat(row["export_format"]),
            export_timestamp_utc=row["export_timestamp_utc"],
            file_path_redacted=row["file_path_redacted"],
            file_size_bytes=row["file_size_bytes"],
            export_status=ExportStatus(row["export_status"]),
            error_message=row["error_message"],
        )
