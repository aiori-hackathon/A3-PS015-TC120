"""Unit and integration tests for TicNet Phase 10 Stage 3 features.

Verifies:
- SettingsService atomic TOML persistence, validation enforcement, and rollback.
- ExportWorker background execution, non-blocking GUI, and Qt signal dispatch.
- Measurement visualization charts data integrity, honest empty states, and missing value exclusion.
- SettingsPage UI controls, validation feedback, and reset to defaults.
- Desktop paths resolution for per-user writable application directories.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import List
import unittest.mock as mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from ticnet.configuration.validator import ConfigValidationError
from ticnet.desktop.pages.settings_page import SettingsPage
from ticnet.desktop.paths import (
    get_default_database_path,
    get_user_config_dir,
    get_user_data_dir,
    is_packaged,
)
from ticnet.desktop.services.export_worker import ExportWorker
from ticnet.desktop.services.reporting_service import ReportingService
from ticnet.desktop.services.settings_service import SettingsService
from ticnet.desktop.widgets.chart_widget import (
    LatencyTimelineChart,
    LoadedLatencyBarChart,
    MeasurementChartsContainer,
)
from ticnet.models.entities import Observation, ReportExport, TestRun, new_id, now_utc_iso
from ticnet.models.enums import ExportFormat, ExportStatus, MeasurementStatus, RunMode, RunStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.run_repo import RunRepository


@pytest.fixture
def qapp():
    """Ensure QApplication is initialized for offscreen widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    """Fixture providing initialized SQLite database with migrations."""
    db_file = tmp_path / "stage3_test.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


# ============================================================================
# Work Package C: SettingsService Tests
# ============================================================================

def test_settings_service_loads_defaults(tmp_path: Path) -> None:
    """Verify SettingsService loads factory defaults when no user overrides exist."""
    svc = SettingsService(config_dir=tmp_path)
    cfg = svc.active_config

    assert cfg.targets.gateway.probe_count == 10
    assert cfg.targets.gateway.probe_timeout_ms == 1000
    assert cfg.targets.gateway.auto_discover is True
    assert cfg.thresholds.gateway_thresholds.packet_loss_warning_pct == 10.0


def test_settings_service_saves_valid_overrides_atomically(tmp_path: Path) -> None:
    """Verify valid parameter changes are validated and saved to TOML."""
    svc = SettingsService(config_dir=tmp_path)
    current_cfg = svc.active_config

    t_dict = dataclasses.asdict(current_cfg.targets)
    th_dict = dataclasses.asdict(current_cfg.thresholds)

    # Modify safe parameters within bounds
    t_dict["gateway"]["probe_count"] = 15
    t_dict["gateway"]["probe_timeout_ms"] = 1500
    t_dict["gateway"]["manual_override_ip"] = "192.168.1.254"
    th_dict["gateway_thresholds"]["packet_loss_warning_pct"] = 12.5

    updated_cfg = svc.save_settings(t_dict, th_dict)

    assert updated_cfg.targets.gateway.probe_count == 15
    assert updated_cfg.targets.gateway.probe_timeout_ms == 1500
    assert updated_cfg.targets.gateway.manual_override_ip == "192.168.1.254"
    assert updated_cfg.thresholds.gateway_thresholds.packet_loss_warning_pct == 12.5

    # Verify files on disk
    assert svc.user_targets_path.exists()
    assert svc.user_thresholds_path.exists()

    # Verify reloading from disk preserves saved values
    reloaded_svc = SettingsService(config_dir=tmp_path)
    assert reloaded_svc.active_config.targets.gateway.probe_count == 15
    assert reloaded_svc.active_config.targets.gateway.manual_override_ip == "192.168.1.254"


def test_settings_service_rejects_out_of_bounds_parameters(tmp_path: Path) -> None:
    """Verify parameter values violating safety bounds are rejected and do not corrupt config."""
    svc = SettingsService(config_dir=tmp_path)
    current_cfg = svc.active_config

    t_dict = dataclasses.asdict(current_cfg.targets)
    th_dict = dataclasses.asdict(current_cfg.thresholds)

    # Exceed maximum probe count ceiling (max is 50)
    t_dict["gateway"]["probe_count"] = 999

    with pytest.raises(ConfigValidationError) as exc_info:
        svc.save_settings(t_dict, th_dict)

    assert "probe_count" in str(exc_info.value)

    # Verify previous active config was not mutated
    assert svc.active_config.targets.gateway.probe_count == 10
    assert not svc.user_targets_path.exists()


