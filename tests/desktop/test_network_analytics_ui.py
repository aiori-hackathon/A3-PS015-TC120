"""UI and integration tests for Network Performance Analytics & Slowdown Detection.

Verifies:
- NetworkAnalyticsWidget layout, header badges, and time-window filter buttons.
- Time window switching (24H, 7D, 30D) and data re-evaluation.
- Chart tab switching (Latency/Jitter, Download Throughput, Packet Loss).
- Metric breakdown cards updating with baseline comparisons and status pills.
- Honest empty states when fewer than 3 completed runs exist.
- Multi-series chart rendering when sufficient data exists.
- Integration within NetworkMonitoringPage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ticnet.desktop.pages.network_monitoring_page import NetworkMonitoringPage
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.desktop.widgets.network_analytics_widget import NetworkAnalyticsWidget
from ticnet.models.monitoring import (
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringTriggerType,
)
from ticnet.monitoring.analytics import DetectionStatus, TimeWindow
from ticnet.persistence.database import DatabaseManager


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_ui_analytics.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def monitoring_service(test_db: DatabaseManager) -> MonitoringService:
    service = MonitoringService(db=test_db)
    yield service
    service.shutdown()


def test_network_analytics_widget_initial_empty_state(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify widget initializes with INSUFFICIENT DATA badge and honest empty chart view."""
    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()

    # 1. Measurement count badge
    assert widget.lbl_sample_count.text() == "0 measurements"

    # 2. Status banner
    assert widget.lbl_detection_badge.text() == "INSUFFICIENT DATA"
    assert "Insufficient" in widget.lbl_headline.text()

    # 3. Filter buttons present and 24H checked by default
    assert widget.btn_24h.isChecked() is True
    assert widget.btn_7d.isChecked() is False
    assert widget.btn_30d.isChecked() is False

    # 4. Chart stack displays empty view (View 1)
    assert widget.chart_stack.currentIndex() == 1
    widget.close()


def test_network_analytics_widget_filter_switching(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify clicking filter buttons switches active time window."""
    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()

    # Click 7 Days
    QTest.mouseClick(widget.btn_7d, QtCore.Qt.MouseButton.LeftButton)
    assert widget.btn_7d.isChecked() is True
    assert widget.btn_24h.isChecked() is False
    assert widget._active_window == TimeWindow.D7

    # Click 30 Days
    QTest.mouseClick(widget.btn_30d, QtCore.Qt.MouseButton.LeftButton)
    assert widget.btn_30d.isChecked() is True
    assert widget._active_window == TimeWindow.D30

    # Click 24 Hours
    QTest.mouseClick(widget.btn_24h, QtCore.Qt.MouseButton.LeftButton)
    assert widget.btn_24h.isChecked() is True
    assert widget._active_window == TimeWindow.H24
    widget.close()


def test_network_analytics_widget_populated_with_runs(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify that when 4 completed runs exist, chart and metric cards are rendered."""
    repo = monitoring_service.scheduler.repo
    now_utc = datetime.now(timezone.utc)

    # Insert 4 completed monitoring runs over the last 4 hours
    for i in range(4):
        t = (now_utc - timedelta(hours=4 - i)).isoformat()
        r = MonitoringRun(
            schedule_id="default",
            trigger_type=MonitoringTriggerType.SCHEDULED.value,
            scheduled_for_utc=t,
            started_at_utc=t,
            completed_at_utc=t,
            status=MonitoringRunStatus.COMPLETED.value,
            latency_ms=18.0 + (i * 0.5),
            jitter_ms=1.2,
            packet_loss_pct=0.0,
            download_mbps=95.0,
        )
        repo.create_monitoring_run(r)

    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()

    # Verify measurement count
    assert widget.lbl_sample_count.text() == "4 measurements"

    # Status banner should reflect NOMINAL PERFORMANCE
    assert widget.lbl_detection_badge.text() == "NOMINAL PERFORMANCE"
    assert "Nominal" in widget.lbl_headline.text()

    # Metric breakdown cards populated with real values
    assert widget.card_lat.lbl_val.text() != "—"
    assert "Baseline:" in widget.card_lat.lbl_sub.text()
    assert widget.card_lat.lbl_badge.text() == "NORMAL"

    # Chart stack should now show the interactive chart (View 0)
    assert widget.chart_stack.currentIndex() == 0

    # Test chart tab switching
    QTest.mouseClick(widget.btn_tab_dl, QtCore.Qt.MouseButton.LeftButton)
    assert widget._active_chart_tab == "download"

    QTest.mouseClick(widget.btn_tab_loss, QtCore.Qt.MouseButton.LeftButton)
    assert widget._active_chart_tab == "loss"
    widget.close()


def test_network_monitoring_page_contains_analytics_section(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify NetworkMonitoringPage contains and updates NetworkAnalyticsWidget."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    assert hasattr(page, "analytics_widget")
    assert page.analytics_widget is not None
    assert isinstance(page.analytics_widget, NetworkAnalyticsWidget)
    assert not page.analytics_widget.isHidden()
    page.close()
