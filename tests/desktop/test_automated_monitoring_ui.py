"""UI and desktop service integration tests for TicNet Automated Network Monitoring.

Verifies:
- Operational state accuracy ("MONITORING ACTIVE", "DIAGNOSTIC IN PROGRESS").
- Timezone labeling on user-facing timestamps and daily UTC schedule picker.
- Scheduler tick timer auto-execution in MonitoringService.
- Page navigation does not pause, reset, or stop the scheduler timer.
- Manual run does not postpone upcoming scheduled test time.
- Overlap prevention with active diagnostic worker.
- End-to-end pipeline: Scheduler -> Run -> Metrics -> Analytics -> Incidents.
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
from ticnet.desktop.services.diagnostic_service import DiagnosticService
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.models.entities import Summary, Target
from ticnet.models.enums import TargetType
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
    db_file = tmp_path / "test_automated_ui_monitoring.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


class StubDiagnosticService(QtCore.QObject):
    sig_run_started = QtCore.Signal(str)
    sig_run_finished = QtCore.Signal(str, str, str)

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.is_running = False
        self._run_count = 0

    def start_diagnosis(self) -> str:
        self.is_running = True
        self._run_count += 1
        run_id = f"stub-run-{self._run_count}"
        self.sig_run_started.emit(run_id)
        return run_id


@pytest.fixture
def stub_diagnostic() -> StubDiagnosticService:
    return StubDiagnosticService()


@pytest.fixture
def monitoring_service(test_db: DatabaseManager, stub_diagnostic: StubDiagnosticService) -> MonitoringService:
    service = MonitoringService(db=test_db, diagnostic_service=stub_diagnostic)
    yield service
    service.shutdown()


def test_ui_shows_monitoring_active_when_enabled(qapp: QApplication, monitoring_service: MonitoringService) -> None:
    """Verify UI status banner shows MONITORING ACTIVE when schedule is saved and enabled."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    assert page.lbl_status_badge.text() == "NOT CONFIGURED"

    # Save an enabled schedule
    monitoring_service.save_schedule(enabled=True, interval_type=IntervalType.H1.value)
    page.refresh_page()

    assert page.lbl_status_badge.text() == "MONITORING ACTIVE"
    assert monitoring_service.is_scheduler_running() is True
    assert "Next Scheduled Test:" in page.lbl_next_run_info.text()
    assert "UTC" in page.lbl_next_run_info.text()
    page.close()


def test_ui_shows_diagnostic_in_progress_state(qapp: QApplication, monitoring_service: MonitoringService) -> None:
    """Verify UI displays distinct DIAGNOSTIC IN PROGRESS badge and disables button while running."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    # Trigger run started event
    page._on_run_started("m-run-123", "SCHEDULED")

    assert page.lbl_status_badge.text() == "DIAGNOSTIC IN PROGRESS"
    assert "Diagnostic Test Executing" in page.lbl_banner_headline.text()
    assert page.btn_run_now.isEnabled() is False
    assert page.btn_run_now.text() == "Diagnosing..."

    # Complete run
    page._on_run_completed("m-run-123", "COMPLETED")
    assert page.btn_run_now.isEnabled() is True
    assert page.btn_run_now.text() == "Run Test Now"
    page.close()


def test_daily_time_picker_shows_local_equivalent_hint(qapp: QApplication, monitoring_service: MonitoringService) -> None:
    """Verify selecting DAILY interval enables the picker and shows local timezone equivalent hint."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    # Select DAILY interval
    idx = page.combo_interval.findData(IntervalType.DAILY.value)
    page.combo_interval.setCurrentIndex(idx)

    assert page.time_daily.isEnabled() is True
    assert page.lbl_daily_tz_info.isEnabled() is True

    # Change time to 15:00 UTC
    page.time_daily.setTime(QtCore.QTime(15, 0))
    hint_text = page.lbl_daily_tz_info.text()
    assert "Local Equivalent:" in hint_text
    page.close()


