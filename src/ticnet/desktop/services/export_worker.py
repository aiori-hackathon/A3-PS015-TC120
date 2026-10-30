"""Asynchronous background report export worker for TicNet desktop presentation layer.

Decouples potentially slow filesystem and network-share I/O from the Qt GUI thread
while strictly preserving all Phase 9 ReportGenerator safeguards (atomic writes,
.bak rollback, formula-injection neutralization, privacy masking, and audit logging).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from PySide6.QtCore import QObject, QRunnable, Signal

from ticnet.models.entities import ReportExport
from ticnet.models.enums import ExportFormat
from ticnet.utils.logging import get_logger

logger = get_logger("desktop.service.export_worker")


class ExportWorkerSignals(QObject):
    """Thread-safe Qt signals communicating export progress and outcomes to the GUI."""

    sig_started = Signal(str)                          # run_id
    sig_completed = Signal(object)                     # ReportExport entity
    sig_failed = Signal(str, str)                      # run_id, error_message


class ExportWorker(QRunnable):
    """QRunnable task executing ReportGenerator.export_report off the Qt GUI thread."""

    def __init__(
        self,
        reporting_service: Any,
        run_id: str,
        export_format: Union[ExportFormat, str],
        output_path: Union[str, Path],
        enable_privacy: bool = True,
        overwrite: bool = True,
    ) -> None:
        """Initialize the background export task.

        Args:
            reporting_service: Configured ReportingService instance.
            run_id: Unique test run identifier.
            export_format: Export format (JSON, CSV, MARKDOWN).
            output_path: Destination path for the exported report.
            enable_privacy: If True, masks public WAN IPs and MAC addresses.
            overwrite: If True, safely overwrites existing target with backup rollback.
        """
        super().__init__()
        self.reporting_service = reporting_service
        self.run_id = run_id
        self.export_format = export_format
        self.output_path = output_path
        self.enable_privacy = enable_privacy
        self.overwrite = overwrite

        self.signals = ExportWorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        """Execute the report export off-thread and emit completion or failure signals."""
        logger.info(
            "Background report export started: run_id=%s, format=%s, path=%s",
            self.run_id,
            self.export_format,
            self.output_path,
        )
        self.signals.sig_started.emit(self.run_id)

        try:
            record: ReportExport = self.reporting_service.export_report(
                run_id=self.run_id,
                export_format=self.export_format,
                output_path=self.output_path,
                enable_privacy=self.enable_privacy,
                overwrite=self.overwrite,
            )
            logger.info("Background report export completed successfully: export_id=%s", record.export_id)
            self.signals.sig_completed.emit(record)
        except Exception as exc:
            err_msg = str(exc)
            logger.error("Background report export failed for run %s: %s", self.run_id, err_msg, exc_info=True)
            self.signals.sig_failed.emit(self.run_id, err_msg)
