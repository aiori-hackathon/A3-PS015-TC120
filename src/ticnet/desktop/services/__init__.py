"""Service adapter layer for TicNet desktop presentation layer.

Exposes thin, thread-safe service adapters connecting PySide6 desktop views
to the underlying measurement orchestrator, persistence repositories,
diagnosis engine, and report exporters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ticnet.configuration.loader import load_app_config
from ticnet.configuration.validator import AppConfig
from ticnet.desktop.services.diagnostic_service import DiagnosticService
from ticnet.desktop.services.findings_service import FindingsService
from ticnet.desktop.services.history_service import HistoricalRunDetail, HistoryService
from ticnet.desktop.services.live_telemetry_service import (
    LiveTelemetrySample,
    LiveTelemetryService,
)
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.desktop.services.reporting_service import ReportingService
from ticnet.desktop.services.settings_service import SettingsService
from ticnet.persistence.database import DatabaseManager
from ticnet.utils.logging import get_logger

logger = get_logger("desktop.services")


@dataclass
class DesktopServices:
    """Dependency container providing access to all desktop service adapters."""

    db: DatabaseManager
    config: AppConfig
    diagnostic: DiagnosticService
    history: HistoryService
    findings: FindingsService
    reporting: ReportingService
    settings: SettingsService
    telemetry: Optional[LiveTelemetryService] = None
    monitoring: Optional[MonitoringService] = None

    @classmethod
    def create_default(
        cls,
        db_path: Optional[Union[str, Path]] = None,
        config: Optional[AppConfig] = None,
        run_migrations: bool = True,
    ) -> DesktopServices:
        """Create and wire the default desktop services suite.

        Args:
            db_path: Optional path to local SQLite database (defaults to per-user data dir).
            config: Optional pre-loaded AppConfig.
            run_migrations: If True, applies pending SQLite migrations.

        Returns:
            Fully initialized DesktopServices container.
        """
        if db_path is None:
            from ticnet.desktop.paths import get_default_database_path
            target_path = get_default_database_path()
        else:
            target_path = Path(db_path)

        db = DatabaseManager(db_path=target_path)
        if run_migrations:
            try:
                applied = db.run_migrations()
                if applied:
                    logger.info("Applied %d SQLite migrations for desktop services", len(applied))
            except Exception as exc:
                logger.error("Failed to run SQLite migrations: %s", exc, exc_info=True)

        settings_service = SettingsService(initial_config=config)
        app_config = settings_service.active_config

        diagnostic_service = DiagnosticService(db=db, config=app_config)
        history_service = HistoryService(db=db)
        findings_service = FindingsService(db=db)
        reporting_service = ReportingService(db=db)
        telemetry_service = LiveTelemetryService(
            target_ip="1.1.1.1",
            interval_s=1.0,
        )
        monitoring_service = MonitoringService(
            db=db,
            diagnostic_service=diagnostic_service,
            telemetry_service=telemetry_service,
        )

        return cls(
            db=db,
            config=app_config,
            diagnostic=diagnostic_service,
            history=history_service,
            findings=findings_service,
            reporting=reporting_service,
            settings=settings_service,
            telemetry=telemetry_service,
            monitoring=monitoring_service,
        )


__all__ = [
    "DesktopServices",
    "DiagnosticService",
    "HistoryService",
    "HistoricalRunDetail",
    "FindingsService",
    "LiveTelemetryService",
    "LiveTelemetrySample",
    "ReportingService",
    "SettingsService",
    "MonitoringService",
]
