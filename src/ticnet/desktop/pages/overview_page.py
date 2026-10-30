"""Overview page for TicNet desktop presentation layer.

Redesigned executive network-diagnostics dashboard featuring:
- Monochromatic 3D Earth globe that rotates automatically, responds to cursor hover
  and click-and-drag interaction, and displays ping latency callouts.
- Live Network Status panel with a continuously moving Global Ping graph animation.
- Executive Network Health & Telemetry Summary with four high-contrast KPI cards.
- Two-Checkpoint Connectivity Isolation comparison (Local Gateway vs External Internet).
- Most Recent Diagnostic Run card and honest empty state when no runs are recorded.
- Interactive Quick Actions panel connecting to diagnostic workflows.
- Professional modern typography, generous spacing, and pristine alignment.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from ticnet.core.timezone import format_ist
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.dialogs.overview_walkthrough_dialog import OverviewWalkthroughDialog
from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.services.history_service import HistoricalRunDetail, HistoryService
from ticnet.desktop.services.live_telemetry_service import LiveTelemetryService
from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.global_ping_graph_widget import GlobalPingGraphWidget
from ticnet.desktop.widgets.interactive_globe_widget import InteractiveGlobeWidget
from ticnet.desktop.widgets.run_id_badge import RunIdBadge
from ticnet.models.enums import FindingPriority


# ---------------------------------------------------------------------------
# Vector Icons for Dashboard Cards & Controls
# ---------------------------------------------------------------------------
_SVG_ICONS = {
    "search": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='11' cy='11' r='8'/>
        <line x1='21' y1='21' x2='16.65' y2='16.65'/>
    </svg>""",
    "bell": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9'/>
        <path d='M13.73 21a2 2 0 0 1-3.46 0'/>
    </svg>""",
    "pulse": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='22 12 18 12 15 21 9 3 6 12 2 12'/>
    </svg>""",
    "wifi": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M5 12.55a11 11 0 0 1 14.08 0'/>
        <path d='M1.42 9a16 16 0 0 1 21.16 0'/>
        <path d='M8.53 16.11a6 6 0 0 1 6.95 0'/>
        <line x1='12' y1='20' x2='12.01' y2='20'/>
    </svg>""",
    "cellular": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <line x1='18' y1='20' x2='18' y2='4'/>
        <line x1='12' y1='20' x2='12' y2='10'/>
        <line x1='6' y1='20' x2='6' y2='16'/>
    </svg>""",
    "router": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <rect x='2' y='14' width='20' height='8' rx='2'/>
        <path d='M6 14V8a3 3 0 0 1 6 0v6'/>
        <line x1='18' y1='8' x2='18.01' y2='8'/>
        <line x1='14' y1='8' x2='14.01' y2='8'/>
    </svg>""",
    "satellite": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M13 2 L3 12 L7 16 L17 6 Z'/>
        <line x1='13' y1='2' x2='22' y2='11'/>
        <line x1='2' y1='13' x2='11' y2='22'/>
    </svg>""",
    "globe": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='10'/>
        <line x1='2' y1='12' x2='22' y2='12'/>
        <path d='M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'/>
    </svg>""",
    "cloud": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z'/>
    </svg>""",
    "target": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='10'/>
        <circle cx='12' cy='12' r='6'/>
        <circle cx='12' cy='12' r='2'/>
    </svg>""",
    "chart": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <line x1='18' y1='20' x2='18' y2='10'/>
        <line x1='12' y1='20' x2='12' y2='4'/>
        <line x1='6' y1='20' x2='6' y2='14'/>
    </svg>""",
    "shield": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'/>
        <polyline points='9 12 11 14 15 10'/>
    </svg>""",
    "bolt": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <polygon points='13 2 3 14 12 14 11 22 21 10 12 10 13 2'/>
    </svg>""",
    "map": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <polygon points='1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6'/>
        <line x1='8' y1='2' x2='8' y2='18'/>
        <line x1='16' y1='6' x2='16' y2='22'/>
    </svg>""",
    "report": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/>
        <polyline points='14 2 14 8 20 8'/>
        <line x1='16' y1='13' x2='8' y2='13'/>
        <line x1='16' y1='17' x2='8' y2='17'/>
    </svg>""",
    "settings": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='3'/>
        <path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/>
    </svg>""",
    "chevron_right": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='9 18 15 12 9 6'/>
    </svg>""",
}


def _render_pixmap_icon(name: str, color: str = "#64748b", size: int = 18) -> QPixmap:
    """Render crisp antialiased SVG icon into a QPixmap."""
    svg_template = _SVG_ICONS.get(name, _SVG_ICONS["pulse"])
    svg_data = svg_template.replace("COLOR", color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_data))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pixmap


