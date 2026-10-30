"""Diagnostic Workflow Timeline Widget for TicNet Stage Execution Visualization.

Presents a clear visual timeline showing the five diagnostic workflow stages:
1. Gateway Discovery.
2. Gateway Reachability.
3. External Multi-Target Ping.
4. DNS Resolution.
5. Throughput and Loaded Latency.

Strict Invariants:
- Shows actual execution status (COMPLETED, FAILED, SKIPPED, or UNAVAILABLE).
- A skipped test must NEVER appear as a successful test.
- Accurately reports start/end timestamps and empirical measurement summaries.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens
from ticnet.models.entities import Observation, Summary, TestRun, TestStage
from ticnet.models.enums import StageStatus


_TIMELINE_SVG_ICONS = {
    "router": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <rect x='2' y='14' width='20' height='8' rx='2'/>
        <path d='M6 14V8a3 3 0 0 1 6 0v6'/>
        <line x1='18' y1='8' x2='18.01' y2='8'/>
        <line x1='14' y1='8' x2='14.01' y2='8'/>
    </svg>""",
    "reachability": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='22 12 18 12 15 21 9 3 6 12 2 12'/>
    </svg>""",
    "server": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <rect x='2' y='2' width='20' height='8' rx='2'/>
        <rect x='2' y='14' width='20' height='8' rx='2'/>
        <line x1='6' y1='6' x2='6.01' y2='6'/>
        <line x1='6' y1='18' x2='6.01' y2='18'/>
    </svg>""",
    "dns": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <ellipse cx='12' cy='5' rx='9' ry='3'/>
        <path d='M21 12c0 1.66-4 3-9 3s-9-1.34-9-3'/>
        <path d='M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5'/>
    </svg>""",
    "gauge": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M12 2a10 10 0 1 0 10 10'/>
        <path d='M12 12l4-4'/>
        <circle cx='12' cy='12' r='2'/>
    </svg>""",
    "wifi": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M5 12.55a11 11 0 0 1 14.08 0'/>
        <path d='M1.42 9a16 16 0 0 1 21.16 0'/>
        <path d='M8.53 16.11a6 6 0 0 1 6.95 0'/>
        <line x1='12' y1='20' x2='12.01' y2='20'/>
    </svg>""",
    "pulse": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='22 12 18 12 15 21 9 3 6 12 2 12'/>
    </svg>""",
    "globe": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='10'/>
        <line x1='2' y1='12' x2='22' y2='12'/>
        <path d='M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'/>
    </svg>""",
    "download": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/>
        <polyline points='7 10 12 15 17 10'//>
        <line x1='12' y1='15' x2='12' y2='3'/>
    </svg>""",
    "clock": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='12' cy='12' r='10'/>
        <polyline points='12 6 12 12 16 14'/>
    </svg>""",
    "check_small": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round'>
        <polyline points='20 6 9 17 4 12'/>
    </svg>""",
    "pipeline": """<svg viewBox='0 0 24 24' fill='none' stroke='COLOR' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>
        <circle cx='18' cy='5' r='3'/>
        <circle cx='6' cy='12' r='3'/>
        <circle cx='18' cy='19' r='3'/>
        <line x1='8.59' y1='13.51' x2='15.42' y2='17.49'/>
        <line x1='15.41' y1='6.51' x2='8.59' y2='10.49'/>
    </svg>""",
}


def _render_timeline_svg(name: str, color: str = "#64748b", size: int = 16) -> QPixmap:
    """Render crisp antialiased SVG icon into a QPixmap."""
    svg_template = _TIMELINE_SVG_ICONS.get(name, _TIMELINE_SVG_ICONS["pipeline"])
    svg_data = svg_template.replace("COLOR", color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_data))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pixmap


STAGE_DEFINITIONS = [
    (1, "Stage 1", "Gateway Discovery", "Inspects routing table unprivileged to identify local router IP."),
    (2, "Stage 2", "Gateway Reachability", "Measures packet loss, median RTT, and delay variation to local router."),
    (3, "Stage 3", "External Multi-Target Ping", "Measures packet loss and path latency across diverse external servers."),
    (4, "Stage 4", "DNS Query Timing", "Direct UDP port 53 query timing bypassing OS resolver cache."),
    (5, "Stage 5", "Throughput & Loaded Latency", "Measures download throughput and bufferbloat under bounded downstream load."),
]