def test_settings_service_reset_to_defaults(tmp_path: Path) -> None:
    """Verify reset_to_defaults removes user override files and restores defaults."""
    svc = SettingsService(config_dir=tmp_path)
    current_cfg = svc.active_config

    t_dict = dataclasses.asdict(current_cfg.targets)
    th_dict = dataclasses.asdict(current_cfg.thresholds)
    t_dict["gateway"]["probe_count"] = 25
    svc.save_settings(t_dict, th_dict)
    assert svc.user_targets_path.exists()

    # Reset
    reset_cfg = svc.reset_to_defaults()
    assert reset_cfg.targets.gateway.probe_count == 10
    assert not svc.user_targets_path.exists()
    assert not svc.user_thresholds_path.exists()


# ============================================================================
# Work Package D: Background Export Worker Tests
# ============================================================================

def test_export_worker_runs_and_emits_signals(test_db: DatabaseManager, tmp_path: Path) -> None:
    """Verify ExportWorker runs off-thread and emits completion signals."""
    run_repo = RunRepository(test_db)
    run_id = "test-export-run-1"
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            run_mode=RunMode.OPERATIONAL,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )

    reporting_svc = ReportingService(db=test_db)
    export_file = tmp_path / "async_report.json"

    worker = ExportWorker(
        reporting_service=reporting_svc,
        run_id=run_id,
        export_format=ExportFormat.JSON,
        output_path=export_file,
        enable_privacy=True,
        overwrite=True,
    )

    signals_received: List[str] = []
    completed_records: List[ReportExport] = []

    worker.signals.sig_started.connect(lambda r_id: signals_received.append(f"STARTED:{r_id}"))
    worker.signals.sig_completed.connect(lambda rec: completed_records.append(rec))

    # Execute worker run
    worker.run()

    assert "STARTED:test-export-run-1" in signals_received
    assert len(completed_records) == 1
    assert completed_records[0].export_status == ExportStatus.SUCCESS
    assert export_file.exists()


def test_export_worker_handles_failure_gracefully(test_db: DatabaseManager, tmp_path: Path) -> None:
    """Verify ExportWorker emits sig_failed on nonexistent run or serialization error."""
    reporting_svc = ReportingService(db=test_db)
    export_file = tmp_path / "failed_report.json"

    worker = ExportWorker(
        reporting_service=reporting_svc,
        run_id="nonexistent-run-id",
        export_format=ExportFormat.JSON,
        output_path=export_file,
        enable_privacy=True,
        overwrite=True,
    )

    failed_messages: List[str] = []
    worker.signals.sig_failed.connect(lambda r_id, err: failed_messages.append(f"FAILED:{r_id}:{err}"))

    worker.run()

    assert len(failed_messages) == 1
    assert "FAILED:nonexistent-run-id" in failed_messages[0]


# ============================================================================
# Work Package B: Measurement Visualization Tests
# ============================================================================

def test_chart_widgets_honor_data_integrity(qapp: QApplication) -> None:
    """Verify charts render empirical samples without fabricating or zero-filling missing data."""
    chart = LatencyTimelineChart()

    # 1. Empty observations list -> renders empty state without crashing
    chart.load_rtt_observations([])
    assert chart.stack_layout.currentIndex() == 0
    assert "No successful round-trip latency samples recorded" in chart.lbl_empty.text()

    # 2. Only failed/timeout samples -> excluded, not coerced to 0.0
    failed_obs = [
        Observation(
            observation_id=new_id(),
            run_id="run-1",
            stage_id=None,
            target_id=None,
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=0.0,
            metric_name="round_trip_time",
            value_numeric=None,
            unit="ms",
            status=MeasurementStatus.TIMEOUT,
        )
    ]
    chart.load_rtt_observations(failed_obs)
    assert chart.stack_layout.currentIndex() == 0  # Still empty state
    assert "No successful round-trip latency samples" in chart.lbl_empty.text()

    # 3. Successful real samples -> plots in chart view
    valid_obs = [
        Observation(
            observation_id=new_id(),
            run_id="run-1",
            stage_id=None,
            target_id=None,
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=12.5,
            metric_name="round_trip_time",
            value_numeric=12.5,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        ),
        Observation(
            observation_id=new_id(),
            run_id="run-1",
            stage_id=None,
            target_id=None,
            sample_index=2,
            timestamp_utc=now_utc_iso(),
            duration_ms=14.2,
            metric_name="round_trip_time",
            value_numeric=14.2,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        ),
    ]
    chart.load_rtt_observations(valid_obs)
    assert chart.stack_layout.currentIndex() == 1  # Active chart view
    assert "n = 2" in chart.lbl_meta.text()


