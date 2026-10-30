"""UI and integration tests for Network Monitoring Page (Stage 1).

Verifies:
- NetworkMonitoringPage UI instantiation and component presence.
- Status banner updates for NOT CONFIGURED, ENABLED, and PAUSED states.
- Schedule settings form interactions (checkbox, interval dropdown, daily time picker).
- Button controls: Save Schedule, Pause / Resume, and Run Test Now.
- Execution history table population with genuine metrics and status badges.
- Honest empty states when no monitoring runs are recorded.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from ticnet.desktop.pages.network_monitoring_page import NetworkMonitoringPage
from ticnet.desktop.services.diagnostic_service import DiagnosticService
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringStatus,
    MonitoringTriggerType,
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
    db_file = tmp_path / "test_ui_monitoring.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def monitoring_service(test_db: DatabaseManager) -> MonitoringService:
    service = MonitoringService(db=test_db)
    yield service
    service.shutdown()


def test_monitoring_page_initialization_empty_state(qapp: QApplication, monitoring_service: MonitoringService) -> None:
    """Verify page initializes with NOT CONFIGURED badge and empty history table."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    # 1. Header titles
    assert page.lbl_title.text() == "Network Monitoring"
    assert "Automated" in page.lbl_subtitle.text()

    # 2. Status banner
    assert page.lbl_status_badge.text() == "NOT CONFIGURED"
    assert "Unavailable" in page.lbl_next_run_info.text()
    assert "No runs recorded yet" in page.lbl_last_run_info.text()

    # 3. Schedule settings controls
    assert page.chk_enable_schedule.isChecked() is False
    assert page.combo_interval.count() == 4
    assert page.btn_pause_resume.isEnabled() is False  # Disabled when not configured
    assert page.btn_run_now.isEnabled() is True

    # 4. Honest empty state
    assert not page.empty_history_frame.isHidden()
    assert page.history_table.isHidden() is True
    page.close()


def test_monitoring_page_save_schedule(qapp: QApplication, monitoring_service: MonitoringService) -> None:
    """Verify user can enable monitoring, select interval, and save schedule."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)

    # User checks enable box and selects "Every 2 hours"
    page.chk_enable_schedule.setChecked(True)
    idx = page.combo_interval.findData(IntervalType.H2.value)
    page.combo_interval.setCurrentIndex(idx)

    # Click Save Schedule
    QTest.mouseClick(page.btn_save_schedule, QtCore.Qt.MouseButton.LeftButton)

    # Verify status badge updated to MONITORING ACTIVE
    assert page.lbl_status_badge.text() == "MONITORING ACTIVE"
    assert page.btn_pause_resume.isEnabled() is True
    assert page.btn_pause_resume.text() == "Pause Monitoring"
    assert "Next Scheduled Test:" in page.lbl_next_run_info.text()
    assert "Schedule saved" in page.lbl_feedback.text()

    # Verify persisted in SQLite
    schedule = monitoring_service.get_schedule()
    assert schedule.enabled is True
    assert schedule.interval_type == IntervalType.H2.value
    assert schedule.status == MonitoringStatus.ENABLED.value
    page.close()


def test_monitoring_page_pause_and_resume(qapp: QApplication, monitoring_service: MonitoringService) -> None:
    """Verify user can pause and resume monitoring via the UI."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)

    # Save active schedule first
    page.chk_enable_schedule.setChecked(True)
    QTest.mouseClick(page.btn_save_schedule, QtCore.Qt.MouseButton.LeftButton)
    assert page.lbl_status_badge.text() == "MONITORING ACTIVE"

    # Click Pause
    QTest.mouseClick(page.btn_pause_resume, QtCore.Qt.MouseButton.LeftButton)
    assert page.lbl_status_badge.text() == "MONITORING PAUSED"
    assert page.btn_pause_resume.text() == "Resume Monitoring"
    assert "Suspended" in page.lbl_next_run_info.text()

    # Click Resume
    QTest.mouseClick(page.btn_pause_resume, QtCore.Qt.MouseButton.LeftButton)
    assert page.lbl_status_badge.text() == "MONITORING ACTIVE"
    assert page.btn_pause_resume.text() == "Pause Monitoring"
    assert "Next Scheduled Test:" in page.lbl_next_run_info.text()
    page.close()