class OverviewPage(BasePage):
    """Redesigned Overview page presenting 3D interactive globe and live network telemetry."""

    def __init__(
        self,
        history_service: Optional[HistoryService] = None,
        telemetry_service: Optional[LiveTelemetryService] = None,
        on_navigate_to_diagnosis: Optional[Callable[[], None]] = None,
        on_view_results: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="Overview",
            subtitle="Explainable Home and Last-Mile Connectivity Diagnosis",
            parent=parent,
        )
        self.history_service = history_service
        self.telemetry_service = telemetry_service
        self.on_navigate_to_diagnosis = on_navigate_to_diagnosis
        self.on_view_results = on_view_results
        self._latest_run_id: Optional[str] = None

        # Build modernized page structure
        self._populate_redesigned_content()

        if self.history_service:
            self.refresh_overview()

    def _populate_redesigned_content(self) -> None:
        """Construct the redesigned dashboard matching the visual reference."""
        # Hide default BasePage header title banner to display sleek top search & user status bar
        header_container = self.findChild(QWidget, "headerContainer")
        if header_container is not None:
            header_container.setVisible(False)

        # -------------------------------------------------------------------
        # 1. Top Header Bar (Search by IP/device, System Online, Profile)
        # -------------------------------------------------------------------
        top_bar = self._create_top_header_bar()
        self.content_layout.addWidget(top_bar)

        # -------------------------------------------------------------------
        # 2. Main Hero Section (Hero Card with 3D Globe + Live Network Status)
        # -------------------------------------------------------------------
        hero_and_status_row = QWidget()
        hs_layout = QHBoxLayout(hero_and_status_row)
        hs_layout.setContentsMargins(0, 0, 0, 0)
        hs_layout.setSpacing(16)

        # 2A. Hero Card (Spans left ~64% width)
        hero_card = self._create_hero_card()
        hs_layout.addWidget(hero_card, 64)

        # 2B. Live Network Speed Telemetry Card (Spans right ~36% width)
        status_card = self._create_live_network_status_card()
        hs_layout.addWidget(status_card, 36)

        self.content_layout.addWidget(hero_and_status_row)

        # -------------------------------------------------------------------
        # 3. Network Health & Telemetry Summary (4 Metric Cards Grid)
        # -------------------------------------------------------------------
        self.kpi_grid_card = self._create_telemetry_summary_section()
        self.content_layout.addWidget(self.kpi_grid_card)

        # -------------------------------------------------------------------
        # 4. Lower Diagnostics Section (Two-Checkpoint, Recent Run, Quick Actions)
        # -------------------------------------------------------------------
        lower_row = QWidget()
        lower_layout = QHBoxLayout(lower_row)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(16)

        # Left Column: Checkpoint Panel + Recent Run Card / Empty State
        left_col = QWidget(lower_row)
        lc_layout = QVBoxLayout(left_col)
        lc_layout.setContentsMargins(0, 0, 0, 0)
        lc_layout.setSpacing(14)

        # Two-Checkpoint Connectivity Isolation Card
        self.checkpoint_panel = self._create_checkpoint_panel()
        lc_layout.addWidget(self.checkpoint_panel)

        # Most Recent Diagnostic Run Card (Dynamic)
        self.latest_run_card = self._create_latest_run_card()
        lc_layout.addWidget(self.latest_run_card)

        # Honest Empty State: Latest Run (Required for tests and zero-telemetry honesty)
        self.empty_state = self.create_empty_state(
            title="No Diagnostic Runs Recorded",
            message=(
                "No previous diagnostic assessments were found in the local SQLite database. "
                "Run your first diagnostic assessment to evaluate gateway reachability, "
                "external packet loss, DNS health, and bufferbloat."
            ),
        )
        lc_layout.addWidget(self.empty_state)

        lower_layout.addWidget(left_col, 72)

        # Right Column: Quick Actions Card
        quick_actions_card = self._create_quick_actions_card()
        lower_layout.addWidget(quick_actions_card, 28)

        self.content_layout.addWidget(lower_row)

    # -----------------------------------------------------------------------
    # Section Creators
    # -----------------------------------------------------------------------
    def _create_top_header_bar(self) -> QWidget:
        """Create sleek top navigation header bar with search and user controls."""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 2, 0, 4)
        layout.setSpacing(14)

        # Search box with magnifying glass icon
        search_box = QFrame(bar)
        search_box.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 20px;
                padding: 2px 12px;
            }
            QFrame:hover {
                border-color: #94a3b8;
            }
        """)
        search_layout = QHBoxLayout(search_box)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(8)

        lbl_search_icon = QLabel(search_box)
        lbl_search_icon.setPixmap(_render_pixmap_icon("search", "#64748b", 16))
        search_layout.addWidget(lbl_search_icon)

        self.txt_top_search = QLineEdit(search_box)
        self.txt_top_search.setPlaceholderText("Search by IP, location or device...")
        self.txt_top_search.setStyleSheet("""
            QLineEdit {
                border: none;
                background-color: transparent;
                font-size: 13px;
                color: #0f172a;
                padding: 6px 0px;
            }
        """)
        search_layout.addWidget(self.txt_top_search)
        layout.addWidget(search_box, 1)

        # Right Controls: System Online pill, Notification bell, User profile
        right_controls = QWidget(bar)
        rc_layout = QHBoxLayout(right_controls)
        rc_layout.setContentsMargins(0, 0, 0, 0)
        rc_layout.setSpacing(12)

        # "● System Online" pill badge
        lbl_online = QLabel("●  System Online", right_controls)
        lbl_online.setStyleSheet("""
            QLabel {
                background-color: #ecfdf5;
                color: #059669;
                font-size: 13px;
                font-weight: 700;
                padding: 6px 14px;
                border: 1px solid #a7f3d0;
                border-radius: 16px;
            }
        """)
        rc_layout.addWidget(lbl_online)

        # Bell Notification Button
        btn_bell = QPushButton(right_controls)
        btn_bell.setIcon(QIcon(_render_pixmap_icon("bell", "#475569", 18)))
        btn_bell.setIconSize(QSize(18, 18))
        btn_bell.setFixedSize(36, 36)
        btn_bell.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_bell.setToolTip("Notifications")
        btn_bell.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 18px;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
            }
        """)
        rc_layout.addWidget(btn_bell)
        layout.addWidget(right_controls, 0)

        return bar

    def _create_hero_card(self) -> QFrame:
        """Create the main Hero Section card with title, feature groups, and interactive 3D globe."""
        card = QFrame()
        card.setObjectName("heroCard")
        card.setStyleSheet("""
            #heroCard {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 18px;
            }
        """)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 20, 20)
        card_layout.setSpacing(16)

        # Left Column: Headline, Description, Features, Action buttons
        left_widget = QWidget(card)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # Category tag
        lbl_tag = QLabel("CONNECTIVITY DIAGNOSTICS PLATFORM  ›", left_widget)
        lbl_tag.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 800; letter-spacing: 0.8px;")
        left_layout.addWidget(lbl_tag)

        # Main Headline
        lbl_headline = QLabel("From Signals to\nSmarter Decisions", left_widget)
        lbl_headline.setStyleSheet("""
            font-size: 32px;
            font-weight: 800;
            color: #0f172a;
            line-height: 1.15;
            letter-spacing: -0.6px;
        """)
        lbl_headline.setWordWrap(True)
        left_layout.addWidget(lbl_headline)

        # Supporting text (Target 15-16px)
        lbl_desc = QLabel(
            "TicNet helps you understand, diagnose and resolve connectivity issues — "
            "from your home network to the last mile. Real data. Clear insights. Better internet.",
            left_widget,
        )
        lbl_desc.setStyleSheet("font-size: 15px; color: #475569; line-height: 1.45;")
        lbl_desc.setWordWrap(True)
        left_layout.addWidget(lbl_desc)

        left_layout.addSpacing(4)

        # Three Compact Feature Groups
        features_row = QWidget(left_widget)
        f_layout = QHBoxLayout(features_row)
        f_layout.setContentsMargins(0, 0, 0, 0)
        f_layout.setSpacing(12)

        feat_items = [
            ("target", "Detect", "Find what's causing\nconnectivity issues"),
            ("chart", "Analyse", "Get detailed insights\nwith real metrics"),
            ("shield", "Resolve", "Make informed\ndecisions, faster"),
        ]

        for icon_name, title, desc in feat_items:
            f_box = QWidget(features_row)
            fb_layout = QHBoxLayout(f_box)
            fb_layout.setContentsMargins(0, 0, 0, 0)
            fb_layout.setSpacing(8)

            icon_lbl = QLabel(f_box)
            icon_lbl.setPixmap(_render_pixmap_icon(icon_name, "#10b981", 20))
            fb_layout.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

            t_box = QWidget(f_box)
            tb_layout = QVBoxLayout(t_box)
            tb_layout.setContentsMargins(0, 0, 0, 0)
            tb_layout.setSpacing(2)

            lbl_t = QLabel(title, t_box)
            lbl_t.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a;")
            tb_layout.addWidget(lbl_t)

            lbl_d = QLabel(desc, t_box)
            lbl_d.setStyleSheet("font-size: 11px; color: #64748b; line-height: 1.25;")
            lbl_d.setWordWrap(True)
            tb_layout.addWidget(lbl_d)

            fb_layout.addWidget(t_box)
            f_layout.addWidget(f_box)

        left_layout.addWidget(features_row)
        left_layout.addSpacing(6)

        # Action Buttons Row
        btn_row = QWidget(left_widget)
        br_layout = QHBoxLayout(btn_row)
        br_layout.setContentsMargins(0, 0, 0, 0)
        br_layout.setSpacing(12)

        # Primary Button: "Start Diagnosis →"
        self.btn_start = QPushButton("Start Diagnosis  →", btn_row)
        self.btn_start.setObjectName("primaryButton")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #ffffff;
                font-size: 12.5px;
                font-weight: 700;
                padding: 8px 14px;
                border: none;
                border-radius: 16px;
            }
            QPushButton:hover {
                background-color: #1e293b;
            }
            QPushButton:pressed {
                background-color: #334155;
            }
        """)
        if self.on_navigate_to_diagnosis:
            self.btn_start.clicked.connect(self.on_navigate_to_diagnosis)
        br_layout.addWidget(self.btn_start)

        # Secondary Button: "▶ Watch Overview (2 min)"
        self.btn_watch_overview = QPushButton("▶  Watch Overview", btn_row)
        self.btn_watch_overview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_watch_overview.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #1e293b;
                font-size: 12.5px;
                font-weight: 600;
                padding: 9px 14px;
                border: 1px solid #e2e8f0;
                border-radius: 18px;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
            }
        """)
        self.btn_watch_overview.clicked.connect(self._open_watch_overview_dialog)
        br_layout.addWidget(self.btn_watch_overview)
        br_layout.addStretch()

        left_layout.addWidget(btn_row)
        card_layout.addWidget(left_widget, 64)

        # Right Column: The Monochromatic 3D Interactive Globe
        self.globe_widget = InteractiveGlobeWidget(card)
        self.globe_widget.setMinimumSize(190, 190)
        card_layout.addWidget(self.globe_widget, 36)

        return card

    def _create_live_network_status_card(self) -> QFrame:
        """Create the dedicated Network Speed live telemetry card matching real-time network status."""
        card = QFrame()
        card.setObjectName("liveNetworkStatusCard")
        card.setMinimumWidth(320)
        card.setMinimumHeight(340)
        card.setStyleSheet("""
            #liveNetworkStatusCard {
                background-color: transparent;
                border: none;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # Network Speed reference card widget with live telemetry
        self.lbl_global_ping_val = QLabel("30 ms", card)
        self.lbl_global_ping_val.setVisible(False)

        self.ping_graph_widget = GlobalPingGraphWidget(baseline_ping_ms=30.0, parent=card)
        self.ping_graph_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        card_layout.addWidget(self.ping_graph_widget)

        if self.telemetry_service is not None:
            self.ping_graph_widget.set_telemetry_service(self.telemetry_service)
            self.telemetry_service.sig_telemetry_updated.connect(self.ping_graph_widget.update_telemetry_sample)
            self.ping_graph_widget.sig_refresh_requested.connect(self._on_speed_refresh_requested)
            self.telemetry_service.start()

        return card

    def _on_speed_refresh_requested(self) -> None:
        """Handle on-demand network speed test requested from widget."""
        if hasattr(self, "telemetry_service") and self.telemetry_service is not None:
            if hasattr(self.telemetry_service, "trigger_speed_refresh"):
                self.telemetry_service.trigger_speed_refresh()
            elif hasattr(self.telemetry_service, "trigger_speed_test"):
                self.telemetry_service.trigger_speed_test(force=True)

    def _create_telemetry_summary_section(self) -> QFrame:
        """Create the Network Health & Telemetry Summary section with 4 KPI cards."""
        card = QFrame()
        card.setObjectName("kpiSectionCard")
        card.setStyleSheet("""
            #kpiSectionCard {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 18px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)

        # Header row with title and "View Details →" link
        head_row = QWidget(card)
        h_layout = QHBoxLayout(head_row)
        h_layout.setContentsMargins(0, 0, 0, 0)

        title_box = QWidget(head_row)
        tb_layout = QHBoxLayout(title_box)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(8)

        lbl_icon = QLabel(title_box)
        lbl_icon.setPixmap(_render_pixmap_icon("pulse", "#10b981", 18))
        tb_layout.addWidget(lbl_icon)

        lbl_title = QLabel("Network Health & Telemetry Summary", title_box)
        lbl_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #0f172a;")
        tb_layout.addWidget(lbl_title)
        h_layout.addWidget(title_box)
        h_layout.addStretch()

        btn_view_details = QPushButton("View Details →", head_row)
        btn_view_details.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_view_details.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #2563eb;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover {
                color: #1d4ed8;
                text-decoration: underline;
            }
        """)
        btn_view_details.clicked.connect(self._handle_view_latest_clicked)
        h_layout.addWidget(btn_view_details)
        layout.addWidget(head_row)

        # Grid of 4 Metric Cards
        grid_widget = QWidget(card)
        self.grid_layout = QGridLayout(grid_widget)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(12)

        # KPI 1: Overall Network State
        self.card_kpi_overall, self.lbl_kpi_overall_val, self.lbl_kpi_overall_sub = self._create_kpi_box(
            icon_name="pulse",
            title="OVERALL NETWORK STATE",
            default_val="IDLE — READY",
            default_sub="Awaiting diagnostic assessment",
            color=ThemeTokens.COLOR_INFO,
            icon_bg="#eff6ff",
            icon_color="#2563eb",
        )
        self.grid_layout.addWidget(self.card_kpi_overall, 0, 0)

        # KPI 2: Local Gateway Checkpoint
        self.card_kpi_gw, self.lbl_kpi_gw_val, self.lbl_kpi_gw_sub = self._create_kpi_box(
            icon_name="router",
            title="LOCAL GATEWAY CHECKPOINT",
            default_val="--",
            default_sub="Router unprobed",
            color=ThemeTokens.TEXT_MUTED,
            icon_bg="#ecfdf5",
            icon_color="#10b981",
        )
        self.grid_layout.addWidget(self.card_kpi_gw, 0, 1)

        # KPI 3: External Internet Checkpoint
        self.card_kpi_ext, self.lbl_kpi_ext_val, self.lbl_kpi_ext_sub = self._create_kpi_box(
            icon_name="globe",
            title="EXTERNAL INTERNET CHECKPOINT",
            default_val="--",
            default_sub="Multi-targets unprobed",
            color=ThemeTokens.TEXT_MUTED,
            icon_bg="#eff6ff",
            icon_color="#2563eb",
        )
        self.grid_layout.addWidget(self.card_kpi_ext, 0, 2)

        # KPI 4: DNS & Traffic Load
        self.card_kpi_dns, self.lbl_kpi_dns_val, self.lbl_kpi_dns_sub = self._create_kpi_box(
            icon_name="cloud",
            title="DNS & TRAFFIC LOAD",
            default_val="--",
            default_sub="Resolvers & Bufferbloat",
            color=ThemeTokens.TEXT_MUTED,
            icon_bg="#eef2ff",
            icon_color="#4f46e5",
        )
        self.grid_layout.addWidget(self.card_kpi_dns, 0, 3)

        layout.addWidget(grid_widget)
        return card

    def _create_kpi_box(
        self,
        icon_name: str,
        title: str,
        default_val: str,
        default_sub: str,
        color: str,
        icon_bg: str = "#f8fafc",
        icon_color: str = "#64748b",
    ) -> tuple[QFrame, QLabel, QLabel]:
        """Create a styled, readable executive KPI metric card."""
        box = QFrame()
        box.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
            QFrame:hover {
                border-color: #cbd5e1;
                background-color: #f1f5f9;
            }
            QLabel {
                border: none;
                background-color: transparent;
            }
        """)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)

        # Top row: icon and title
        top_row = QWidget(box)
        tr_layout = QHBoxLayout(top_row)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(8)

        lbl_icon = QLabel(top_row)
        lbl_icon.setFixedSize(28, 28)
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_icon.setStyleSheet(f"background-color: {icon_bg}; border-radius: 14px;")
        lbl_icon.setPixmap(_render_pixmap_icon(icon_name, icon_color, 15))
        tr_layout.addWidget(lbl_icon)

        lbl_title = QLabel(title, top_row)
        lbl_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #64748b; letter-spacing: 0.6px;")
        lbl_title.setWordWrap(True)
        tr_layout.addWidget(lbl_title)
        tr_layout.addStretch()
        layout.addWidget(top_row)

        # Metric value (Target 22-26px)
        lbl_val = QLabel(default_val, box)
        lbl_val.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {color}; margin-top: 2px;")
        lbl_val.setWordWrap(True)
        layout.addWidget(lbl_val)

        # Subtitle
        lbl_sub = QLabel(default_sub, box)
        lbl_sub.setStyleSheet("font-size: 12px; color: #64748b;")
        lbl_sub.setWordWrap(True)
        layout.addWidget(lbl_sub)

        return box, lbl_val, lbl_sub

    def _create_checkpoint_panel(self) -> QFrame:
        """Create the Two-Checkpoint Connectivity Isolation comparison panel."""
        panel = QFrame()
        panel.setProperty("class", "Card")
        panel.setStyleSheet("""
            QFrame[class="Card"] {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 18px;
            }
        """)
        cp_layout = QVBoxLayout(panel)
        cp_layout.setContentsMargins(18, 16, 18, 16)
        cp_layout.setSpacing(12)

        # Title row
        title_row = QWidget(panel)
        tr_layout = QHBoxLayout(title_row)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(10)

        lbl_icon = QLabel(title_row)
        lbl_icon.setPixmap(_render_pixmap_icon("map", "#2563eb", 18))
        tr_layout.addWidget(lbl_icon)

        lbl_title = QLabel("Two-Checkpoint Connectivity Isolation", title_row)
        lbl_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #0f172a;")
        tr_layout.addWidget(lbl_title)
        tr_layout.addStretch()
        cp_layout.addWidget(title_row)

        # Side-by-side columns: Checkpoint 1 vs Checkpoint 2
        cols_widget = QWidget(panel)
        cols_layout = QHBoxLayout(cols_widget)
        cols_layout.setContentsMargins(0, 4, 0, 0)
        cols_layout.setSpacing(12)

        # Checkpoint 1 (Local Gateway)
        self.gw_col_box = QFrame(cols_widget)
        self.gw_col_box.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
            QLabel {
                border: none;
                background-color: transparent;
            }
        """)
        gw_layout = QVBoxLayout(self.gw_col_box)
        gw_layout.setContentsMargins(14, 12, 14, 12)
        gw_layout.setSpacing(6)

        lbl_gw_head = QLabel("<b>Checkpoint 1: Local Gateway (Host → Router)</b>", self.gw_col_box)
        lbl_gw_head.setTextFormat(Qt.TextFormat.RichText)
        lbl_gw_head.setStyleSheet("font-size: 13px; color: #0f172a;")
        lbl_gw_head.setWordWrap(True)
        gw_layout.addWidget(lbl_gw_head)

        self.lbl_gw_desc = QLabel(
            "Isolates local Wi-Fi radio signal, channel interference, and router LAN ports.",
            self.gw_col_box,
        )
        self.lbl_gw_desc.setStyleSheet("color: #475569; font-size: 12px; line-height: 1.35;")
        self.lbl_gw_desc.setWordWrap(True)
        gw_layout.addWidget(self.lbl_gw_desc)

        self.lbl_gw_telemetry = QLabel(
            f"• Interface: <span style='color:{ThemeTokens.BRAND_PRIMARY};'>--</span><br>"
            f"• Gateway IP: <span style='color:{ThemeTokens.BRAND_PRIMARY};'>--</span><br>"
            f"• Packet Loss: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• Round-Trip Delay: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span>",
            self.gw_col_box,
        )
        self.lbl_gw_telemetry.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_gw_telemetry.setWordWrap(True)
        self.lbl_gw_telemetry.setStyleSheet("font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; margin-top: 4px;")
        gw_layout.addWidget(self.lbl_gw_telemetry)
        cols_layout.addWidget(self.gw_col_box, 1)

        # Checkpoint 2 (External Internet)
        self.ext_col_box = QFrame(cols_widget)
        self.ext_col_box.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
            QLabel {
                border: none;
                background-color: transparent;
            }
        """)
        ext_layout = QVBoxLayout(self.ext_col_box)
        ext_layout.setContentsMargins(14, 12, 14, 12)
        ext_layout.setSpacing(6)

        lbl_ext_head = QLabel("<b>Checkpoint 2: External Internet (Router → WAN)</b>", self.ext_col_box)
        lbl_ext_head.setTextFormat(Qt.TextFormat.RichText)
        lbl_ext_head.setStyleSheet("font-size: 13px; color: #0f172a;")
        lbl_ext_head.setWordWrap(True)
        ext_layout.addWidget(lbl_ext_head)

        self.lbl_ext_desc = QLabel(
            "Measures upstream ISP transit line, multi-target backbones, and public resolvers.",
            self.ext_col_box,
        )
        self.lbl_ext_desc.setStyleSheet("color: #475569; font-size: 12px; line-height: 1.35;")
        self.lbl_ext_desc.setWordWrap(True)
        ext_layout.addWidget(self.lbl_ext_desc)

        self.lbl_ext_telemetry = QLabel(
            f"• Multi-Targets: <span style='color:{ThemeTokens.BRAND_PRIMARY};'>Cloudflare, Google, Quad9</span><br>"
            f"• External Loss: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• External Latency: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• DNS Subsystem: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span>",
            self.ext_col_box,
        )
        self.lbl_ext_telemetry.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_ext_telemetry.setWordWrap(True)
        self.lbl_ext_telemetry.setStyleSheet("font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; margin-top: 4px;")
        ext_layout.addWidget(self.lbl_ext_telemetry)
        cols_layout.addWidget(self.ext_col_box, 1)

        cp_layout.addWidget(cols_widget)

        # Bottom Action Bar
        act_row = QWidget(panel)
        a_layout = QHBoxLayout(act_row)
        a_layout.setContentsMargins(0, 6, 0, 0)
        a_layout.setSpacing(12)

        btn_run = QPushButton("▶  Run Network Diagnosis", act_row)
        btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_run.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 18px;
                border-radius: 16px;
                border: none;
            }
            QPushButton:hover {
                background-color: #1e293b;
            }
        """)
        if self.on_navigate_to_diagnosis:
            btn_run.clicked.connect(self.on_navigate_to_diagnosis)
        a_layout.addWidget(btn_run)

        lbl_info = QLabel("ⓘ  Executes unprivileged 5-stage measurement pipeline and deterministic rules.", act_row)
        lbl_info.setStyleSheet("color: #64748b; font-size: 12px;")
        a_layout.addWidget(lbl_info)
        a_layout.addStretch()

        cp_layout.addWidget(act_row)
        return panel

    def _create_latest_run_card(self) -> QFrame:
        """Create the Most Recent Diagnostic Run card."""
        card = QFrame()
        card.setProperty("class", "Card")
        card.setStyleSheet("""
            QFrame[class="Card"] {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 18px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Header row
        header_row = QWidget(card)
        h_layout = QHBoxLayout(header_row)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(10)

        lbl_icon = QLabel(header_row)
        lbl_icon.setPixmap(_render_pixmap_icon("report", "#2563eb", 18))
        h_layout.addWidget(lbl_icon)

        lbl_title = QLabel("Most Recent Diagnostic Run", header_row)
        lbl_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #0f172a;")
        h_layout.addWidget(lbl_title)

        self.lbl_lr_status_badge = QLabel("COMPLETED", header_row)
        self.lbl_lr_status_badge.setStyleSheet("""
            background-color: #ecfdf5;
            color: #059669;
            font-size: 11px;
            font-weight: 800;
            padding: 3px 10px;
            border-radius: 10px;
        """)
        h_layout.addWidget(self.lbl_lr_status_badge)

        h_layout.addStretch()

        self.lr_run_id_badge = RunIdBadge(parent=header_row, compact=True)
        h_layout.addWidget(self.lr_run_id_badge)
        layout.addWidget(header_row)

        # Run detail metadata row
        self.lbl_lr_info = QLabel("Run details...", card)
        self.lbl_lr_info.setStyleSheet("color: #475569; font-size: 13px; line-height: 1.4;")
        self.lbl_lr_info.setWordWrap(True)
        layout.addWidget(self.lbl_lr_info)

        # Action buttons row
        btn_row = QWidget(card)
        b_layout = QHBoxLayout(btn_row)
        b_layout.setContentsMargins(0, 4, 0, 0)

        self.btn_view_latest = QPushButton("🗎  View Full Report  →", btn_row)
        self.btn_view_latest.setObjectName("primaryButton")
        self.btn_view_latest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_latest.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 18px;
                border-radius: 16px;
                border: none;
            }
            QPushButton:hover {
                background-color: #1e293b;
            }
        """)
        self.btn_view_latest.clicked.connect(self._handle_view_latest_clicked)
        b_layout.addWidget(self.btn_view_latest)
        b_layout.addStretch()

        layout.addWidget(btn_row)
        card.setVisible(False)
        return card

    def _create_quick_actions_card(self) -> QFrame:
        """Create the Quick Actions card connecting directly to application workflows."""
        card = QFrame()
        card.setObjectName("quickActionsCard")
        card.setStyleSheet("""
            #quickActionsCard {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 18px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Header
        head_row = QWidget(card)
        h_layout = QHBoxLayout(head_row)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(8)

        lbl_icon = QLabel(head_row)
        lbl_icon.setPixmap(_render_pixmap_icon("bolt", "#10b981", 18))
        h_layout.addWidget(lbl_icon)

        lbl_title = QLabel("Quick Actions", head_row)
        lbl_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #0f172a;")
        h_layout.addWidget(lbl_title)
        h_layout.addStretch()
        layout.addWidget(head_row)

        actions = [
            ("search", "Run Diagnosis", "Check your network health", lambda: self._navigate_page(1)),
            ("map", "View Network Map", "Explore live topology", lambda: self._navigate_page(2)),
            ("report", "Generate Report", "Detailed insights & logs", lambda: self._navigate_page(4)),
            ("settings", "Settings", "Configure your preferences", lambda: self._navigate_page(5)),
        ]

        for icon_name, title, desc, callback in actions:
            act_btn = QPushButton(card)
            act_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            act_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f8fafc;
                    border: 1px solid #e2e8f0;
                    border-radius: 12px;
                    padding: 8px 12px;
                    text-align: left;
                }
                QPushButton:hover {
                    background-color: #f1f5f9;
                    border-color: #cbd5e1;
                }
                QLabel {
                    border: none;
                    background-color: transparent;
                }
            """)
            act_btn.clicked.connect(callback)

            ab_layout = QHBoxLayout(act_btn)
            ab_layout.setContentsMargins(0, 0, 0, 0)
            ab_layout.setSpacing(10)

            c_icon = QLabel(act_btn)
            c_icon.setFixedSize(30, 30)
            c_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c_icon.setStyleSheet("background-color: #ecfdf5; border-radius: 15px;")
            c_icon.setPixmap(_render_pixmap_icon(icon_name, "#10b981", 15))
            ab_layout.addWidget(c_icon)

            text_box = QWidget(act_btn)
            tb_layout = QVBoxLayout(text_box)
            tb_layout.setContentsMargins(0, 0, 0, 0)
            tb_layout.setSpacing(2)

            lbl_t = QLabel(title, text_box)
            lbl_t.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a;")
            tb_layout.addWidget(lbl_t)

            lbl_d = QLabel(desc, text_box)
            lbl_d.setStyleSheet("font-size: 11px; color: #64748b;")
            tb_layout.addWidget(lbl_d)

            ab_layout.addWidget(text_box)
            ab_layout.addStretch()

            lbl_chev = QLabel(act_btn)
            lbl_chev.setPixmap(_render_pixmap_icon("chevron_right", "#94a3b8", 14))
            ab_layout.addWidget(lbl_chev)

            layout.addWidget(act_btn)

        layout.addStretch()
        return card

    def _navigate_page(self, index: int) -> None:
        """Helper to navigate to target stacked widget page."""
        if index == 1 and self.on_navigate_to_diagnosis:
            self.on_navigate_to_diagnosis()
            return

        # Attempt to reach MainWindow.switch_page
        parent_win = self.window()
        if hasattr(parent_win, "switch_page") and callable(parent_win.switch_page):
            parent_win.switch_page(index)

    # -----------------------------------------------------------------------
    # Data Binding & Real Diagnostic Synchronization
    # -----------------------------------------------------------------------
    def refresh_overview(self) -> None:
        """Inspect HistoryService to display the latest run and real KPI summaries."""
        if not self.history_service:
            return

        runs = self.history_service.list_runs(limit=1)
        if not runs:
            self._latest_run_id = None
            self.latest_run_card.setVisible(False)
            self.empty_state.setVisible(True)
            self._reset_kpi_to_idle()
            return

        latest = runs[0]
        self._latest_run_id = latest.run_id
        self.empty_state.setVisible(False)
        self.latest_run_card.setVisible(True)

        detail: Optional[HistoricalRunDetail] = self.history_service.get_run_detail(latest.run_id)

        gw_ip = latest.gateway_ip or "Unresolved"
        iface = latest.interface_name or "Unknown"

        # Update Latest Run Brief
        self.lr_run_id_badge.set_run_id(latest.run_id)
        self.lbl_lr_status_badge.setText(latest.status.value.upper())
        self.lbl_lr_info.setText(
            f"<b>Status:</b> {latest.status.value} | <b>Gateway:</b> {gw_ip} | <b>Interface:</b> {iface}<br>"
            f"<b>Started:</b> {format_ist(latest.start_time_utc)}"
        )
        self.lbl_lr_info.setTextFormat(Qt.TextFormat.RichText)

        if not detail:
            return

        # Parse empirical summaries
        gw_loss_pct: Optional[float] = None
        gw_median_rtt: Optional[float] = None
        gw_jitter: Optional[float] = None

        ext_loss_pct: Optional[float] = None
        ext_median_rtt: Optional[float] = None
        ext_jitter: Optional[float] = None

        dns_status_str = "Not Evaluated"
        bufferbloat_str = "Not Evaluated"

        for s in detail.summaries:
            is_gw = (
                s.target_id == latest.gateway_ip
                or (s.target_id and ("gw" in s.target_id.lower() or "gateway" in s.target_id.lower()))
                or (s.target_id and not any(k in s.target_id.lower() for k in ("ext-", "dns-", "tp-", "cloudflare", "google", "quad9")))
            )
            if is_gw:
                if s.statistic_name == "loss_percentage":
                    gw_loss_pct = s.value_numeric
                elif s.statistic_name in ("median", "p50"):
                    gw_median_rtt = s.value_numeric
                elif s.statistic_name == "macd_jitter":
                    gw_jitter = s.value_numeric
            else:
                # External target metrics
                if s.statistic_name == "loss_percentage" and s.value_numeric is not None:
                    ext_loss_pct = max(ext_loss_pct or 0.0, s.value_numeric)
                elif s.statistic_name in ("median", "p50") and s.value_numeric is not None:
                    ext_median_rtt = max(ext_median_rtt or 0.0, s.value_numeric)
                elif s.statistic_name == "macd_jitter" and s.value_numeric is not None:
                    ext_jitter = max(ext_jitter or 0.0, s.value_numeric)
                elif s.metric_name in ("dns_resolution_time", "dns_failure_rate"):
                    if s.statistic_name == "loss_percentage" or s.metric_name == "dns_failure_rate":
                        if s.value_numeric is not None:
                            dns_status_str = f"{100.0 - s.value_numeric:.0f}% Resolved"
                    elif s.statistic_name == "median" and s.value_numeric is not None:
                        dns_status_str = f"~{s.value_numeric:.1f} ms"
                elif "bufferbloat" in s.metric_name or "bufferbloat" in s.statistic_name:
                    if s.value_numeric is not None:
                        bufferbloat_str = f"+{s.value_numeric:.1f} ms"

        # Update Global Ping number and continuous graph
        if ext_median_rtt is not None:
            self.lbl_global_ping_val.setText(f"{ext_median_rtt:.0f} ms")
            if hasattr(self, "ping_graph_widget"):
                self.ping_graph_widget.set_ping_value(ext_median_rtt, ext_jitter)

        # KPI 1: Overall Status based on findings
        has_critical = any(f.priority == FindingPriority.CRITICAL for f in detail.findings)
        has_warning = any(f.priority == FindingPriority.WARNING for f in detail.findings)

        if has_critical:
            self.lbl_kpi_overall_val.setText("CRITICAL FAULT ✖")
            self.lbl_kpi_overall_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.COLOR_ERROR};")
            self.lbl_kpi_overall_sub.setText("Connectivity failure isolated")
        elif has_warning:
            self.lbl_kpi_overall_val.setText("DEGRADED ⚠️")
            self.lbl_kpi_overall_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.COLOR_WARNING};")
            self.lbl_kpi_overall_sub.setText("Elevated latency or packet loss")
        else:
            self.lbl_kpi_overall_val.setText("HEALTHY ●")
            self.lbl_kpi_overall_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.COLOR_SUCCESS};")
            self.lbl_kpi_overall_sub.setText("All checkpoints passed")

        # KPI 2: Local Gateway
        if gw_loss_pct is not None and gw_loss_pct >= 100.0:
            self.lbl_kpi_gw_val.setText("UNREACHABLE")
            self.lbl_kpi_gw_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.COLOR_ERROR};")
            self.lbl_kpi_gw_sub.setText(f"{gw_ip} | 100% Packet Loss")
        elif gw_median_rtt is not None:
            gw_loss_display = f"{gw_loss_pct:.1f}% loss" if gw_loss_pct is not None else "0% loss"
            self.lbl_kpi_gw_val.setText(f"{gw_median_rtt:.1f} ms")
            self.lbl_kpi_gw_val.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {ThemeTokens.COLOR_SUCCESS};")
            self.lbl_kpi_gw_sub.setText(f"{gw_ip} ({gw_loss_display})")
        else:
            self.lbl_kpi_gw_val.setText(gw_ip)
            self.lbl_kpi_gw_sub.setText(f"Interface: {iface}")

        # KPI 3: External Internet
        if ext_loss_pct is not None and ext_loss_pct > 15.0:
            self.lbl_kpi_ext_val.setText(f"{ext_loss_pct:.1f}% LOSS")
            self.lbl_kpi_ext_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.COLOR_WARNING};")
            self.lbl_kpi_ext_sub.setText(f"Multi-target drop (Med: {ext_median_rtt or 0:.1f}ms)")
        elif ext_median_rtt is not None:
            self.lbl_kpi_ext_val.setText(f"{ext_median_rtt:.1f} ms")
            self.lbl_kpi_ext_val.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {ThemeTokens.COLOR_SUCCESS};")
            self.lbl_kpi_ext_sub.setText(f"3 Endpoints probed (0% loss)")
        else:
            self.lbl_kpi_ext_val.setText("--")
            self.lbl_kpi_ext_sub.setText("External endpoints unprobed")

        # KPI 4: DNS & Traffic Load
        self.lbl_kpi_dns_val.setText(dns_status_str)
        self.lbl_kpi_dns_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.COLOR_INFO};")
        self.lbl_kpi_dns_sub.setText(f"Bufferbloat {bufferbloat_str}")

        # Update Two-Checkpoint Comparison Panel Text
        gw_color = "#ef4444" if (gw_loss_pct and gw_loss_pct >= 100.0) else ("#f59e0b" if (gw_loss_pct and gw_loss_pct > 10.0) else "#10b981")
        gw_loss_str = f"{gw_loss_pct:.1f}%" if gw_loss_pct is not None else "0.0%"
        gw_rtt_str = f"{gw_median_rtt:.1f} ms" if gw_median_rtt is not None else "--"
        gw_jitter_str = f"{gw_jitter:.1f} ms" if gw_jitter is not None else "--"

        self.lbl_gw_telemetry.setText(
            f"• Interface: <span style='color:{ThemeTokens.BRAND_PRIMARY};'>{iface}</span><br>"
            f"• Gateway IP: <span style='color:{ThemeTokens.BRAND_PRIMARY};'>{gw_ip}</span><br>"
            f"• Packet Loss: <span style='color:{gw_color}; font-weight:700;'>{gw_loss_str}</span><br>"
            f"• Round-Trip Delay: <span style='color:{ThemeTokens.TEXT_PRIMARY}; font-weight:600;'>{gw_rtt_str}</span> (Jitter: {gw_jitter_str})"
        )

        ext_color = "#ef4444" if (ext_loss_pct and ext_loss_pct > 50.0) else ("#f59e0b" if (ext_loss_pct and ext_loss_pct > 15.0) else "#10b981")
        ext_loss_str = f"{ext_loss_pct:.1f}%" if ext_loss_pct is not None else "0.0%"
        ext_rtt_str = f"{ext_median_rtt:.1f} ms" if ext_median_rtt is not None else "--"
        ext_jitter_str = f"{ext_jitter:.1f} ms" if ext_jitter is not None else "--"

        self.lbl_ext_telemetry.setText(
            f"• Multi-Targets: <span style='color:{ThemeTokens.BRAND_PRIMARY};'>Cloudflare, Google, Quad9</span><br>"
            f"• External Loss: <span style='color:{ext_color}; font-weight:700;'>{ext_loss_str}</span><br>"
            f"• External Latency: <span style='color:{ThemeTokens.TEXT_PRIMARY}; font-weight:600;'>{ext_rtt_str}</span> (Jitter: {ext_jitter_str})<br>"
            f"• DNS Subsystem: <span style='color:{ThemeTokens.BRAND_PRIMARY};'>{dns_status_str}</span>"
        )

    def _reset_kpi_to_idle(self) -> None:
        """Reset metric cards to idle state when database is empty."""
        self.lbl_kpi_overall_val.setText("IDLE — READY")
        self.lbl_kpi_overall_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.COLOR_INFO};")
        self.lbl_kpi_overall_sub.setText("Awaiting diagnostic assessment")

        self.lbl_kpi_gw_val.setText("--")
        self.lbl_kpi_gw_val.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {ThemeTokens.TEXT_MUTED};")
        self.lbl_kpi_gw_sub.setText("Router unprobed")

        self.lbl_kpi_ext_val.setText("--")
        self.lbl_kpi_ext_val.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {ThemeTokens.TEXT_MUTED};")
        self.lbl_kpi_ext_sub.setText("Multi-targets unprobed")

        self.lbl_kpi_dns_val.setText("--")
        self.lbl_kpi_dns_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {ThemeTokens.TEXT_MUTED};")
        self.lbl_kpi_dns_sub.setText("Resolvers & Bufferbloat")

        self.lbl_gw_telemetry.setText(
            f"• Interface: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• Gateway IP: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• Packet Loss: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• Round-Trip Delay: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span>"
        )
        self.lbl_ext_telemetry.setText(
            f"• Multi-Targets: <span style='color:{ThemeTokens.TEXT_MUTED};'>Cloudflare, Google, Quad9</span><br>"
            f"• External Loss: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• External Latency: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span><br>"
            f"• DNS Subsystem: <span style='color:{ThemeTokens.TEXT_MUTED};'>--</span>"
        )

        if hasattr(self, "lbl_global_ping_val"):
            self.lbl_global_ping_val.setText("30 ms")
        if hasattr(self, "ping_graph_widget"):
            self.ping_graph_widget.set_ping_value(30.0)

    def _handle_view_latest_clicked(self) -> None:
        """Handle CTA click to navigate to the latest run findings."""
        if self._latest_run_id and self.on_view_results:
            self.on_view_results(self._latest_run_id)
        else:
            self._navigate_page(2)

    def _open_watch_overview_dialog(self) -> None:
        """Launch the interactive platform walkthrough and architecture dialog."""
        dialog = OverviewWalkthroughDialog(
            parent=self.window() if self.window() else self,
            on_start_diagnosis=self.on_navigate_to_diagnosis,
        )
        dialog.exec()

    def closeEvent(self, event) -> None:
        """Ensure background telemetry worker terminates cleanly upon closing."""
        if hasattr(self, "telemetry_service") and self.telemetry_service is not None:
            self.telemetry_service.stop()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        """Pause background telemetry polling when Overview page is not visible."""
        if hasattr(self, "telemetry_service") and self.telemetry_service is not None:
            self.telemetry_service.pause()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        """Resume background telemetry polling when Overview page becomes visible."""
        if hasattr(self, "telemetry_service") and self.telemetry_service is not None:
            self.telemetry_service.resume()
        super().showEvent(event)

