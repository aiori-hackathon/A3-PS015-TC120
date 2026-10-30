"""Application entry point for TicNet Native Windows Desktop (Phase 10).

Initializes High-DPI scaling, logging, theme stylesheets, database services,
and launches the PySide6 MainWindow application event loop.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Ensure src root is in sys.path for direct script execution
_src_dir = str(Path(__file__).resolve().parent.parent.parent)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from PySide6 import QtCore
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ticnet.desktop import paths
from ticnet.desktop.main_window import MainWindow
from ticnet.desktop.services import DesktopServices
from ticnet.desktop.theme import get_application_stylesheet
from ticnet.utils.logging import get_logger, setup_logging

logger = get_logger("desktop.app")


def create_application(
    services: Optional[DesktopServices] = None,
) -> tuple[QApplication, MainWindow]:
    """Create and configure the QApplication and MainWindow instances.

    Args:
        services: Optional DesktopServices container (initialized if None).

    Returns:
        Tuple of configured (QApplication, MainWindow).
    """
    # High-DPI scaling configuration for Windows Per-Monitor V2 DPI awareness
    if hasattr(Qt.HighDpiScaleFactorRoundingPolicy, "PassThrough"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    # Initialize QApplication if not already created (useful for tests)
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)

    app.setApplicationName("TicNet")
    app.setOrganizationName("TicNet")
    app.setApplicationDisplayName("TicNet — Explainable Network Diagnostics")

    # Apply global QSS stylesheet
    app.setStyleSheet(get_application_stylesheet())

    if services is None:
        try:
            services = DesktopServices.create_default()
        except Exception as exc:
            logger.warning("Could not initialize default DesktopServices: %s", exc)

    window = MainWindow(services=services)
    return app, window


def main() -> int:
    """Main application entry point."""
    # Initialize privacy-redacting logging
    log_dir = paths.get_default_log_dir()
    log_file = log_dir / "ticnet.log"
    setup_logging(log_file=log_file)
    logger.info("Starting TicNet Native Desktop Application (PySide6)")

    app, window = create_application()
    window.show()

    exit_code = app.exec()
    logger.info("TicNet desktop application exited with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