STAGE_CONFIGS = {
    1: {
        "icon": "router",
        "icon_bg": "#dbeafe",
        "icon_border": "#bfdbfe",
        "icon_color": "#2563eb",
        "pill_bg": "#eff6ff",
        "pill_border": "#dbeafe",
        "pill_icon": "wifi",
        "pill_text_color": "#1e40af",
    },
    2: {
        "icon": "reachability",
        "icon_bg": "#ccfbf1",
        "icon_border": "#99f6e4",
        "icon_color": "#0d9488",
        "pill_bg": "#f0fdf4",
        "pill_border": "#bbf7d0",
        "pill_icon": "pulse",
        "pill_text_color": "#15803d",
    },
    3: {
        "icon": "server",
        "icon_bg": "#ede9fe",
        "icon_border": "#ddd6fe",
        "icon_color": "#7c3aed",
        "pill_bg": "#f5f3ff",
        "pill_border": "#ddd6fe",
        "pill_icon": "globe",
        "pill_text_color": "#6d28d9",
    },
    4: {
        "icon": "dns",
        "icon_bg": "#ffedd5",
        "icon_border": "#fed7aa",
        "icon_color": "#ea580c",
        "pill_bg": "#fffbeb",
        "pill_border": "#fde68a",
        "pill_icon": "dns",
        "pill_text_color": "#b45309",
    },
    5: {
        "icon": "gauge",
        "icon_bg": "#cffafe",
        "icon_border": "#a5f3fc",
        "icon_color": "#0891b2",
        "pill_bg": "#ecfeff",
        "pill_border": "#a5f3fc",
        "pill_icon": "download",
        "pill_text_color": "#0e7490",
    },
}


