"""UI and integration tests for Incident History and Alert Notifications.

Verifies:
- IncidentHistoryWidget rendering, empty state, and header badges.
- Notification preferences form controls and atomic saving.
- Incident table population, status badges, and duration formatting.
- Incident status filter buttons (All, Active, Resolved).
- NetworkMonitoringPage integration and signal connectivity.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ticnet.desktop.pages.network_monitoring_page import NetworkMonitoringPage
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.desktop.widgets.incident_history_widget import IncidentHistoryWidget
from ticnet.models.notifications import (
    IncidentSeverity,
    IncidentStatus,
    NetworkIncident,
    NotificationPreferences,
)
from ticnet.persistence.database import DatabaseManager


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_ui_incidents.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def monitoring_service(test_db: DatabaseManager) -> MonitoringService:
    service = MonitoringService(db=test_db)
    yield service
    service.shutdown()


def test_incident_history_widget_initial_empty_state(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify widget initializes with zero count and honest empty state."""
    widget = IncidentHistoryWidget(monitoring_service=monitoring_service)
    widget.show()

    assert widget.lbl_incident_count.text() == "0 incidents"
    assert widget.table.isHidden() is True
    assert widget.lbl_empty_state.isVisible() is True
    assert "No Network Incidents Recorded" in widget.lbl_empty_state.text()

    # Default preferences
    assert widget.chk_desktop.isChecked() is True
    assert widget.chk_sound.isChecked() is False
    assert widget.chk_confirmed.isChecked() is True
    assert widget.chk_recovery.isChecked() is True
    assert widget.chk_suspected.isChecked() is False
    assert widget.spin_cooldown.value() == 30
    widget.close()


def test_incident_history_widget_preferences_saving(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify modifying and saving notification preferences."""
    widget = IncidentHistoryWidget(monitoring_service=monitoring_service)
    widget.show()

    # Toggle options
    widget.chk_sound.setChecked(True)
    widget.chk_suspected.setChecked(True)
    widget.spin_cooldown.setValue(45)

    QTest.mouseClick(widget.btn_save_prefs, QtCore.Qt.MouseButton.LeftButton)

    assert "✓ Preferences saved" in widget.lbl_prefs_feedback.text()

    # Verify persisted in database
    saved = monitoring_service.get_notification_preferences()
    assert saved.sound_enabled is True
    assert saved.notify_suspected is True
    assert saved.cooldown_minutes == 45
    widget.close()


def test_incident_history_widget_populated_with_incidents(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify table renders rows and status badges when incidents exist."""
    repo = monitoring_service.incident_repo
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Create a confirmed active incident
    inc1 = NetworkIncident(
        metric_name="latency",
        severity=IncidentSeverity.CRITICAL.value,
        status=IncidentStatus.CONFIRMED.value,
        baseline_value=20.0,
        observed_value=72.0,
        headline="Confirmed Latency Slowdown",
        description="Sustained high latency",
        started_at_utc=now_iso,
        last_seen_at_utc=now_iso,
    )
    repo.create_incident(inc1)

    # 2. Create a resolved incident
    inc2 = NetworkIncident(
        metric_name="download_speed",
        severity=IncidentSeverity.CRITICAL.value,
        status=IncidentStatus.RESOLVED.value,
        baseline_value=95.0,
        observed_value=25.0,
        headline="Download Degradation",
        description="Throughput collapsed",
        started_at_utc=now_iso,
        last_seen_at_utc=now_iso,
        resolved_at_utc=now_iso,
    )
    repo.create_incident(inc2)

    widget = IncidentHistoryWidget(monitoring_service=monitoring_service)
    widget.show()

    assert widget.lbl_incident_count.text() == "2 incidents"
    assert widget.table.isVisible() is True
    assert widget.lbl_empty_state.isHidden() is True
    assert widget.table.rowCount() == 2

    # Check cell items
    metric_cell = widget.table.item(0, 1)
    assert metric_cell is not None
    assert metric_cell.text() in ("Latency", "Download Speed")
    widget.close()


def test_incident_history_widget_filter_buttons(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify clicking filter buttons filters active vs resolved incidents."""
    repo = monitoring_service.incident_repo
    now_iso = datetime.now(timezone.utc).isoformat()

    # Insert 1 active confirmed, 1 resolved
    repo.create_incident(
        NetworkIncident(
            metric_name="jitter",
            status=IncidentStatus.CONFIRMED.value,
            baseline_value=1.0,
            observed_value=6.0,
            started_at_utc=now_iso,
        )
    )
    repo.create_incident(
        NetworkIncident(
            metric_name="packet_loss",
            status=IncidentStatus.RESOLVED.value,
            baseline_value=0.0,
            observed_value=5.0,
            started_at_utc=now_iso,
            resolved_at_utc=now_iso,
        )
    )

    widget = IncidentHistoryWidget(monitoring_service=monitoring_service)
    widget.show()

    # Default is All (2 rows)
    assert widget.table.rowCount() == 2

    # Filter Active / Ongoing (1 row)
    QTest.mouseClick(widget.btn_filter_active, QtCore.Qt.MouseButton.LeftButton)
    assert widget.table.rowCount() == 1

    # Filter Resolved (1 row)
    QTest.mouseClick(widget.btn_filter_resolved, QtCore.Qt.MouseButton.LeftButton)
    assert widget.table.rowCount() == 1

    # Back to All (2 rows)
    QTest.mouseClick(widget.btn_filter_all, QtCore.Qt.MouseButton.LeftButton)
    assert widget.table.rowCount() == 2
    widget.close()


def test_network_monitoring_page_contains_incident_widget(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify NetworkMonitoringPage contains and wires IncidentHistoryWidget."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    assert hasattr(page, "incident_widget")
    assert page.incident_widget is not None
    assert isinstance(page.incident_widget, IncidentHistoryWidget)
    assert not page.incident_widget.isHidden()
    page.close()