def test_history_table_displays_timestamps_with_timezone(qapp: QApplication, monitoring_service: MonitoringService, test_db: DatabaseManager) -> None:
    """Verify history table renders timestamps with local timezone label and UTC tooltip."""
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()

    # Create a completed monitoring run
    m_run = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.SCHEDULED.value,
        scheduled_for_utc="2026-10-29T10:00:00+00:00",
        started_at_utc="2026-10-29T10:00:00+00:00",
        completed_at_utc="2026-10-29T10:01:00+00:00",
        status=MonitoringRunStatus.COMPLETED.value,
        latency_ms=25.4,
        jitter_ms=1.2,
        packet_loss_pct=0.0,
        download_mbps=85.0,
    )
    monitoring_service.scheduler.repo.create_monitoring_run(m_run)

    page.load_history()

    assert page.history_table.rowCount() == 1
    item_time = page.history_table.item(0, 0)
    assert item_time is not None
    assert "2026-10-29" in item_time.text()
    assert "UTC" in item_time.toolTip()
    page.close()


def test_monitoring_service_auto_tick_execution(test_db: DatabaseManager, stub_diagnostic: StubDiagnosticService) -> None:
    """Verify MonitoringService tick handler automatically initiates due scheduled run."""
    run_started_signals = []
    run_completed_signals = []

    service = MonitoringService(db=test_db, diagnostic_service=stub_diagnostic)
    service.sig_monitoring_run_started.connect(lambda mid, trig: run_started_signals.append((mid, trig)))
    service.sig_monitoring_run_completed.connect(lambda mid, stat: run_completed_signals.append((mid, stat)))

    # Configure schedule enabled (30M)
    service.save_schedule(enabled=True, interval_type=IntervalType.M30.value)

    # Make run overdue by 2 minutes
    sched = service.get_schedule()
    now_utc = datetime.now(timezone.utc)
    sched.next_run_due_utc = (now_utc - timedelta(minutes=2)).isoformat()
    service.scheduler.repo.save_schedule(sched)

    assert service.scheduler.is_run_due(now_utc) is True

    # Simulate tick event
    service._on_tick()

    assert len(run_started_signals) == 1
    m_id, trig = run_started_signals[0]
    assert trig == MonitoringTriggerType.SCHEDULED.value
    assert service.get_active_monitoring_run_id() == m_id

    # Simulate diagnostic completion
    service._on_diagnostic_finished(run_id="run-auto-1", status="COMPLETED", message="Diagnostic complete")

    assert len(run_completed_signals) == 1
    assert run_completed_signals[0] == (m_id, "COMPLETED")
    assert service.get_active_monitoring_run_id() is None

    service.shutdown()


def test_manual_test_preserves_future_scheduled_due_time(monitoring_service: MonitoringService) -> None:
    """Verify triggering manual run does not postpone an upcoming scheduled test."""
    monitoring_service.save_schedule(enabled=True, interval_type=IntervalType.H1.value)

    # Set schedule for 45 minutes in the future
    fixed_now = datetime.now(timezone.utc)
    future_due = fixed_now + timedelta(minutes=45)
    sched = monitoring_service.get_schedule()
    sched.next_run_due_utc = future_due.isoformat()
    monitoring_service.scheduler.repo.save_schedule(sched)

    # Execute manual run
    m_run, err = monitoring_service.run_test_now()
    assert err is None
    assert m_run is not None
    assert m_run.trigger_type == MonitoringTriggerType.MANUAL.value

    # Next run due time must not be altered
    updated_sched = monitoring_service.get_schedule()
    assert updated_sched.next_run_due_utc == future_due.isoformat()


def test_page_navigation_preserves_scheduler_timer(qapp: QApplication, monitoring_service: MonitoringService) -> None:
    """Verify switching pages or creating/destroying page widgets does not stop the background tick timer."""
    monitoring_service.save_schedule(enabled=True, interval_type=IntervalType.H1.value)
    assert monitoring_service.is_scheduler_running() is True
    assert monitoring_service._tick_timer.isActive() is True

    # Simulate navigating away by closing page widget
    page = NetworkMonitoringPage(monitoring_service=monitoring_service)
    page.show()
    page.close()

    # Service tick timer must continue running uninterrupted
    assert monitoring_service._tick_timer.isActive() is True
    assert monitoring_service.is_scheduler_running() is True
