"""Unit tests for TicNet Native Windows Desktop MainWindow and Navigation (Stage 1).

Verifies:
- MainWindow initialization with genuine Windows desktop layout
- Navigation rail switching across all 7 page shells
- Honest empty states rendering without synthetic/fake telemetry
- Page transitions from Overview to Diagnosis
- Safe window close handling when idle
- Theme and stylesheet validity
"""

from __future__ import annotations

import os
import sys

# Ensure offscreen rendering for headless test execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6 import QtCore
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ticnet.desktop.app import create_application
from ticnet.desktop.main_window import MainWindow
from ticnet.desktop.pages.about_page import AboutPage
from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.pages.history_page import HistoryPage
from ticnet.desktop.pages.network_monitoring_page import NetworkMonitoringPage
from ticnet.desktop.pages.overview_page import OverviewPage
from ticnet.desktop.pages.reports_page import ReportsPage
from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.pages.settings_page import SettingsPage
from ticnet.desktop.theme import ThemeTokens, get_application_stylesheet


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provide a persistent QApplication instance for headless Qt testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def main_window(qapp: QApplication) -> MainWindow:
    """Create a fresh MainWindow instance for each test."""
    window = MainWindow()
    yield window
    window._is_diagnosing = False
    window.close()


def test_main_window_initialization(main_window: MainWindow) -> None:
    """Verify MainWindow initializes with expected title, dimensions, and pages."""
    assert "TicNet" in main_window.windowTitle()
    assert main_window.minimumWidth() == 1024
    assert main_window.minimumHeight() == 700

    # Verify 8 page shells exist in stacked container
    assert main_window.page_stack.count() == 8
    assert main_window.page_stack.currentIndex() == 0  # Default to Overview

    # Verify navigation buttons count matches pages count
    assert len(main_window.nav_buttons) == 8

    # Verify status bar initialized
    status_bar = main_window.statusBar()
    assert status_bar is not None
    assert "Ready" in main_window.lbl_status_left.text()


def test_navigation_rail_page_switching(main_window: MainWindow) -> None:
    """Verify navigation buttons correctly activate all 8 page shells."""
    expected_pages = [
        (0, OverviewPage, "Overview"),
        (1, DiagnosisPage, "Run Diagnosis"),
        (2, ResultsPage, "Results & Evidence"),
        (3, NetworkMonitoringPage, "Network Monitoring"),
        (4, HistoryPage, "History"),
        (5, ReportsPage, "Reports & Export"),
        (6, SettingsPage, "Settings"),
        (7, AboutPage, "About"),
    ]

    for index, expected_cls, expected_label in expected_pages:
        # Click navigation button
        btn = main_window.nav_button_group.button(index)
        assert btn is not None
        assert expected_label in btn.text()

        # Simulate user click
        QTest.mouseClick(btn, QtCore.Qt.MouseButton.LeftButton)

        # Verify active page index and class
        assert main_window.page_stack.currentIndex() == index
        current_widget = main_window.page_stack.currentWidget()
        assert isinstance(current_widget, expected_cls)
        assert btn.isChecked() is True


def test_overview_navigation_button(main_window: MainWindow) -> None:
    """Verify 'Go to Diagnosis' CTA on Overview page switches to Diagnosis page."""
    main_window.switch_page(0)
    assert main_window.page_stack.currentIndex() == 0

    overview_page: OverviewPage = main_window.page_overview
    assert overview_page.btn_start is not None

    QTest.mouseClick(overview_page.btn_start, QtCore.Qt.MouseButton.LeftButton)
    assert main_window.page_stack.currentIndex() == 1
    assert isinstance(main_window.page_stack.currentWidget(), DiagnosisPage)


