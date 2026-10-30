"""Main desktop application window for TicNet (PySide6).

Provides the primary application window shell, left navigation rail,
stacked pages container, status bar, and safe window lifecycle management
integrated with the backend service adapter layer.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QByteArray, QEvent, QObject, QSize, Qt
from PySide6.QtGui import QCloseEvent, QColor, QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.pages.about_page import AboutPage
from ticnet.desktop.pages.diagnosis_page import DiagnosisPage
from ticnet.desktop.pages.history_page import HistoryPage
from ticnet.desktop.pages.network_monitoring_page import NetworkMonitoringPage
from ticnet.desktop.pages.overview_page import OverviewPage
from ticnet.desktop.pages.reports_page import ReportsPage
from ticnet.desktop.pages.results_page import ResultsPage
from ticnet.desktop.pages.settings_page import SettingsPage
from ticnet.desktop.services import DesktopServices
from ticnet.desktop.theme import ThemeTokens


_SIDEBAR_ICONS_SVG: dict[str, str] = {
    "menu": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <line x1='4' y1='7' x2='20' y2='7'/>
        <line x1='4' y1='12' x2='20' y2='12'/>
        <line x1='4' y1='17' x2='20' y2='17'/>
    </svg>""",
    "overview": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <rect x='3' y='3' width='7' height='9' rx='1.5'/>
        <rect x='14' y='3' width='7' height='5' rx='1.5'/>
        <rect x='14' y='12' width='7' height='9' rx='1.5'/>
        <rect x='3' y='16' width='7' height='5' rx='1.5'/>
    </svg>""",
    "diagnosis": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='22 12 18 12 15 20 9 4 6 12 2 12'/>
    </svg>""",
    "results": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <line x1='18' y1='20' x2='18' y2='10'/>
        <line x1='12' y1='20' x2='12' y2='4'/>
        <line x1='6' y1='20' x2='6' y2='14'/>
    </svg>""",
    "monitoring": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='18' cy='5' r='3'/>
        <circle cx='6' cy='12' r='3'/>
        <circle cx='18' cy='19' r='3'/>
        <line x1='8.59' y1='13.51' x2='15.42' y2='17.49'/>
        <line x1='15.41' y1='6.51' x2='8.59' y2='10.49'/>
    </svg>""",
    "history": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8'/>
        <polyline points='3 3 3 8 8 8'/>
        <polyline points='12 7 12 12 15 14'/>
    </svg>""",
    "reports": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/>
        <polyline points='14 2 14 8 20 8'/>
        <line x1='16' y1='13' x2='8' y2='13'/>
        <line x1='16' y1='17' x2='8' y2='17'/>
        <line x1='10' y1='9' x2='8' y2='9'/>
    </svg>""",
    "settings": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='3'/>
        <path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/>
    </svg>""",
    "about": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='9'/>
        <line x1='12' y1='16' x2='12' y2='11'/>
        <line x1='12' y1='8' x2='12.01' y2='8'/>
    </svg>""",
    "close": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='15 18 9 12 15 6'/>
    </svg>""",
}


def _render_svg_icon(name: str, color: str = "#52525b", size: int = 20) -> QIcon:
    """Render crisp SVG vector icon into QIcon avoiding platform font and emoji fallback quirks."""
    svg_template = _SIDEBAR_ICONS_SVG.get(name) or _SIDEBAR_ICONS_SVG.get("menu", "")
    svg_data = svg_template.replace("COLOR", color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_data))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    """Main application window for TicNet native desktop."""

    def __init__(
        self,
        services: Optional[DesktopServices] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TicNet — Explainable Network Diagnostics")
        self.resize(1280, 750)
        self.setMinimumSize(1024, 700)

        self.services = services
        self._is_diagnosing: bool = False
        self._is_shutting_down: bool = False

        self._setup_ui()

        if self.services:
            self._bind_services()

    def _setup_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Left Navigation Rail
        sidebar = self._create_sidebar()
        root_layout.addWidget(sidebar, 0)

        # 2. Main Page Stack Container
        self.page_stack = QStackedWidget(central_widget)
        self.page_stack.installEventFilter(self)
        self._init_pages()
        root_layout.addWidget(self.page_stack, 1)

        # 3. Persistent Status Bar
        self._setup_status_bar()

        # Default to Overview (Index 0)
        self.switch_page(0)

    def _create_sidebar(self) -> QWidget:
        self.sidebar_container = QWidget()
        self.sidebar_container.setObjectName("sidebarContainer")
        self.is_sidebar_expanded = False
        self.sidebar_container.setFixedWidth(68)

        layout = QHBoxLayout(self.sidebar_container)
        layout.setContentsMargins(8, 12, 4, 12)
        layout.setSpacing(0)

        # -------------------------------------------------------------
        # 1. COLLAPSED CAPSULE RAIL (Reference Image 1, far left)
        # -------------------------------------------------------------
        self.capsule_rail = QFrame(self.sidebar_container)
        self.capsule_rail.setObjectName("capsuleRail")
        self.capsule_rail.setFixedWidth(52)
        self.capsule_rail.setStyleSheet("""
            #capsuleRail {
                background-color: #ffffff;
                border: 1px solid #e4e4e7;
                border-radius: 26px;
            }
        """)

        rail_layout = QVBoxLayout(self.capsule_rail)
        rail_layout.setContentsMargins(5, 8, 5, 12)
        rail_layout.setSpacing(6)

        # Menu Launcher / Expand Button
        self.btn_sparkle = QPushButton(self.capsule_rail)
        self.btn_sparkle.setIcon(_render_svg_icon("menu", "#18181b", 20))
        self.btn_sparkle.setIconSize(QSize(20, 20))
        self.btn_sparkle.setToolTip("Open Navigation Menu")
        self.btn_sparkle.setFixedSize(40, 40)
        self.btn_sparkle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sparkle.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 20px;
            }
            QPushButton:hover {
                background-color: #f4f4f5;
            }
        """)
        self.btn_sparkle.clicked.connect(self.toggle_sidebar_expanded)
        rail_layout.addWidget(self.btn_sparkle, 0, Qt.AlignmentFlag.AlignCenter)
        rail_layout.addSpacing(4)

        nav_items = [
            ("Overview", 0, "overview", "Overview & KPIs"),
            ("Run Diagnosis", 1, "diagnosis", "Live Diagnostic Pipeline"),
            ("Results & Evidence", 2, "results", "Empirical Findings & Trace"),
            ("Network Monitoring", 3, "monitoring", "Scheduled Network Tests"),
            ("History", 4, "history", "Historical Diagnostic Runs"),
            ("Reports & Export", 5, "reports", "Reports & Evidence Export"),
            ("Settings", 6, "settings", "Configuration & Thresholds"),
            ("About", 7, "about", "About TicNet 1.0"),
        ]
        self.nav_items = nav_items

        self.rail_buttons: List[QPushButton] = []
        for label, idx, icon_name, tooltip in nav_items:
            if idx == 6:
                # Vertical divider with subtle line (matching Ref Image 1)
                sep = QFrame(self.capsule_rail)
                sep.setFixedSize(2, 28)
                sep.setStyleSheet("background-color: #e4e4e7; border-radius: 1px;")
                rail_layout.addWidget(sep, 0, Qt.AlignmentFlag.AlignCenter)
                rail_layout.addSpacing(2)

            btn = QPushButton(self.capsule_rail)
            btn.setIconSize(QSize(20, 20))
            btn.setToolTip(f"{label} ({tooltip})")
            btn.setFixedSize(40, 40)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, i=idx: self.switch_page(i))
            rail_layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
            self.rail_buttons.append(btn)

        rail_layout.addStretch()

        # -------------------------------------------------------------
        # 2. EXPANDED FLOATING DRAWER (Reference Image 2 & 1)
        # -------------------------------------------------------------
        self.drawer_card = QFrame(self.sidebar_container)
        self.drawer_card.setObjectName("drawerCard")
        self.drawer_card.setFixedWidth(224)
        self.drawer_card.setStyleSheet("""
            #drawerCard {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0.3,
                    stop:0 #ffffff, stop:0.75 #ffffff, stop:1.0 #f5f3ff);
                border: 1px solid #e4e4e7;
                border-radius: 24px;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self.drawer_card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 18))
        self.drawer_card.setGraphicsEffect(shadow)

        drawer_layout = QVBoxLayout(self.drawer_card)
        drawer_layout.setContentsMargins(14, 16, 14, 14)
        drawer_layout.setSpacing(6)

        # Header Row: "Menu" and "‹" Back Button (Image 2 & Image 1)
        header_row = QWidget(self.drawer_card)
        h_layout = QHBoxLayout(header_row)
        h_layout.setContentsMargins(0, 0, 0, 6)
        h_layout.setSpacing(6)

        self.btn_drawer_menu = QPushButton("  Menu", header_row)
        self.btn_drawer_menu.setIcon(_render_svg_icon("menu", "#18181b", 18))
        self.btn_drawer_menu.setIconSize(QSize(18, 18))
        self.btn_drawer_menu.setToolTip("Collapse Navigation Menu")
        self.btn_drawer_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drawer_menu.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                font-size: 17px;
                font-weight: 800;
                color: #18181b;
                letter-spacing: -0.3px;
                text-align: left;
                padding: 4px 6px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #f4f4f5;
            }
        """)
        self.btn_drawer_menu.clicked.connect(self.toggle_sidebar_expanded)
        h_layout.addWidget(self.btn_drawer_menu)
        h_layout.addStretch()

        self.btn_back = QPushButton(header_row)
        self.btn_back.setIcon(_render_svg_icon("close", "#71717a", 16))
        self.btn_back.setIconSize(QSize(16, 16))
        self.btn_back.setToolTip("Collapse Menu (Esc)")
        self.btn_back.setFixedSize(30, 30)
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setStyleSheet("""
            QPushButton {
                background-color: #f4f4f5;
                border: 1px solid #e4e4e7;
                border-radius: 15px;
            }
            QPushButton:hover {
                background-color: #e4e4e7;
            }
        """)
        self.btn_back.clicked.connect(self.toggle_sidebar_expanded)
        h_layout.addWidget(self.btn_back)

        drawer_layout.addWidget(header_row)

        # Navigation Buttons Group
        self.nav_button_group = QButtonGroup(self)
        self.nav_button_group.setExclusive(True)

        self.nav_buttons: List[QPushButton] = []
        for label, idx, icon_name, _ in nav_items:
            if idx == 6:
                # Divider before settings
                div = QFrame(self.drawer_card)
                div.setFixedHeight(1)
                div.setStyleSheet("background-color: #f1f1f4; margin: 4px 6px;")
                drawer_layout.addWidget(div)

            btn = QPushButton(f"  {label}", self.drawer_card)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(38)
            btn.setIconSize(QSize(18, 18))
            self.nav_button_group.addButton(btn, idx)
            drawer_layout.addWidget(btn)
            self.nav_buttons.append(btn)

        self.nav_button_group.idClicked.connect(self.switch_page)

        drawer_layout.addStretch()

        # Bottom Black Pill Button (matching Ref Image 2 "More Filters")
        self.btn_run_pill = QPushButton("New Diagnostic Run", self.drawer_card)
        self.btn_run_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_pill.setStyleSheet("""
            QPushButton {
                background-color: #18181b;
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                padding: 10px 16px;
                border: none;
                border-radius: 20px;
            }
            QPushButton:hover {
                background-color: #27272a;
            }
            QPushButton:pressed {
                background-color: #09090b;
            }
        """)
        self.btn_run_pill.clicked.connect(lambda: self.switch_page(1))
        drawer_layout.addWidget(self.btn_run_pill)

        # Version text at bottom
        lbl_version = QLabel("TicNet v1.0 • Diagnostic Suite", self.drawer_card)
        lbl_version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_version.setStyleSheet("font-size: 10px; color: #a1a1aa; padding-top: 4px;")
        drawer_layout.addWidget(lbl_version)

        layout.addWidget(self.capsule_rail)
        layout.addWidget(self.drawer_card)

        # Initialize to Collapsed state (Reference Image 1 left)
        self.set_sidebar_expanded(False)

        return self.sidebar_container

    def set_sidebar_expanded(self, expanded: bool) -> None:
        """Switch between Collapsed Capsule Rail (Image 1) and Floating Drawer (Image 2)."""
        self.is_sidebar_expanded = expanded
        if expanded:
            self.capsule_rail.setVisible(False)
            self.drawer_card.setVisible(True)
            self.sidebar_container.setFixedWidth(240)
        else:
            self.capsule_rail.setVisible(True)
            self.drawer_card.setVisible(False)
            self.sidebar_container.setFixedWidth(68)
        self._sync_sidebar_styles(self.page_stack.currentIndex() if hasattr(self, "page_stack") else 0)

    def toggle_sidebar_expanded(self) -> None:
        """Toggle sidebar between capsule rail and floating drawer."""
        self.set_sidebar_expanded(not self.is_sidebar_expanded)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Handle click outside expanded sidebar to collapse."""
        if watched == self.page_stack and event.type() == QEvent.Type.MouseButtonPress:
            if self.is_sidebar_expanded:
                self.set_sidebar_expanded(False)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle Esc key to collapse expanded sidebar."""
        if event.key() == Qt.Key.Key_Escape and self.is_sidebar_expanded:
            self.set_sidebar_expanded(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def _sync_sidebar_styles(self, active_index: int) -> None:
        """Synchronize active indicators between capsule rail and expanded drawer."""
        if (
            not hasattr(self, "rail_buttons")
            or not hasattr(self, "nav_buttons")
            or not hasattr(self, "nav_items")
        ):
            return

        # 1. Update Collapsed Rail Buttons
        for idx, btn in enumerate(self.rail_buttons):
            is_active = (idx == active_index)
            btn.setChecked(is_active)
            icon_name = self.nav_items[idx][2]
            if is_active:
                btn.setIcon(_render_svg_icon(icon_name, "#ffffff", 20))
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #18181b;
                        border: none;
                        border-radius: 14px;
                    }
                """)
            else:
                btn.setIcon(_render_svg_icon(icon_name, "#52525b", 20))
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        border: none;
                        border-radius: 12px;
                    }
                    QPushButton:hover {
                        background-color: #f4f4f5;
                    }
                """)

        # 2. Update Drawer Buttons (Active Dark Pill from Ref Image 1)
        for idx, btn in enumerate(self.nav_buttons):
            is_active = (idx == active_index)
            btn.setChecked(is_active)
            icon_name = self.nav_items[idx][2]
            if is_active:
                btn.setIcon(_render_svg_icon(icon_name, "#ffffff", 18))
                btn.setStyleSheet("""
                    QPushButton#navButton {
                        background-color: #18181b;
                        color: #ffffff;
                        font-size: 13px;
                        font-weight: 700;
                        border: none;
                        border-radius: 18px;
                        padding: 8px 14px;
                        text-align: left;
                    }
                """)
            else:
                btn.setIcon(_render_svg_icon(icon_name, "#52525b", 18))
                btn.setStyleSheet("""
                    QPushButton#navButton {
                        background-color: transparent;
                        color: #3f3f46;
                        font-size: 13px;
                        font-weight: 500;
                        border: none;
                        border-radius: 12px;
                        padding: 8px 14px;
                        text-align: left;
                    }
                    QPushButton#navButton:hover {
                        background-color: #f4f4f5;
                        color: #18181b;
                    }
                """)

    def _init_pages(self) -> None:
        """Instantiate and register the 8 core page shells."""
        hist_svc = self.services.history if self.services else None
        diag_svc = self.services.diagnostic if self.services else None
        find_svc = self.services.findings if self.services else None
        rep_svc = self.services.reporting if self.services else None
        sett_svc = self.services.settings if self.services else None
        telem_svc = self.services.telemetry if self.services else None
        mon_svc = self.services.monitoring if self.services else None

        self.page_overview = OverviewPage(
            history_service=hist_svc,
            telemetry_service=telem_svc,
            on_navigate_to_diagnosis=lambda: self.switch_page(1),
            on_view_results=self.show_results_for_run,
            parent=self.page_stack,
        )
        self.page_diagnosis = DiagnosisPage(
            diagnostic_service=diag_svc,
            on_view_results=self.show_results_for_run,
            parent=self.page_stack,
        )
        self.page_results = ResultsPage(
            findings_service=find_svc,
            history_service=hist_svc,
            parent=self.page_stack,
        )
        self.page_monitoring = NetworkMonitoringPage(
            monitoring_service=mon_svc,
            telemetry_service=telem_svc,
            parent=self.page_stack,
        )
        self.page_history = HistoryPage(
            history_service=hist_svc,
            on_select_run=self.show_results_for_run,
            parent=self.page_stack,
        )
        self.page_reports = ReportsPage(
            reporting_service=rep_svc,
            history_service=hist_svc,
            parent=self.page_stack,
        )
        self.page_settings = SettingsPage(
            settings_service=sett_svc,
            monitoring_service=mon_svc,
            parent=self.page_stack,
        )
        self.page_about = AboutPage(parent=self.page_stack)

        self.page_stack.addWidget(self.page_overview)    # Index 0
        self.page_stack.addWidget(self.page_diagnosis)   # Index 1
        self.page_stack.addWidget(self.page_results)     # Index 2
        self.page_stack.addWidget(self.page_monitoring)  # Index 3
        self.page_stack.addWidget(self.page_history)     # Index 4
        self.page_stack.addWidget(self.page_reports)     # Index 5
        self.page_stack.addWidget(self.page_settings)    # Index 6
        self.page_stack.addWidget(self.page_about)       # Index 7

    def _setup_status_bar(self) -> None:
        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)

        self.lbl_status_left = QLabel("Ready | Native Windows Desktop Mode", status_bar)
        status_bar.addWidget(self.lbl_status_left, 1)

    def _bind_services(self) -> None:
        """Connect service level signals to global window state and status bar."""
        if not self.services:
            return

        diag = self.services.diagnostic
        diag.sig_run_started.connect(self._on_global_run_started)
        diag.sig_run_finished.connect(self._on_global_run_finished)

    def _on_global_run_started(self, run_id: str) -> None:
        self._is_diagnosing = True
        self.lbl_status_left.setText(f"Diagnosing... Active Run: {run_id} (Background Worker Active)")
        self.lbl_status_left.setStyleSheet(f"color: {ThemeTokens.BRAND_PRIMARY}; font-weight: 600;")

    def _on_global_run_finished(self, run_id: str, status: str, message: str) -> None:
        self._is_diagnosing = False
        self.lbl_status_left.setText(f"Ready | Run {run_id} {status}")
        self.lbl_status_left.setStyleSheet("")

        # Refresh pages with fresh SQLite data
        self.page_history.load_history()
        self.page_overview.refresh_overview()
        self.page_reports.refresh_runs_list()
        self.page_results.load_run_results(run_id)
        if hasattr(self, "page_monitoring"):
            self.page_monitoring.refresh_page()

    def show_results_for_run(self, run_id: str) -> None:
        """Display findings for a specific run and switch to Results page."""
        self.page_results.load_run_results(run_id)
        self.page_reports.set_selected_run(run_id)
        self.switch_page(2)

    def switch_page(self, index: int) -> None:
        """Switch active stacked widget page and synchronize navigation button."""
        if 0 <= index < self.page_stack.count():
            self.page_stack.setCurrentIndex(index)
            btn = self.nav_button_group.button(index)
            if btn and not btn.isChecked():
                btn.setChecked(True)
            self._sync_sidebar_styles(index)

            # Ensure page-level dynamic table column allocations update on page switch
            current_page = self.page_stack.currentWidget()
            if current_page is not None:
                adjust_fn = getattr(current_page, "adjust_column_widths", None)
                if callable(adjust_fn):
                    adjust_fn()
                trace_widget = getattr(current_page, "trace_widget", None)
                if trace_widget is not None:
                    trace_adjust = getattr(trace_widget, "adjust_column_widths", None)
                    if callable(trace_adjust):
                        trace_adjust()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Safe application shutdown handling.

        Ensures active diagnostic sessions and in-flight background report exports
        are cooperatively canceled, awaited, and disconnected before the window is destroyed.
        """
        if self._is_shutting_down:
            event.ignore()
            return

        is_diagnosing = self._is_diagnosing or bool(
            self.services and self.services.diagnostic and self.services.diagnostic.is_running
        )

        if is_diagnosing:
            reply = QMessageBox.question(
                self,
                "Diagnostic Run in Progress",
                "A diagnostic measurement run is currently active.\n\n"
                "Do you want to cancel the active diagnosis and exit TicNet?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

            # Cancel and join worker cleanly if services are available
            if self.services and self.services.diagnostic:
                clean = self.services.diagnostic.shutdown(timeout_s=3.0)
                if not clean:
                    warn_reply = QMessageBox.warning(
                        self,
                        "Worker Termination Timeout",
                        "The background diagnostic worker could not be stopped cleanly within 3.0 seconds.\n\n"
                        "Forcing exit may interrupt database writes. Do you wish to force exit now?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if warn_reply == QMessageBox.StandardButton.No:
                        event.ignore()
                        return

        # Check for in-flight background report export
        if hasattr(self, "page_reports") and getattr(self.page_reports, "is_exporting", False):
            export_reply = QMessageBox.question(
                self,
                "Report Export in Progress",
                "A background report export is currently in progress.\n\n"
                "Exiting now will cancel the file write. Do you want to exit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if export_reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

            # Await or cleanly detach export worker before UI destruction
            if hasattr(self.page_reports, "shutdown"):
                self.page_reports.shutdown(timeout_ms=3000)

        # Mark shutdown in progress to protect against repeated or re-entrant calls
        self._is_shutting_down = True

        # Disconnect service-level signals to prevent delivering events to destroyed widgets
        if hasattr(self, "page_diagnosis") and hasattr(self.page_diagnosis, "disconnect_service"):
            self.page_diagnosis.disconnect_service()

        if self.services and self.services.diagnostic:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    self.services.diagnostic.sig_run_started.disconnect(self._on_global_run_started)
                except Exception:
                    pass
                try:
                    self.services.diagnostic.sig_run_finished.disconnect(self._on_global_run_finished)
                except Exception:
                    pass

        # Stop background live network telemetry worker cleanly
        if hasattr(self, "page_overview") and hasattr(self.page_overview, "closeEvent"):
            try:
                self.page_overview.closeEvent(event)
            except Exception:
                pass
        if self.services and getattr(self.services, "telemetry", None):
            try:
                self.services.telemetry.stop()
            except Exception:
                pass

        # Stop background monitoring service cleanly
        if self.services and getattr(self.services, "monitoring", None):
            try:
                self.services.monitoring.shutdown()
            except Exception:
                pass

        # Safe shutdown allowed
        event.accept()
