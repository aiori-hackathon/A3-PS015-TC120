"""Report generator orchestrating data retrieval, serialization, and atomic export.

Implements:
- FR-12 / SRS-F-012: Technician Reporting & Serialized Export
- FR-18 / SRS-F-018: Privacy Safeguards & Data Minimization
- TC-INT-05: Consistency checking between database records and exported artifacts
- AC-PERS-03: 100% data equality for JSON and CSV exports
- AC-PERS-04: Markdown technician summary formatted for support tickets
- AC-PERS-05: Configurable privacy filtering on export
- Safe, atomic file writing with destination validation and audit logging
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Union
import uuid

from ticnet.models.entities import (
    TestRun,
    TestStage,
    Target,
    Observation,
    Summary,
    Finding,
    FindingEvidence,
    ReportExport,
    new_id,
    now_utc_iso,
)
from ticnet.models.enums import ExportFormat, ExportStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.export_repo import ExportRepository
from ticnet.reporting.privacy import (
    PrivacyFilter,
    DEFAULT_PRIVACY_FILTER,
)
from ticnet.reporting.serializers import (
    DiagnosticReportData,
    serialize_to_json,
    serialize_to_csv,
    serialize_report_to_csv,
    serialize_to_markdown,
    format_report_filename,
)

logger = logging.getLogger(__name__)


class ReportExportError(Exception):
    """Raised when report generation or file export fails."""
    pass


def redact_filesystem_path(path: Union[str, Path]) -> str:
    """Redact user profile and home directory details from exported path for audit log."""
    path_str = str(path)
    try:
        user_home = str(Path.home())
        if user_home and user_home in path_str:
            return path_str.replace(user_home, "~")
    except Exception:
        pass
    return path_str


class ReportGenerator:
    """Coordinates diagnostic report generation and atomic file export."""

    def __init__(
        self,
        db: DatabaseManager,
        default_export_dir: Optional[Union[str, Path]] = None,
    ):
        self.db = db
        self.default_export_dir = (
            Path(default_export_dir).resolve()
            if default_export_dir
            else Path("reports").resolve()
        )
        self.run_repo = RunRepository(db)
        self.obs_repo = ObservationRepository(db)
        self.summary_repo = SummaryRepository(db)
        self.finding_repo = FindingRepository(db)
        self.export_repo = ExportRepository(db)

    def build_report_data(self, run_id: str) -> DiagnosticReportData:
        """Fetch all entities for a diagnostic run and assemble DiagnosticReportData."""
        run = self.run_repo.get_run(run_id)
        if not run:
            raise ReportExportError(f"TestRun '{run_id}' not found in database.")

        stages = self.run_repo.get_stages_for_run(run_id)
        observations = self.obs_repo.get_observations_for_run(run_id)
        summaries = self.summary_repo.get_summaries_for_run(run_id)
        findings = self.finding_repo.get_findings_for_run(run_id)

        evidence: List[FindingEvidence] = []
        for finding in findings:
            ev_list = self.finding_repo.get_evidence_for_finding(finding.finding_id)
            evidence.extend(ev_list)

        # Collect targets from database
        all_targets = self.obs_repo.list_targets(active_only=False)
        targets_dict: Dict[str, Target] = {t.target_id: t for t in all_targets}

        return DiagnosticReportData(
            run=run,
            stages=stages,
            observations=observations,
            summaries=summaries,
            findings=findings,
            evidence=evidence,
            targets=targets_dict,
            schema_version="1.0.0",
            generated_at_utc=now_utc_iso(),
        )

    def export_to_string(
        self,
        run_id: str,
        export_format: Union[ExportFormat, str],
        privacy_filter: Optional[PrivacyFilter] = DEFAULT_PRIVACY_FILTER,
    ) -> str:
        """Generate serialized report as an in-memory string without writing to disk."""
        if isinstance(export_format, str):
            try:
                export_format = ExportFormat(export_format.upper())
            except ValueError:
                raise ReportExportError(f"Unsupported export format: {export_format}")

        data = self.build_report_data(run_id)

        if export_format == ExportFormat.JSON:
            return serialize_to_json(data, privacy_filter=privacy_filter)
        elif export_format == ExportFormat.CSV:
            return serialize_to_csv(
                data,
                privacy_filter=privacy_filter,
            )
        elif export_format == ExportFormat.MARKDOWN:
            return serialize_to_markdown(data, privacy_filter=privacy_filter)
        else:
            raise ReportExportError(f"Unsupported export format: {export_format}")

    def export_report(
        self,
        run_id: str,
        export_format: Union[ExportFormat, str],
        output_path: Optional[Union[str, Path]] = None,
        privacy_filter: Optional[PrivacyFilter] = DEFAULT_PRIVACY_FILTER,
        overwrite: bool = True,
    ) -> ReportExport:
        """Serialize diagnostic report, write atomically to disk, and record audit log."""
        if isinstance(export_format, str):
            try:
                export_format = ExportFormat(export_format.upper())
            except ValueError:
                raise ReportExportError(f"Unsupported export format: {export_format}")

        export_id = new_id()
        timestamp_utc = now_utc_iso()

        # Determine target file extension
        ext_map = {
            ExportFormat.JSON: ".json",
            ExportFormat.CSV: ".csv",
            ExportFormat.MARKDOWN: ".md",
        }
        ext = ext_map.get(export_format, ".txt")

        # Determine destination path
        if output_path is None:
            self.default_export_dir.mkdir(parents=True, exist_ok=True)
            target_path = self.default_export_dir / f"ticnet_report_{run_id}_{export_format.value.lower()}{ext}"
        else:
            target_path = Path(output_path).resolve()

        redacted_path = redact_filesystem_path(target_path)

        # Validate target path
        if target_path.is_dir():
            err_msg = f"Target path '{target_path}' is an existing directory; expected file."
            self._record_failed_export(export_id, run_id, export_format, timestamp_utc, redacted_path, err_msg)
            raise ReportExportError(err_msg)

        if not overwrite and target_path.exists():
            err_msg = f"Target file '{target_path}' already exists and overwrite=False."
            self._record_failed_export(export_id, run_id, export_format, timestamp_utc, redacted_path, err_msg)
            raise ReportExportError(err_msg)

        temp_path: Optional[Path] = None
        backup_path: Optional[Path] = None
        target_replaced = False
        target_existed_prior = target_path.exists()
        audit_persisted = False
        audit_record: Optional[ReportExport] = None
        try:
            # 1. Fetch data & serialize
            content = self.export_to_string(run_id, export_format, privacy_filter=privacy_filter)

            # 2. Ensure parent directory exists
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 3. Write atomically to temporary file in same directory
            temp_filename = f".tmp_{uuid.uuid4().hex}_{target_path.name}"
            temp_path = target_path.parent / temp_filename

            with open(temp_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass

            # 4. If destination already exists and overwrite is permitted,
            # create a temporary backup in the same directory so pre-existing valid
            # contents can be restored if subsequent audit persistence fails.
            if target_existed_prior:
                backup_filename = f".bak_{uuid.uuid4().hex}_{target_path.name}"
                backup_path = target_path.parent / backup_filename
                os.replace(target_path, backup_path)

            # 5. Atomic rename / replace
            os.replace(temp_path, target_path)
            target_replaced = True

            # 6. Measure written size
            file_size_bytes = target_path.stat().st_size

            # 7. Record successful export audit log
            audit_record = ReportExport(
                export_id=export_id,
                run_id=run_id,
                export_format=export_format,
                export_timestamp_utc=timestamp_utc,
                file_path_redacted=redacted_path,
                file_size_bytes=file_size_bytes,
                export_status=ExportStatus.SUCCESS,
                error_message=None,
            )
            self.export_repo.create_export(audit_record)
            audit_persisted = True

            # 8. Success: delete temporary backup file if one was created
            if backup_path and backup_path.exists():
                try:
                    backup_path.unlink()
                except Exception as cleanup_exc:
                    logger.warning("Could not delete temporary backup file %s: %s", backup_path, cleanup_exc)

            logger.info("Successfully exported %s report for run %s to %s", export_format.value, run_id, redacted_path)
            return audit_record

        except Exception as exc:
            # If the audit entry was already persisted as SUCCESS, do not trigger destructive rollback
            if audit_persisted and audit_record is not None:
                logger.warning("Post-audit step encountered an error for run %s: %s", run_id, exc)
                return audit_record

            # Cleanup temp file if still present
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

            # Safe rollback handling:
            if target_replaced:
                # Remove the new, un-audited target_path file
                if target_path.exists():
                    try:
                        target_path.unlink()
                    except OSError:
                        pass
                # Restore pre-existing file from backup if one was created
                if backup_path and backup_path.exists():
                    try:
                        os.replace(backup_path, target_path)
                    except OSError:
                        pass
            elif backup_path and backup_path.exists():
                # If target was moved to backup but replacement never completed, restore it
                try:
                    os.replace(backup_path, target_path)
                except OSError:
                    pass

            err_msg = f"Failed to export {export_format.value} report: {str(exc)}"
            logger.error("Export failure for run %s: %s", run_id, err_msg, exc_info=True)
            self._record_failed_export(export_id, run_id, export_format, timestamp_utc, redacted_path, err_msg)
            raise ReportExportError(err_msg) from exc

    def _record_failed_export(
        self,
        export_id: str,
        run_id: str,
        export_format: ExportFormat,
        timestamp_utc: str,
        redacted_path: str,
        error_message: str,
    ) -> None:
        """Record an export failure in the report_exports audit table."""
        try:
            audit_record = ReportExport(
                export_id=export_id,
                run_id=run_id,
                export_format=export_format,
                export_timestamp_utc=timestamp_utc,
                file_path_redacted=redacted_path,
                file_size_bytes=None,
                export_status=ExportStatus.FAILED,
                error_message=error_message,
            )
            self.export_repo.create_export(audit_record)
        except Exception as db_exc:
            logger.warning("Could not persist failed export audit record: %s", db_exc)