def test_honest_empty_states_rendered_without_fake_telemetry(main_window: MainWindow) -> None:
    """Verify empty states are rendered honestly with zero fabricated metrics."""
    from PySide6.QtWidgets import QLabel

    # 1. Overview Page: empty runs state
    overview: OverviewPage = main_window.page_overview
    assert overview.lbl_title.text() == "Overview"
    overview_texts = [lbl.text() for lbl in overview.findChildren(QLabel)]
    assert any("No Diagnostic Runs Recorded" in t for t in overview_texts)

    # 2. Diagnosis Page: controls present, Cancel disabled, no idle card banner
    diag: DiagnosisPage = main_window.page_diagnosis
    assert diag.btn_start.isEnabled() is True
    assert diag.btn_cancel.isEnabled() is False  # Idle: cancel disabled
    diag_texts = [lbl.text() for lbl in diag.findChildren(QLabel)]
    assert not any("Diagnostic Engine Idle" in t for t in diag_texts)

    # 3. Results Page: empty findings state
    results: ResultsPage = main_window.page_results
    assert results.lbl_title.text() == "Results & Evidence"
    results_texts = [lbl.text() for lbl in results.findChildren(QLabel)]
    assert any("No Diagnostic Findings Available" in t for t in results_texts)

    # 4. Network Monitoring Page: empty history state and honest metrics
    monitoring: NetworkMonitoringPage = main_window.page_monitoring
    assert monitoring.lbl_title.text() == "Network Monitoring"
    monitoring_texts = [lbl.text() for lbl in monitoring.findChildren(QLabel)]
    assert any("NOT CONFIGURED" in t for t in monitoring_texts)
    assert any("No Monitoring History Recorded" in t for t in monitoring_texts)

    # 5. History Page: empty history state
    history: HistoryPage = main_window.page_history
    assert history.lbl_title.text() == "History"
    assert history.txt_search.placeholderText() != ""
    history_texts = [lbl.text() for lbl in history.findChildren(QLabel)]
    assert any("Diagnostic History Is Empty" in t for t in history_texts)

    # 6. Reports Page: export button disabled when no session selected
    reports: ReportsPage = main_window.page_reports
    assert reports.btn_export.isEnabled() is False
    assert reports.chk_privacy.isChecked() is True  # Privacy checked by default
    assert reports.combo_format.count() == 3
    assert reports.preview_card.isVisible() is False
    reports_texts = [lbl.text() for lbl in reports.findChildren(QLabel)]
    assert any("Data Safety & Privacy Guarantees" in t for t in reports_texts)

    # 7. Settings Page: parameter labels present
    settings: SettingsPage = main_window.page_settings
    assert settings.lbl_title.text() == "Settings"
    settings_texts = [lbl.text() for lbl in settings.findChildren(QLabel)]
    assert any("Gateway Probe Count" in t for t in settings_texts)

    # 8. About Page: attribution details present
    about: AboutPage = main_window.page_about
    about_texts = [lbl.text() for lbl in about.findChildren(QLabel)]
    assert any("TicNet — Version 0.1.0" in t for t in about_texts)
    assert any("Sudip Barik" in t for t in about_texts)


def test_safe_close_event_when_idle(main_window: MainWindow) -> None:
    """Verify window accepts close event immediately when no diagnostic run is active."""
    assert main_window._is_diagnosing is False

    event = QCloseEvent()
    main_window.closeEvent(event)
    assert event.isAccepted() is True


def test_close_event_confirmation_when_diagnosing(main_window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify modal confirmation prompt is displayed when active diagnosis is running."""
    from PySide6.QtWidgets import QMessageBox

    main_window._is_diagnosing = True

    try:
        # 1. User selects "No" -> close event is rejected
        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
        event_no = QCloseEvent()
        main_window.closeEvent(event_no)
        assert event_no.isAccepted() is False

        # 2. User selects "Yes" -> close event is accepted
        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
        event_yes = QCloseEvent()
        main_window.closeEvent(event_yes)
        assert event_yes.isAccepted() is True
    finally:
        main_window._is_diagnosing = False


def test_switch_page_out_of_bounds(main_window: MainWindow) -> None:
    """Verify switch_page safely ignores invalid out-of-bounds page indices."""
    main_window.switch_page(0)
    assert main_window.page_stack.currentIndex() == 0

    # Negative index
    main_window.switch_page(-1)
    assert main_window.page_stack.currentIndex() == 0

    # Beyond upper bound
    main_window.switch_page(99)
    assert main_window.page_stack.currentIndex() == 0


def test_create_application_factory(qapp: QApplication) -> None:
    """Verify create_application configures QApplication and MainWindow correctly."""
    app, win = create_application()
    assert app is not None
    assert win is not None
    assert app.applicationName() == "TicNet"
    assert app.organizationName() == "TicNet"
    win.close()


def test_theme_and_stylesheet_generation() -> None:
    """Verify stylesheet contains core styling tokens and rules."""
    qss = get_application_stylesheet()
    assert isinstance(qss, str)
    assert len(qss) > 200
    assert "QMainWindow" in qss
    assert "#sidebarContainer" in qss
    assert "#primaryButton" in qss
    assert "#dangerButton" in qss
    assert ThemeTokens.BG_WINDOW in qss
    assert ThemeTokens.BRAND_PRIMARY in qss