class StageStepCard(QFrame):
    """Individual visual card for a single diagnostic workflow stage."""

    def __init__(self, order: int, stage_num: str, name: str, desc: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.order = order
        self.stage_num = stage_num
        self.stage_name = name
        self.desc = desc
        self.cfg = STAGE_CONFIGS.get(order, STAGE_CONFIGS[1])

        self.setStyleSheet(
            "background-color: #ffffff; "
            "border: 1px solid #e2e8f0; "
            "border-radius: 12px;"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(138)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(8)

        # -------------------------------------------------------------------
        # 1. Header: Stage Icon + Stage Title + Completion Badge
        # -------------------------------------------------------------------
        top_row = QWidget(self)
        tr_layout = QHBoxLayout(top_row)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(10)

        # Left: Rounded square icon box
        self.icon_box = QLabel(top_row)
        self.icon_box.setFixedSize(32, 32)
        self.icon_box.setStyleSheet(
            f"background-color: {self.cfg['icon_bg']}; "
            f"border: 1px solid {self.cfg['icon_border']}; "
            f"border-radius: 8px;"
        )
        self.icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_box.setPixmap(_render_timeline_svg(self.cfg["icon"], self.cfg["icon_color"], 18))
        tr_layout.addWidget(self.icon_box)

        # Title: e.g. Stage 1: Gateway Discovery
        self.lbl_title = QLabel(f"<b>{stage_num}: {name}</b>", top_row)
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0f172a;")
        tr_layout.addWidget(self.lbl_title)

        tr_layout.addStretch()

        # Right: Pill Status Badge
        self.status_badge_frame = QFrame(top_row)
        self.status_badge_frame.setStyleSheet(
            "background-color: #f1f5f9; border-radius: 10px; padding: 2px 8px;"
        )
        sbf_layout = QHBoxLayout(self.status_badge_frame)
        sbf_layout.setContentsMargins(6, 2, 8, 2)
        sbf_layout.setSpacing(4)

        self.status_check_icon = QLabel(self.status_badge_frame)
        self.status_check_icon.setFixedSize(11, 11)
        self.status_check_icon.setPixmap(_render_timeline_svg("check_small", "#ffffff", 11))
        self.status_check_icon.setVisible(False)
        sbf_layout.addWidget(self.status_check_icon)

        self.lbl_status = QLabel("PENDING", self.status_badge_frame)
        self.lbl_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #64748b;")
        sbf_layout.addWidget(self.lbl_status)

        tr_layout.addWidget(self.status_badge_frame)
        card_layout.addWidget(top_row)

        # -------------------------------------------------------------------
        # 2. Description
        # -------------------------------------------------------------------
        self.lbl_desc = QLabel(desc, self)
        self.lbl_desc.setStyleSheet("font-size: 11px; color: #64748b; line-height: 1.35;")
        self.lbl_desc.setWordWrap(True)
        card_layout.addWidget(self.lbl_desc)

        card_layout.addStretch(1)

        # -------------------------------------------------------------------
        # 3. Empirical Outcome Banner Pill
        # -------------------------------------------------------------------
        self.outcome_box = QFrame(self)
        self.outcome_box.setStyleSheet(
            f"background-color: {self.cfg['pill_bg']}; "
            f"border: 1px solid {self.cfg['pill_border']}; "
            f"border-radius: 8px;"
        )
        ob_layout = QHBoxLayout(self.outcome_box)
        ob_layout.setContentsMargins(8, 5, 8, 5)
        ob_layout.setSpacing(7)

        self.outcome_icon = QLabel(self.outcome_box)
        self.outcome_icon.setFixedSize(14, 14)
        self.outcome_icon.setPixmap(_render_timeline_svg(self.cfg["pill_icon"], self.cfg["icon_color"], 14))
        ob_layout.addWidget(self.outcome_icon)

        self.lbl_outcome = QLabel("Awaiting execution...", self.outcome_box)
        self.lbl_outcome.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {self.cfg['pill_text_color']};")
        self.lbl_outcome.setWordWrap(True)
        ob_layout.addWidget(self.lbl_outcome, 1)

        card_layout.addWidget(self.outcome_box)

        # -------------------------------------------------------------------
        # 4. Timestamp Footer
        # -------------------------------------------------------------------
        self.time_box = QWidget(self)
        tb_layout = QHBoxLayout(self.time_box)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(5)

        self.clock_icon = QLabel(self.time_box)
        self.clock_icon.setFixedSize(12, 12)
        self.clock_icon.setPixmap(_render_timeline_svg("clock", "#94a3b8", 12))
        tb_layout.addWidget(self.clock_icon)

        self.lbl_time = QLabel("", self.time_box)
        self.lbl_time.setStyleSheet("font-size: 10.5px; color: #64748b; font-family: Consolas, monospace;")
        tb_layout.addWidget(self.lbl_time)
        tb_layout.addStretch()

        card_layout.addWidget(self.time_box)

    def set_stage_data(
        self,
        stage: Optional[TestStage],
        outcome_summary: str,
    ) -> None:
        """Update stage card with execution status, timestamps, and measurement outcomes."""
        if not stage:
            self.status_badge_frame.setStyleSheet("background-color: #f1f5f9; border-radius: 10px; padding: 2px 8px;")
            self.status_check_icon.setVisible(False)
            self.lbl_status.setText("NOT RUN")
            self.lbl_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #64748b;")
            self.lbl_outcome.setText("Stage was not scheduled for this run.")
            self.lbl_outcome.setStyleSheet("font-size: 11px; color: #94a3b8;")
            self.outcome_box.setStyleSheet("background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;")
            self.lbl_time.setText("")
            return

        status_val = stage.status.value if hasattr(stage.status, "value") else str(stage.status)

        # Format timestamps
        time_text = ""
        if stage.start_time_utc:
            start_short = stage.start_time_utc[11:19] if len(stage.start_time_utc) >= 19 else stage.start_time_utc
            if stage.end_time_utc:
                end_short = stage.end_time_utc[11:19] if len(stage.end_time_utc) >= 19 else stage.end_time_utc
                time_text = f"Executed: {start_short} → {end_short} UTC"
            else:
                time_text = f"Started: {start_short} UTC"
        self.lbl_time.setText(time_text)

        if status_val in ("SUCCESS", "COMPLETED"):
            self.status_badge_frame.setStyleSheet("background-color: #10b981; border-radius: 10px; padding: 2px 8px;")
            self.status_check_icon.setVisible(True)
            self.lbl_status.setText("COMPLETED")
            self.lbl_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #ffffff;")

            self.outcome_box.setStyleSheet(
                f"background-color: {self.cfg['pill_bg']}; "
                f"border: 1px solid {self.cfg['pill_border']}; "
                f"border-radius: 8px;"
            )
            self.outcome_icon.setPixmap(_render_timeline_svg(self.cfg["pill_icon"], self.cfg["icon_color"], 14))

            # Format outcome text with crisp highlight spans
            display_outcome = outcome_summary or "Completed successfully."
            if "Identified Gateway:" in display_outcome and " on " in display_outcome:
                parts = display_outcome.split("Identified Gateway: ")[1].split(" on ")
                gw_val = parts[0]
                iface_val = parts[1] if len(parts) > 1 else ""
                display_outcome = f"Identified Gateway: <b style='color:#1d4ed8;'>{gw_val}</b> on <b style='color:#1d4ed8;'>{iface_val}</b>"
            elif "Gateway Median:" in display_outcome:
                display_outcome = display_outcome.replace("Gateway Median: ", "Gateway Median: <b style='color:#15803d;'>").replace(" ms", " ms</b>")
            elif "External Median:" in display_outcome:
                display_outcome = display_outcome.replace("External Median: ", "External Median: <b style='color:#6d28d9;'>").replace(" ms", " ms</b>")

            self.lbl_outcome.setText(display_outcome)
            self.lbl_outcome.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {self.cfg['pill_text_color']};")

        elif status_val == "SKIPPED":
            reason = stage.skip_reason or "Precondition not met"
            self.status_badge_frame.setStyleSheet("background-color: #f59e0b; border-radius: 10px; padding: 2px 8px;")
            self.status_check_icon.setVisible(False)
            self.lbl_status.setText("SKIPPED")
            self.lbl_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #ffffff;")

            self.outcome_box.setStyleSheet(
                "background-color: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;"
            )
            self.outcome_icon.setPixmap(_render_timeline_svg("reachability", "#d97706", 14))
            self.lbl_outcome.setText(f"Skipped: {reason}")
            self.lbl_outcome.setStyleSheet("font-size: 11px; font-weight: 600; color: #b45309;")

        elif status_val in ("FAILED", "PARTIAL"):
            err = stage.error_message or "Execution failed"
            self.status_badge_frame.setStyleSheet("background-color: #ef4444; border-radius: 10px; padding: 2px 8px;")
            self.status_check_icon.setVisible(False)
            self.lbl_status.setText("FAILED")
            self.lbl_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #ffffff;")

            self.outcome_box.setStyleSheet(
                "background-color: #fef2f2; border: 1px solid #fca5a5; border-radius: 8px;"
            )
            self.outcome_icon.setPixmap(_render_timeline_svg("reachability", "#dc2626", 14))
            self.lbl_outcome.setText(f"Failed: {err}")
            self.lbl_outcome.setStyleSheet("font-size: 11px; font-weight: 600; color: #b91c1c;")

        else:
            self.lbl_status.setText(status_val)
            self.lbl_outcome.setText(outcome_summary or "In progress or pending...")


class DiagnosticWorkflowTimeline(QFrame):
    """Composite visual container rendering the sequential 5-stage diagnostic workflow."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "background-color: #ffffff; "
            "border: 1px solid #e2e8f0; "
            "border-radius: 14px;"
        )
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(18, 16, 18, 18)
        self.main_layout.setSpacing(14)

        # -------------------------------------------------------------------
        # Header: Icon + Title + Pipeline Badge
        # -------------------------------------------------------------------
        header_widget = QWidget(self)
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(10)

        header_icon = QLabel(header_widget)
        header_icon.setPixmap(_render_timeline_svg("pipeline", "#2563eb", 20))
        h_layout.addWidget(header_icon)

        title_lbl = QLabel("Diagnostic Workflow Stage Timeline", header_widget)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #0f172a;")
        h_layout.addWidget(title_lbl)

        badge_lbl = QLabel("5-Stage Sequential Pipeline | Execution History", header_widget)
        badge_lbl.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #2563eb; "
            "background-color: #eff6ff; border: 1px solid #dbeafe; "
            "padding: 3px 10px; border-radius: 12px;"
        )
        h_layout.addWidget(badge_lbl)
        h_layout.addStretch()
        self.main_layout.addWidget(header_widget)

        # -------------------------------------------------------------------
        # Stage Cards Grid:
        # Row 0: Stages 1-3 in an evenly spaced three-column grid
        # Row 1: Stages 4-5 in a balanced two-column row
        # -------------------------------------------------------------------
        grid_widget = QWidget(self)
        self.grid = QGridLayout(grid_widget)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setSpacing(12)

        # 6 equal columns so 3 items take 2 slots each, and 2 items take 3 slots each
        for col in range(6):
            self.grid.setColumnStretch(col, 1)

        self.stage_cards: Dict[int, StageStepCard] = {}
        for order, stage_num, name, desc in STAGE_DEFINITIONS:
            card = StageStepCard(order, stage_num, name, desc, grid_widget)
            self.stage_cards[order] = card

        # Place Stages 1, 2, 3 in Row 0 (each spanning 2 columns -> exactly 3 equal columns)
        self.grid.addWidget(self.stage_cards[1], 0, 0, 1, 2)
        self.grid.addWidget(self.stage_cards[2], 0, 2, 1, 2)
        self.grid.addWidget(self.stage_cards[3], 0, 4, 1, 2)

        # Place Stages 4, 5 in Row 1 (each spanning 3 columns -> exactly 2 equal columns)
        self.grid.addWidget(self.stage_cards[4], 1, 0, 1, 3)
        self.grid.addWidget(self.stage_cards[5], 1, 3, 1, 3)

        self.main_layout.addWidget(grid_widget)

    def populate_timeline(
        self,
        stages: List[TestStage],
        run: Optional[TestRun] = None,
        summaries: Optional[List[Summary]] = None,
        observations: Optional[List[Observation]] = None,
    ) -> None:
        """Populate each stage card with real execution data from SQLite."""
        stages_by_order: Dict[int, TestStage] = {s.stage_order: s for s in stages}

        # Derive empirical measurement outcomes per stage
        gw_ip = run.gateway_ip if run and run.gateway_ip else "--"
        iface = run.interface_name if run and run.interface_name else "--"

        # S1 summary
        s1_summary = f"Identified Gateway: {gw_ip} on {iface}" if gw_ip != "--" else "Gateway discovery attempted"

        # Identify gateway target IDs
        gw_tids = set()
        if run and run.gateway_ip:
            gw_tids.add(run.gateway_ip)
        for o in (observations or []):
            if o.metric_name == "gateway_discovery" and o.target_id:
                gw_tids.add(o.target_id)

        def _is_summary_gw(s_tid: Optional[str]) -> bool:
            if not s_tid:
                return False
            if s_tid in gw_tids or "gw" in s_tid.lower() or "gateway" in s_tid.lower():
                return True
            return not any(k in s_tid.lower() for k in ("ext-", "dns-", "tp-", "cloudflare", "google", "quad9"))

        # S2 summary (Gateway RTT & Loss)
        s2_summary = "Gateway reachability evaluated"
        if summaries:
            s_gw_loss = next((s for s in summaries if _is_summary_gw(s.target_id) and s.metric_name == "packet_loss"), None)
            s_gw_rtt = next((s for s in summaries if _is_summary_gw(s.target_id) and s.metric_name == "round_trip_time" and s.statistic_name == "median"), None)
            if s_gw_rtt and s_gw_rtt.value_numeric is not None:
                loss_str = f"{s_gw_loss.value_numeric:.1f}% loss" if s_gw_loss and s_gw_loss.value_numeric is not None else "0.0% loss"
                s2_summary = f"Gateway Median: {s_gw_rtt.value_numeric:.1f} ms ({loss_str})"

        # S3 summary (External Multi-Target Ping)
        s3_summary = "External endpoints probed"
        if summaries:
            ext_rtts = [s.value_numeric for s in summaries if not _is_summary_gw(s.target_id) and s.metric_name == "round_trip_time" and s.statistic_name == "median" and s.value_numeric is not None]
            ext_losses = [s.value_numeric for s in summaries if not _is_summary_gw(s.target_id) and s.metric_name == "packet_loss" and s.value_numeric is not None]
            if ext_rtts:
                avg_rtt = sum(ext_rtts) / len(ext_rtts)
                avg_loss = (sum(ext_losses) / len(ext_losses)) if ext_losses else 0.0
                s3_summary = f"External Median: {avg_rtt:.1f} ms across {len(ext_rtts)} endpoints ({avg_loss:.1f}% loss)"

        # S4 summary (DNS Resolution)
        s4_summary = "DNS query timing evaluated"
        if summaries:
            s_dns = next((s for s in summaries if s.metric_name == "dns_resolution_time" and s.statistic_name == "median"), None)
            if s_dns and s_dns.value_numeric is not None:
                s4_summary = f"Resolver Response: {s_dns.value_numeric:.1f} ms (Port 53 UDP)"

        # S5 summary (Loaded Latency / Bufferbloat)
        s5_summary = "Loaded latency evaluated"
        if summaries:
            s_bb = next((s for s in summaries if "bufferbloat" in s.metric_name.lower() or "loaded" in s.metric_name.lower()), None)
            if s_bb and s_bb.value_numeric is not None:
                s5_summary = f"Bufferbloat Delta: +{s_bb.value_numeric:.1f} ms under load"
            elif stages_by_order.get(5) and stages_by_order[5].status == StageStatus.SKIPPED:
                s5_summary = f"Skipped: {stages_by_order[5].skip_reason or 'No user consent / skipped'}"

        outcomes = {
            1: s1_summary,
            2: s2_summary,
            3: s3_summary,
            4: s4_summary,
            5: s5_summary,
        }

        for order, card in self.stage_cards.items():
            stg = stages_by_order.get(order)
            card.set_stage_data(stg, outcomes.get(order, ""))