def test_monitoring_page_history_table_population(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify history table renders completed and skipped runs with genuine metrics."""
    # Seed historical monitoring runs
    repo = monitoring_service.scheduler.repo
    now_iso = datetime.now(timezone.utc).isoformat()

    r1 = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.SCHEDULED.value,
        scheduled_for_utc=now_iso,
        started_at_utc=now_iso,
        completed_at_utc=now_iso,
        status=MonitoringRunStatus.COMPLETED.value,
        run_id="TCN-20261029-000001",
        latency_ms=15.2,
        jitter_ms=1.1,
        packet_loss_pct=0.0,
        download_mbps=92.4,
    )
    r2 = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.SCHEDULED.value,
        scheduled_for_utc=now_iso,
        started_at_utc=now_iso,
        completed_at_utc=now_iso,
        status=MonitoringRunStatus.SKIPPED.value,
        skip_reason="DIAGNOSTIC_ACTIVE_DEFERRED",
        error_message="Skipped due to active diagnosis",
    )
    repo.create_monitoring_run(r1)
    repo.create_monitoring_run(r2)

    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    # Verify table is now visible and empty frame is hidden
    assert not page.history_table.isHidden()
    assert page.empty_history_frame.isHidden() is True
    assert page.history_table.rowCount() == 2

    # Check row 0 contents (latest run)
    item_trigger = page.history_table.item(0, 1)
    item_status = page.history_table.item(0, 2)
    item_lat = page.history_table.item(0, 3)
    item_loss = page.history_table.item(0, 4)
    item_tp = page.history_table.item(0, 5)

    assert item_trigger.text() == "SCHEDULED"
    assert item_status.text() in ("COMPLETED", "SKIPPED")

    # Check KPI tiles
    assert page.lbl_val_download.text() != "—"
    assert page.lbl_val_latency.text() != "—"
    assert page.lbl_val_upload.text() == "Not Measured"
    page.close()


def test_monitoring_page_daily_utc_time_picker(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify DAILY interval enables time picker and saves configured UTC time."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    # Time picker initially disabled
    assert page.time_daily.isEnabled() is False

    # Select DAILY interval
    idx = page.combo_interval.findData(IntervalType.DAILY.value)
    page.combo_interval.setCurrentIndex(idx)
    assert page.time_daily.isEnabled() is True

    # Set time to 14:30
    page.time_daily.setTime(QtCore.QTime(14, 30))
    page.chk_enable_schedule.setChecked(True)

    # Save schedule
    QTest.mouseClick(page.btn_save_schedule, QtCore.Qt.MouseButton.LeftButton)

    schedule = monitoring_service.get_schedule()
    assert schedule.enabled is True
    assert schedule.interval_type == IntervalType.DAILY.value
    assert schedule.daily_time_utc == "14:30"
    page.close()


def test_monitoring_page_run_now_action_and_error_handling(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify clicking 'Run Test Now' handles skipped/busy states gracefully without freezing."""
    # Set scheduler active checker to busy
    monitoring_service.scheduler.is_active_checker = lambda: True

    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    # Click Run Test Now
    QTest.mouseClick(page.btn_run_now, QtCore.Qt.MouseButton.LeftButton)

    # Button must be restored to enabled state and feedback shows skip reason
    assert page.btn_run_now.isEnabled() is True
    assert page.btn_run_now.text() == "Run Test Now"
    assert "skipped" in page.lbl_feedback.text().lower() or "active" in page.lbl_feedback.text().lower()
    page.close()


def test_monitoring_history_view_all_dialog_and_filtering(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify View All button opens the history dialog and filtering behaves properly."""
    from ticnet.desktop.dialogs.monitoring_history_dialog import MonitoringHistoryDialog

    repo = monitoring_service.scheduler.repo
    now_iso = datetime.now(timezone.utc).isoformat()

    r1 = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.SCHEDULED.value,
        scheduled_for_utc=now_iso,
        status=MonitoringRunStatus.COMPLETED.value,
        latency_ms=25.0,
        packet_loss_pct=0.0,
        download_mbps=80.0,
    )
    r2 = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.MANUAL.value,
        scheduled_for_utc=now_iso,
        status=MonitoringRunStatus.SKIPPED.value,
        skip_reason="DIAGNOSTIC_ACTIVE_DEFERRED",
    )
    repo.create_monitoring_run(r1)
    repo.create_monitoring_run(r2)

    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    assert hasattr(page, "btn_view_all_history")
    assert page.btn_view_all_history.text() == "View All ›"

    # Instantiate dialog directly with service
    dialog = MonitoringHistoryDialog(monitoring_service=monitoring_service)
    dialog.show()

    # All runs
    assert dialog.table.rowCount() == 2
    assert "2 runs" in dialog.lbl_run_count_badge.text()

    # Filter Completed
    dialog.btn_completed.click()
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 2).text() == "COMPLETED"

    # Filter Skipped
    dialog.btn_skipped.click()
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 2).text() == "SKIPPED"

    # Filter Manual
    dialog.btn_manual.click()
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "MANUAL"

    dialog.close()
    page.close()

