"""Desktop page shells for TicNet presentation layer."""

from __future__ import annotations

from ticnet.desktop.pages.about_page import AboutPage
from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.pages.history_page import HistoryPage
from ticnet.desktop.pages.network_monitoring_page import NetworkMonitoringPage
from ticnet.desktop.pages.overview_page import OverviewPage
from ticnet.desktop.pages.reports_page import ReportsPage
from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.pages.settings_page import SettingsPage

__all__ = [
    "BasePage",
    "OverviewPage",
    "DiagnosisPage",
    "ResultsPage",
    "NetworkMonitoringPage",
    "HistoryPage",
    "ReportsPage",
    "SettingsPage",
    "AboutPage",
]
