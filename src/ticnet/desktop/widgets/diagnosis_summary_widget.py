"""Diagnosis Summary Panel (Hero Verdict Banner) for TicNet Results Presentation.

Provides a prominent, beginner-friendly diagnostic verdict inspired by PingPlotter
and NetSonar command centers:
- Verdict status indicator (Green: Healthy, Amber: Degraded, Red: Confirmed Failure, Grey: Inconclusive).
- Diagnosis name and severity (CRITICAL, WARNING, INFO).
- Plain English explanation.
- Affected network checkpoint (Checkpoint 1 Local LAN vs Checkpoint 2 External WAN).
- Session timestamp.
- Quick navigation shortcuts to evidence, stage timeline, telemetry charts, and recommendations.

Strict Invariants:
- Does NOT invent synthetic confidence scores or severity levels.
- Reuses exact Finding entities from the deterministic rule engine.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens
from ticnet.desktop.widgets.run_id_badge import RunIdBadge
from ticnet.models.entities import Finding, TestRun
from ticnet.models.enums import FindingCategory, FindingPriority


_SVG_ICONS = {
    "check": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='20 6 9 17 4 12'/>
    </svg>""",
    "alert": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='10'/>
        <line x1='12' y1='8' x2='12' y2='12'/>
        <line x1='12' y1='16' x2='12.01' y2='16'/>
    </svg>""",
    "x": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='10'/>
        <line x1='15' y1='9' x2='9' y2='15'/>
        <line x1='9' y1='9' x2='15' y2='15'/>
    </svg>""",
    "shield": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'/>
        <polyline points='9 12 11 14 15 10'/>
    </svg>""",
    "network": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='18' cy='5' r='3'/>
        <circle cx='6' cy='12' r='3'/>
        <circle cx='18' cy='19' r='3'/>
        <line x1='8.59' y1='13.51' x2='15.42' y2='17.49'/>
        <line x1='15.41' y1='6.51' x2='8.59' y2='10.49'/>
    </svg>""",
    "clock": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='10'/>
        <polyline points='12 6 12 12 16 14'/>
    </svg>""",
    "nodes": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='18' cy='5' r='3'/>
        <circle cx='6' cy='12' r='3'/>
        <circle cx='18' cy='19' r='3'/>
        <line x1='8.59' y1='13.51' x2='15.42' y2='17.49'/>
        <line x1='15.41' y1='6.51' x2='8.59' y2='10.49'/>
    </svg>""",
    "matrix": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <rect x='3' y='3' width='18' height='18' rx='2'/>
        <line x1='3' y1='9' x2='21' y2='9'/>
        <line x1='3' y1='15' x2='21' y2='15'/>
        <line x1='9' y1='3' x2='9' y2='21'/>
        <line x1='15' y1='3' x2='15' y2='21'/>
    </svg>""",
    "chart": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <line x1='18' y1='20' x2='18' y2='10'/>
        <line x1='12' y1='20' x2='12' y2='4'/>
        <line x1='6' y1='20' x2='6' y2='14'/>
    </svg>""",
    "bulb": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M9 18h6'/>
        <path d='M10 22h4'/>
        <path d='M12 2a7 7 0 0 0-7 7c0 2.5 1.5 4.5 3 6h8c1.5-1.5 3-3.5 3-6a7 7 0 0 0-7-7z'/>
    </svg>""",
}


def _render_svg_pixmap(name: str, color: str = "#64748b", size: int = 16) -> QPixmap:
    """Render crisp antialiased SVG icon into a QPixmap."""
    svg_template = _SVG_ICONS.get(name, _SVG_ICONS["nodes"])
    svg_data = svg_template.replace("COLOR", color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_data))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pixmap


def _render_circle_icon(icon_name: str, icon_color: str, bg_color: str, diameter: int = 48) -> QPixmap:
    """Render a solid colored circle with an antialiased SVG icon centered inside."""
    pixmap = QPixmap(diameter, diameter)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(bg_color))
    painter.drawEllipse(0, 0, diameter, diameter)

    inner_size = int(diameter * 0.5)
    inner_pix = _render_svg_pixmap(icon_name, icon_color, inner_size)
    offset = (diameter - inner_size) // 2
    painter.drawPixmap(offset, offset, inner_pix)
    painter.end()
    return pixmap


class DiagnosisSummaryPanel(QFrame):
    """Hero verdict banner presenting high-impact diagnostic conclusions."""

    # Navigation signals
    sig_jump_to_evidence = Signal()
    sig_jump_to_timeline = Signal()
    sig_jump_to_charts = Signal()
    sig_jump_to_recommendations = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background-color: transparent; border: none;")
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(12)

        # -------------------------------------------------------------------
        # 1. Top Metadata & Badges Bar
        # -------------------------------------------------------------------
        self.top_row = QWidget(self)
        self.tr_layout = QHBoxLayout(self.top_row)
        self.tr_layout.setContentsMargins(0, 0, 0, 0)
        self.tr_layout.setSpacing(10)

        # Verdict status pill badge
        self.lbl_verdict_badge = QLabel("• HEALTHY — ALL CHECKPOINTS NORMAL", self.top_row)
        self.lbl_verdict_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 12px; "
            f"border-radius: {ThemeTokens.RADIUS_PILL}; "
            f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; "
            f"color: {ThemeTokens.COLOR_SUCCESS}; "
            f"border: 1px solid {ThemeTokens.BORDER_SUCCESS};"
        )
        self.tr_layout.addWidget(self.lbl_verdict_badge)

        # Checkpoints badge container
        self.checkpoint_widget = QFrame(self.top_row)
        self.checkpoint_widget.setStyleSheet(
            f"background-color: #ffffff; border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_PILL}; padding: 2px 10px;"
        )
        cp_layout = QHBoxLayout(self.checkpoint_widget)
        cp_layout.setContentsMargins(4, 2, 4, 2)
        cp_layout.setSpacing(6)

        cp_icon_lbl = QLabel(self.checkpoint_widget)
        cp_icon_lbl.setPixmap(_render_svg_pixmap("network", ThemeTokens.TEXT_MUTED, 13))
        cp_layout.addWidget(cp_icon_lbl)

        self.lbl_checkpoint_badge = QLabel(
            "Checkpoints: <span style='color:#d97706; font-weight:700;'>LAN</span> & <span style='color:#4f46e5; font-weight:700;'>WAN</span> Operational",
            self.checkpoint_widget,
        )
        self.lbl_checkpoint_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ThemeTokens.TEXT_SECONDARY};"
        )
        cp_layout.addWidget(self.lbl_checkpoint_badge)
        self.tr_layout.addWidget(self.checkpoint_widget)

        self.tr_layout.addStretch()

        # RUN ID Badge
        self.run_id_badge = RunIdBadge(parent=self.top_row, compact=False)
        self.tr_layout.addWidget(self.run_id_badge)

        # Session timestamp
        self.time_meta_widget = QWidget(self.top_row)
        tm_layout = QHBoxLayout(self.time_meta_widget)
        tm_layout.setContentsMargins(0, 0, 0, 0)
        tm_layout.setSpacing(5)

        clock_lbl = QLabel(self.time_meta_widget)
        clock_lbl.setPixmap(_render_svg_pixmap("clock", ThemeTokens.TEXT_MUTED, 13))
        tm_layout.addWidget(clock_lbl)

        self.lbl_session_meta = QLabel("Started: --", self.time_meta_widget)
        self.lbl_session_meta.setStyleSheet(
            f"font-size: 11px; color: {ThemeTokens.TEXT_MUTED}; font-family: Consolas, monospace;"
        )
        tm_layout.addWidget(self.lbl_session_meta)
        self.tr_layout.addWidget(self.time_meta_widget)

        self.main_layout.addWidget(self.top_row)

        # -------------------------------------------------------------------
        # 2. Hero Verdict Banner Card (Rounded container matching reference image)
        # -------------------------------------------------------------------
        self.verdict_card = QFrame(self)
        self.verdict_card.setStyleSheet(
            f"background-color: #f6fef9; "
            f"border: 1px solid #86efac; "
            f"border-radius: 14px;"
        )
        vc_layout = QHBoxLayout(self.verdict_card)
        vc_layout.setContentsMargins(18, 16, 18, 16)
        vc_layout.setSpacing(16)

        # Large circular status icon on left
        self.verdict_icon_label = QLabel(self.verdict_card)
        self.verdict_icon_label.setFixedSize(48, 48)
        self.verdict_icon_label.setPixmap(_render_circle_icon("check", "#ffffff", ThemeTokens.COLOR_SUCCESS, 48))
        self.verdict_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vc_layout.addWidget(self.verdict_icon_label)

        # Headline and plain English explanation (Center expands)
        center_widget = QWidget(self.verdict_card)
        c_layout = QVBoxLayout(center_widget)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(4)

        self.lbl_diagnosis_title = QLabel("Healthy Network Connection — No Faults Detected", center_widget)
        self.lbl_diagnosis_title.setStyleSheet(
            f"font-size: 20px; font-weight: 800; color: #064e3b; letter-spacing: -0.3px;"
        )
        self.lbl_diagnosis_title.setWordWrap(True)
        c_layout.addWidget(self.lbl_diagnosis_title)

        self.lbl_plain_summary = QLabel(
            "All deterministic diagnostic rules evaluated to normal. Local gateway reachability, "
            "external endpoint path latency, and DNS resolution meet baseline operational thresholds. "
            "No packet loss or latency bottlenecks were detected across tested paths.",
            center_widget,
        )
        self.lbl_plain_summary.setStyleSheet(
            f"font-size: 13px; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.45;"
        )
        self.lbl_plain_summary.setWordWrap(True)
        c_layout.addWidget(self.lbl_plain_summary)

        vc_layout.addWidget(center_widget, 1)

        # Network Status indicator box on right
        self.status_card = QFrame(self.verdict_card)
        self.status_card.setStyleSheet(
            "background-color: #ecfdf5; border: 1px solid #86efac; border-radius: 10px; padding: 6px 14px;"
        )
        sc_layout = QHBoxLayout(self.status_card)
        sc_layout.setContentsMargins(10, 8, 12, 8)
        sc_layout.setSpacing(10)

        self.status_icon_label = QLabel(self.status_card)
        self.status_icon_label.setPixmap(_render_svg_pixmap("shield", "#059669", 24))
        self.status_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sc_layout.addWidget(self.status_icon_label)

        status_text_box = QWidget(self.status_card)
        stb_layout = QVBoxLayout(status_text_box)
        stb_layout.setContentsMargins(0, 0, 0, 0)
        stb_layout.setSpacing(1)

        self.lbl_status_card_head = QLabel("Network Status", status_text_box)
        self.lbl_status_card_head.setStyleSheet("font-size: 11px; font-weight: 600; color: #047857;")
        stb_layout.addWidget(self.lbl_status_card_head)

        self.lbl_status_card_val = QLabel("NORMAL", status_text_box)
        self.lbl_status_card_val.setStyleSheet("font-size: 15px; font-weight: 800; color: #047857; letter-spacing: 0.5px;")
        stb_layout.addWidget(self.lbl_status_card_val)

        sc_layout.addWidget(status_text_box)
        vc_layout.addWidget(self.status_card)

        self.main_layout.addWidget(self.verdict_card)

        # -------------------------------------------------------------------
        # 3. Quick Action Navigation Tabs ("Jump to:")
        # -------------------------------------------------------------------
        self.nav_row = QWidget(self)
        self.nr_layout = QHBoxLayout(self.nav_row)
        self.nr_layout.setContentsMargins(0, 2, 0, 0)
        self.nr_layout.setSpacing(8)

        lbl_nav_hint = QLabel("Jump to:", self.nav_row)
        lbl_nav_hint.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ThemeTokens.TEXT_MUTED};")
        self.nr_layout.addWidget(lbl_nav_hint)

        # Active tab style (Stage Timeline selected)
        self.btn_nav_timeline = QPushButton("Stage Timeline", self.nav_row)
        self.btn_nav_timeline.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_nav_timeline.setIcon(QIcon(_render_svg_pixmap("nodes", "#2563eb", 14)))
        self.btn_nav_timeline.setStyleSheet(
            "QPushButton { "
            "  font-size: 12px; font-weight: 600; color: #2563eb; "
            "  background-color: #eff6ff; border: 1px solid #93c5fd; "
            "  border-radius: 8px; padding: 6px 14px; "
            "} "
            "QPushButton:hover { background-color: #dbeafe; }"
        )
        self.btn_nav_timeline.clicked.connect(self.sig_jump_to_timeline.emit)
        self.nr_layout.addWidget(self.btn_nav_timeline)

        def _tab_style() -> str:
            return (
                "QPushButton { "
                "  font-size: 12px; font-weight: 500; color: #475569; "
                "  background-color: #ffffff; border: 1px solid #e2e8f0; "
                "  border-radius: 8px; padding: 6px 14px; "
                "} "
                "QPushButton:hover { "
                "  background-color: #f8fafc; border-color: #cbd5e1; color: #0f172a; "
                "}"
            )

        self.btn_nav_evidence = QPushButton("Evidence Matrix", self.nav_row)
        self.btn_nav_evidence.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_nav_evidence.setIcon(QIcon(_render_svg_pixmap("matrix", "#64748b", 14)))
        self.btn_nav_evidence.setStyleSheet(_tab_style())
        self.btn_nav_evidence.clicked.connect(self.sig_jump_to_evidence.emit)
        self.nr_layout.addWidget(self.btn_nav_evidence)

        self.btn_nav_charts = QPushButton("Telemetry Charts", self.nav_row)
        self.btn_nav_charts.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_nav_charts.setIcon(QIcon(_render_svg_pixmap("chart", "#64748b", 14)))
        self.btn_nav_charts.setStyleSheet(_tab_style())
        self.btn_nav_charts.clicked.connect(self.sig_jump_to_charts.emit)
        self.nr_layout.addWidget(self.btn_nav_charts)

        self.btn_nav_recommendations = QPushButton("Recommended Actions", self.nav_row)
        self.btn_nav_recommendations.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_nav_recommendations.setIcon(QIcon(_render_svg_pixmap("bulb", "#64748b", 14)))
        self.btn_nav_recommendations.setStyleSheet(_tab_style())
        self.btn_nav_recommendations.clicked.connect(self.sig_jump_to_recommendations.emit)
        self.nr_layout.addWidget(self.btn_nav_recommendations)

        self.nr_layout.addStretch()
        self.main_layout.addWidget(self.nav_row)

    def set_verdict_data(
        self,
        findings: List[Finding],
        run: Optional[TestRun] = None,
    ) -> None:
        """Populate hero verdict banner with empirical findings and run metadata."""
        start_ts = run.start_time_utc if run and run.start_time_utc else "Unknown Start Time"
        run_id_str = run.run_id if run and run.run_id else "N/A"
        self.run_id_badge.set_run_id(run_id_str)
        self.lbl_session_meta.setText(f"Started: {start_ts}")

        if not findings:
            # Healthy baseline: No rule violations detected
            self.verdict_card.setStyleSheet(
                "background-color: #f6fef9; border: 1px solid #86efac; border-radius: 14px;"
            )
            self.verdict_icon_label.setPixmap(_render_circle_icon("check", "#ffffff", ThemeTokens.COLOR_SUCCESS, 48))

            self.lbl_verdict_badge.setText("• HEALTHY — ALL CHECKPOINTS NORMAL")
            self.lbl_verdict_badge.setStyleSheet(
                f"font-size: 11px; font-weight: 700; padding: 4px 12px; "
                f"border-radius: {ThemeTokens.RADIUS_PILL}; "
                f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; color: {ThemeTokens.COLOR_SUCCESS}; "
                f"border: 1px solid {ThemeTokens.BORDER_SUCCESS};"
            )
            self.lbl_checkpoint_badge.setText(
                "Checkpoints: <span style='color:#d97706; font-weight:700;'>LAN</span> & <span style='color:#4f46e5; font-weight:700;'>WAN</span> Operational"
            )
            self.lbl_diagnosis_title.setText("Healthy Network Connection — No Faults Detected")
            self.lbl_diagnosis_title.setStyleSheet("font-size: 20px; font-weight: 800; color: #064e3b; letter-spacing: -0.3px;")
            self.lbl_plain_summary.setText(
                "All deterministic diagnostic rules evaluated to normal. Local gateway reachability, "
                "external endpoint path latency, and DNS resolution meet baseline operational thresholds. "
                "No packet loss or latency bottlenecks were detected across tested paths."
            )
            self.status_card.setStyleSheet("background-color: #ecfdf5; border: 1px solid #86efac; border-radius: 10px; padding: 6px 14px;")
            self.lbl_status_card_head.setText("Network Status")
            self.lbl_status_card_head.setStyleSheet("font-size: 11px; font-weight: 600; color: #047857;")
            self.lbl_status_card_val.setText("NORMAL")
            self.lbl_status_card_val.setStyleSheet("font-size: 15px; font-weight: 800; color: #047857; letter-spacing: 0.5px;")
            self.status_icon_label.setPixmap(_render_svg_pixmap("shield", "#059669", 24))
            return

        # Sort findings by priority: CRITICAL -> WARNING -> INFO
        sorted_findings = sorted(
            findings,
            key=lambda f: (
                0 if f.priority == FindingPriority.CRITICAL else (1 if f.priority == FindingPriority.WARNING else 2)
            ),
        )
        primary = sorted_findings[0]

        # Determine severity style and verdict badge
        if primary.priority == FindingPriority.CRITICAL:
            bg_color = ThemeTokens.BG_ERROR_LIGHT
            border_color = ThemeTokens.BORDER_ERROR
            badge_color = ThemeTokens.COLOR_ERROR
            verdict_text = f"● CONFIRMED FAILURE: {primary.rule_id}"
            title_color = "#991b1b"
            card_border = "#fca5a5"
            card_bg = "#fff5f5"
            status_val = "CRITICAL"
            status_color = "#dc2626"
            status_box_bg = "#fee2e2"
            icon_type = "x"
        elif primary.priority == FindingPriority.WARNING:
            bg_color = ThemeTokens.BG_WARNING_LIGHT
            border_color = ThemeTokens.BORDER_WARNING
            badge_color = ThemeTokens.COLOR_WARNING
            verdict_text = f"● DEGRADED PERFORMANCE: {primary.rule_id}"
            title_color = "#92400e"
            card_border = "#fcd34d"
            card_bg = "#fffbeb"
            status_val = "WARNING"
            status_color = "#d97706"
            status_box_bg = "#fef3c7"
            icon_type = "alert"
        else:
            if primary.category == FindingCategory.INCONCLUSIVE:
                bg_color = ThemeTokens.BG_MUTED
                border_color = ThemeTokens.BORDER_DEFAULT
                badge_color = ThemeTokens.TEXT_MUTED
                verdict_text = f"● INCONCLUSIVE DIAGNOSIS: {primary.rule_id}"
                title_color = ThemeTokens.TEXT_PRIMARY
                card_border = ThemeTokens.BORDER_DEFAULT
                card_bg = "#f8fafc"
                status_val = "INCONCLUSIVE"
                status_color = ThemeTokens.TEXT_MUTED
                status_box_bg = "#f1f5f9"
                icon_type = "alert"
            else:
                bg_color = ThemeTokens.BG_INFO_LIGHT
                border_color = ThemeTokens.BORDER_INFO
                badge_color = ThemeTokens.COLOR_INFO
                verdict_text = f"● INFORMATIONAL: {primary.rule_id}"
                title_color = "#0369a1"
                card_border = "#bae6fd"
                card_bg = "#f0f9ff"
                status_val = "INFO"
                status_color = "#0284c7"
                status_box_bg = "#e0f2fe"
                icon_type = "alert"

        self.verdict_card.setStyleSheet(f"background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 14px;")
        self.verdict_icon_label.setPixmap(_render_circle_icon(icon_type, "#ffffff", badge_color, 48))

        self.lbl_verdict_badge.setText(verdict_text)
        self.lbl_verdict_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 12px; "
            f"border-radius: {ThemeTokens.RADIUS_PILL}; "
            f"background-color: {bg_color}; color: {badge_color}; border: 1px solid {border_color};"
        )

        # Checkpoint localization badge
        if primary.category == FindingCategory.LOCAL_NETWORK:
            cp_text = "Checkpoint 1 [Local Gateway / LAN]"
        elif primary.category == FindingCategory.EXTERNAL_PATH:
            cp_text = "Checkpoint 2 [External Internet / WAN]"
        elif primary.category == FindingCategory.DNS_SUBSYSTEM:
            cp_text = "Checkpoint 2 [DNS Resolvers]"
        elif primary.category == FindingCategory.BUFFERBLOAT:
            cp_text = "Traffic Load [Bufferbloat / Queue]"
        else:
            cp_text = "Inconclusive Checkpoint"

        self.lbl_checkpoint_badge.setText(f"Affected: {cp_text}")

        # Title & Summary from actual Finding entity
        self.lbl_diagnosis_title.setText(primary.plain_summary)
        self.lbl_diagnosis_title.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {title_color}; letter-spacing: -0.3px;")

        # Status indicator card
        self.status_card.setStyleSheet(f"background-color: {status_box_bg}; border: 1px solid {card_border}; border-radius: 10px; padding: 6px 14px;")
        self.lbl_status_card_head.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {title_color};")
        self.lbl_status_card_val.setText(status_val)
        self.lbl_status_card_val.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {status_color}; letter-spacing: 0.5px;")
        self.status_icon_label.setPixmap(_render_svg_pixmap("shield", badge_color, 24))

        # Compose descriptive explanation
        additional_findings_count = len(findings) - 1
        extra_str = f" (+ {additional_findings_count} secondary finding{'s' if additional_findings_count > 1 else ''})" if additional_findings_count > 0 else ""

        limitation_notice = (
            f"<br><span style='color:{ThemeTokens.TEXT_MUTED}; font-size:12px;'><i>Limitation: {primary.limitations}</i></span>"
            if primary.limitations and primary.limitations != "None"
            else ""
        )

        self.lbl_plain_summary.setText(
            f"<b>Primary Finding:</b> {primary.plain_summary}{extra_str}. "
            f"Deterministic rule <b>{primary.rule_id}</b> evaluated with <b>{primary.evidence_strength.value}</b> evidence strength."
            f"{limitation_notice}"
        )