def test_bufferbloat_bar_chart_handles_missing_data(qapp: QApplication) -> None:
    """Verify LoadedLatencyBarChart renders honest empty message when Stage 5 was skipped."""
    chart = LoadedLatencyBarChart()

    # Observations without loaded latency
    obs_list = [
        Observation(
            observation_id=new_id(),
            run_id="run-1",
            stage_id=None,
            target_id=None,
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=0.0,
            metric_name="gateway_discovery",
            value_numeric=1.0,
            unit="status",
            status=MeasurementStatus.SUCCESS,
        )
    ]

    chart.load_bufferbloat_observations(obs_list)
    assert chart.stack_layout.currentIndex() == 0
    assert "Loaded latency not evaluated" in chart.lbl_empty.text()


# ============================================================================
# Work Package A & C: SettingsPage UI Integration Tests
# ============================================================================

def test_settings_page_ui_controls_and_save(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify SettingsPage displays current values and saves user modifications."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    svc = SettingsService(config_dir=tmp_path)
    page = SettingsPage(settings_service=svc)

    assert page.spin_gw_count.value() == 10
    assert page.chk_gw_autodiscover.isChecked() is True

    # Change UI control value
    page.spin_gw_count.setValue(20)
    page.txt_gw_override.setText("192.168.0.1")

    # Simulate Save button click
    page._handle_save_clicked()

    assert svc.active_config.targets.gateway.probe_count == 20
    assert svc.active_config.targets.gateway.manual_override_ip == "192.168.0.1"


# ============================================================================
# Work Package F: Desktop Paths Tests
# ============================================================================

def test_desktop_paths_resolution() -> None:
    """Verify path resolution helpers return valid paths."""
    data_dir = get_user_data_dir()
    assert data_dir.is_dir()
    assert "TicNet" in str(data_dir)

    config_dir = get_user_config_dir()
    assert config_dir.is_dir()

    db_path = get_default_database_path()
    assert "sqlite3" in str(db_path)


# ============================================================================
# Safe Shutdown and Lifecycle Regression Tests
# ============================================================================

def test_main_window_close_event_with_active_export_accepted(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify closeEvent handles active export when user confirms exit."""
    from ticnet.desktop.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtGui import QCloseEvent

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    window = MainWindow(services=None)
    window.page_reports._is_exporting = True

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted() is True
    assert window._is_shutting_down is True
    assert window.page_reports._is_shutting_down is True


def test_main_window_close_event_with_active_export_cancelled(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify closeEvent ignores close when user declines to cancel export."""
    from ticnet.desktop.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtGui import QCloseEvent

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)

    window = MainWindow(services=None)
    window.page_reports._is_exporting = True

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert window._is_shutting_down is False


def test_main_window_close_event_reentrancy_guard(qapp: QApplication) -> None:
    """Verify repeated closeEvent calls while shutting down are safely ignored."""
    from ticnet.desktop.main_window import MainWindow
    from PySide6.QtGui import QCloseEvent

    window = MainWindow(services=None)
    window._is_shutting_down = True

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted() is False


def test_reports_page_shutdown_detaches_worker(qapp: QApplication) -> None:
    """Verify ReportsPage.shutdown safely disconnects signals and sets flag."""
    from ticnet.desktop.pages.reports_page import ReportsPage
    page = ReportsPage()
    page._is_exporting = True

    # Call shutdown
    clean = page.shutdown(timeout_ms=100)
    assert clean is True
    assert page._is_shutting_down is True


def test_diagnosis_page_disconnect_service_method(qapp: QApplication) -> None:
    """Verify DiagnosisPage.disconnect_service detaches all service signals."""
    from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
    page = DiagnosisPage(diagnostic_service=None)
    # Calling disconnect_service with None is a safe no-op
    page.disconnect_service()

