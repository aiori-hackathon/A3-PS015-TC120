"""Reporting service adapter for TicNet desktop presentation layer.

Coordinates report serialization, atomic filesystem export, and audit logging
using the validated Phase 9 ReportGenerator implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

from ticnet.models.entities import ReportExport
from ticnet.models.enums import ExportFormat, ExportStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.export_repo import ExportRepository
from ticnet.reporting.generator import ReportExportError, ReportGenerator
from ticnet.reporting.privacy import DEFAULT_PRIVACY_FILTER, PrivacyFilter
from ticnet.reporting.serializers import format_report_filename
from ticnet.utils.logging import get_logger

logger = get_logger("desktop.service.reporting")


class ReportingService:
    """Manages report exports and preview generation adhering to Phase 9 safeguards."""

    def __init__(
        self,
        db: DatabaseManager,
        generator: Optional[ReportGenerator] = None,
    ) -> None:
        """Initialize reporting service with database manager.

        Args:
            db: Configured DatabaseManager instance.
            generator: Optional ReportGenerator override.
        """
        self.db = db
        self.generator = generator or ReportGenerator(db=self.db)
        self.export_repo = ExportRepository(db=self.db)

    def export_report(
        self,
        run_id: str,
        export_format: Union[ExportFormat, str],
        output_path: Union[str, Path],
        enable_privacy: bool = True,
        overwrite: bool = True,
    ) -> ReportExport:
        """Export diagnostic report to disk using Phase 9 atomic write and audit logging.

        Args:
            run_id: Unique test run identifier.
            export_format: JSON, CSV, or MARKDOWN format.
            output_path: Target filesystem path for the export.
            enable_privacy: If True, masks public WAN IPs and MAC addresses.
            overwrite: If True, safely backs up and overwrites destination.

        Returns:
            ReportExport entity recording the export operation.

        Raises:
            ReportExportError: If serialization, file write, or audit persistence fails.
        """
        privacy_filter = (
            DEFAULT_PRIVACY_FILTER
            if enable_privacy
            else PrivacyFilter(mask_mac_addresses=False, mask_public_ips=False, mask_credentials=False)
        )

        logger.info(
            "Executing report export: run_id=%s, format=%s, output_path=%s, privacy=%s",
            run_id,
            export_format,
            output_path,
            enable_privacy,
        )

        return self.generator.export_report(
            run_id=run_id,
            export_format=export_format,
            output_path=output_path,
            privacy_filter=privacy_filter,
            overwrite=overwrite,
        )

    def get_export_preview(
        self,
        run_id: str,
        export_format: Union[ExportFormat, str],
        enable_privacy: bool = True,
    ) -> str:
        """Generate an in-memory preview string of the serialized report.

        Args:
            run_id: Unique test run identifier.
            export_format: JSON, CSV, or MARKDOWN format.
            enable_privacy: If True, masks public WAN IPs and MAC addresses.

        Returns:
            Formatted serialized report content as a string.
        """
        privacy_filter = (
            DEFAULT_PRIVACY_FILTER
            if enable_privacy
            else PrivacyFilter(mask_mac_addresses=False, mask_public_ips=False, mask_credentials=False)
        )
        return self.generator.export_to_string(
            run_id=run_id,
            export_format=export_format,
            privacy_filter=privacy_filter,
        )

    def list_exports_for_run(self, run_id: str) -> List[ReportExport]:
        """Query audit log for previous exports of this diagnostic run.

        Args:
            run_id: Unique test run identifier.

        Returns:
            List of ReportExport audit records.
        """
        try:
            return self.export_repo.get_exports_for_run(run_id)
        except Exception as exc:
            logger.error("Failed to query export audit for run %s: %s", run_id, exc, exc_info=True)
            return []

    @staticmethod
    def get_extension_for_format(export_format: Union[ExportFormat, str]) -> str:
        """Return the standard file extension for a given export format."""
        fmt_str = str(export_format.value if isinstance(export_format, ExportFormat) else export_format).upper()
        if "JSON" in fmt_str:
            return ".json"
        elif "CSV" in fmt_str:
            return ".csv"
        return ".md"

    @staticmethod
    def get_recommended_filename(
        run_id: str,
        export_format: Union[ExportFormat, str],
        timestamp_utc: Optional[str] = None,
    ) -> str:
        """Return the standardized report filename conforming to TicNet conventions."""
        return format_report_filename(
            run_id=run_id,
            export_format=export_format,
            timestamp_utc=timestamp_utc,
        )
