"""Interactive Overview and Architectural Walkthrough Dialog for TicNet.

Provides an intuitive, guided 4-step interactive presentation explaining:
1. Two-Checkpoint Fault Isolation (Host -> Gateway vs Router -> WAN)
2. Deterministic Latency & Loss Engine (High-frequency ICMP probing)
3. Bufferbloat & Loaded Latency Analysis
4. Plain-English Verdicts, Confidence Ratings & Exportable Reports

Features:
- Step-by-step interactive navigation with visual diagrams
- Auto-play / pause toggle mode (2-minute guided overview)
- Direct CTA to start a live diagnosis run
- Polished SaaS aesthetic matching TicNet light theme
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class OverviewWalkthroughDialog(QDialog):
    """Interactive guided walkthrough modal for TicNet architecture and diagnosis workflow."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        on_start_diagnosis: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TicNet — Platform Overview & Guided Walkthrough")
        self.setModal(True)
        self.resize(840, 650)
        self.setMinimumSize(780, 600)

        self.on_start_diagnosis = on_start_diagnosis
        self._current_step: int = 0
        self._total_steps: int = 4
        self._is_playing: bool = False
        self._auto_play_timer = QTimer(self)
        self._auto_play_timer.setInterval(4500)  # 4.5 seconds per slide
        self._auto_play_timer.timeout.connect(self._on_auto_advance)

        self._setup_ui()
        self._update_step_view()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #f8fafc;
                font-family: 'Segoe UI', -apple-system, sans-serif;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 18, 24, 18)
        main_layout.setSpacing(10)

        # -------------------------------------------------------------------
        # Header Row: Badge, Title, Close Button
        # -------------------------------------------------------------------
        header_widget = QWidget(self)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        lbl_badge = QLabel("▶  PLATFORM OVERVIEW", header_widget)
        lbl_badge.setStyleSheet("""
            background-color: #eff6ff;
            color: #2563eb;
            font-size: 11px;
            font-weight: 800;
            padding: 3px 10px;
            border-radius: 10px;
            border: 1px solid #bfdbfe;
        """)
        badge_row.addWidget(lbl_badge)

        self.lbl_step_counter = QLabel("Step 1 of 4", header_widget)
        self.lbl_step_counter.setStyleSheet("font-size: 11px; font-weight: 700; color: #64748b;")
        badge_row.addWidget(self.lbl_step_counter)
        badge_row.addStretch()
        title_col.addLayout(badge_row)

        lbl_modal_title = QLabel("How TicNet Diagnoses Network Connectivity", header_widget)
        lbl_modal_title.setStyleSheet("font-size: 20px; font-weight: 800; color: #0f172a;")
        title_col.addWidget(lbl_modal_title)

        header_layout.addLayout(title_col, 1)

        btn_close = QPushButton("✕", header_widget)
        btn_close.setFixedSize(32, 32)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #64748b;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #fee2e2;
                color: #ef4444;
                border-color: #fca5a5;
            }
        """)
        btn_close.clicked.connect(self.reject)
        header_layout.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignTop)

        main_layout.addWidget(header_widget)

        # -------------------------------------------------------------------
        # Slides Stack (Card Container)
        # -------------------------------------------------------------------
        self.slide_card = QFrame(self)
        self.slide_card.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
            }
        """)
        card_shadow = QGraphicsDropShadowEffect(self.slide_card)
        card_shadow.setBlurRadius(20)
        card_shadow.setOffset(0, 4)
        card_shadow.setColor(QColor(0, 0, 0, 14))
        self.slide_card.setGraphicsEffect(card_shadow)

        card_layout = QVBoxLayout(self.slide_card)
        card_layout.setContentsMargins(24, 20, 24, 20)

        self.stack = QStackedWidget(self.slide_card)
        self.stack.addWidget(self._create_slide_checkpoint_isolation())
        self.stack.addWidget(self._create_slide_probing_engine())
        self.stack.addWidget(self._create_slide_bufferbloat())
        self.stack.addWidget(self._create_slide_verdicts_and_reports())
        card_layout.addWidget(self.stack)

        main_layout.addWidget(self.slide_card, 1)

        # Progress bar showing slide timer / reading progress
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, self._total_steps)
        self.progress_bar.setValue(1)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #e2e8f0;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 2px;
            }
        """)
        main_layout.addWidget(self.progress_bar)

        # -------------------------------------------------------------------
        # Bottom Controls: Dots, Auto-play, Previous, Next, Start Diagnosis
        # -------------------------------------------------------------------
        footer_widget = QWidget(self)
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(0, 4, 0, 0)
        footer_layout.setSpacing(10)

        # Auto Play toggle
        self.btn_autoplay = QPushButton("▶ Auto-Play (2 min)", footer_widget)
        self.btn_autoplay.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_autoplay.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #334155;
                font-size: 12px;
                font-weight: 700;
                padding: 7px 14px;
                border: 1px solid #cbd5e1;
                border-radius: 16px;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
            }
        """)
        self.btn_autoplay.clicked.connect(self._toggle_autoplay)
        footer_layout.addWidget(self.btn_autoplay)

        footer_layout.addStretch()

        # Previous button
        self.btn_prev = QPushButton("‹  Previous", footer_widget)
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #475569;
                font-size: 13px;
                font-weight: 600;
                padding: 8px 16px;
                border: 1px solid #cbd5e1;
                border-radius: 18px;
            }
            QPushButton:hover {
                background-color: #f8fafc;
            }
            QPushButton:disabled {
                color: #cbd5e1;
                border-color: #e2e8f0;
            }
        """)
        self.btn_prev.clicked.connect(self._on_prev_clicked)
        footer_layout.addWidget(self.btn_prev)

        # Next button
        self.btn_next = QPushButton("Next  ›", footer_widget)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 20px;
                border: none;
                border-radius: 18px;
            }
            QPushButton:hover {
                background-color: #1e293b;
            }
        """)
        self.btn_next.clicked.connect(self._on_next_clicked)
        footer_layout.addWidget(self.btn_next)

        # Start Diagnosis CTA
        self.btn_start_diag = QPushButton("Start Diagnosis  →", footer_widget)
        self.btn_start_diag.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_diag.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 18px;
                border: none;
                border-radius: 18px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        self.btn_start_diag.clicked.connect(self._on_start_diag_clicked)
        footer_layout.addWidget(self.btn_start_diag)

        main_layout.addWidget(footer_widget)

    # -----------------------------------------------------------------------
    # Slide Builders
    # -----------------------------------------------------------------------
    def _create_slide_checkpoint_isolation(self) -> QWidget:
        """Slide 1: Two-Checkpoint Connectivity Fault Isolation."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(14)

        title = QLabel("1. Two-Checkpoint Connectivity Isolation", w)
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #0f172a;")
        l.addWidget(title)

        desc = QLabel(
            "When internet feels sluggish or cuts out, is the fault inside your house or upstream with your ISP?\n"
            "TicNet isolates root causes by simultaneously interrogating two distinct network boundaries:",
            w,
        )
        desc.setStyleSheet("font-size: 13px; color: #475569; line-height: 1.4;")
        desc.setWordWrap(True)
        l.addWidget(desc)

        # Diagram frame
        diag = QFrame(w)
        diag.setStyleSheet("background-color: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 12px;")
        dl = QHBoxLayout(diag)
        dl.setContentsMargins(12, 12, 12, 12)
        dl.setSpacing(8)

        # Node A: Host Device
        node_host = self._create_diagram_node("💻 Host PC", "Local Adapter", "192.168.1.105", "#0284c7")
        dl.addWidget(node_host, 1)

        # Path 1
        path1 = self._create_diagram_arrow("Checkpoint 1\nLocal Gateway", "Wi-Fi / LAN\n< 5 ms")
        dl.addWidget(path1, 0)

        # Node B: Router / Gateway
        node_gw = self._create_diagram_node("🛜 Router", "Default Gateway", "192.168.1.1", "#059669")
        dl.addWidget(node_gw, 1)

        # Path 2
        path2 = self._create_diagram_arrow("Checkpoint 2\nExternal WAN", "Fiber / Cable\nUpstream ISP")
        dl.addWidget(path2, 0)

        # Node C: External Internet
        node_ext = self._create_diagram_node("🌐 External Anycast", "Cloudflare & Google", "1.1.1.1 / 8.8.8.8", "#7c3aed")
        dl.addWidget(node_ext, 1)

        l.addWidget(diag)

        takeaway = QLabel(
            "💡 Verdict Logic: If Checkpoint 1 degrades, your Wi-Fi or router cable is failing.\n"
            "If Checkpoint 1 is pristine but Checkpoint 2 exhibits packet loss, the issue is confirmed outside your home.",
            w,
        )
        takeaway.setStyleSheet("font-size: 12px; font-weight: 600; color: #0369a1; background-color: #e0f2fe; padding: 10px; border-radius: 8px;")
        takeaway.setWordWrap(True)
        l.addWidget(takeaway)
        l.addStretch()
        return w

    def _create_slide_probing_engine(self) -> QWidget:
        """Slide 2: Deterministic Latency & Packet-Loss Probing."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(14)

        title = QLabel("2. Deterministic Latency & Loss Engine", w)
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #0f172a;")
        l.addWidget(title)

        desc = QLabel(
            "TicNet operates with ZERO synthetic telemetry and zero mocked measurements. All metrics are "
            "derived directly from native, millisecond-precision ICMP socket probes dispatched at steady cadences.",
            w,
        )
        desc.setStyleSheet("font-size: 13px; color: #475569;")
        desc.setWordWrap(True)
        l.addWidget(desc)

        # Features grid
        f_frame = QFrame(w)
        f_frame.setStyleSheet("background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 12px;")
        fl = QVBoxLayout(f_frame)
        fl.setSpacing(10)

        f1 = QLabel("• <b>High-Precision Time-Series Tracking:</b> Real millisecond latency graphed over time without smoothing distortion.", f_frame)
        f1.setStyleSheet("font-size: 12px; color: #1e293b;")
        fl.addWidget(f1)

        f2 = QLabel("• <b>Prominent Packet Loss Bars:</b> Timed-out probes are visually rendered as vertical red drop bars, never hidden or forced to 0 ms.", f_frame)
        f2.setStyleSheet("font-size: 12px; color: #1e293b;")
        fl.addWidget(f2)

        f3 = QLabel("• <b>Dual Target Verification:</b> Probes both Cloudflare (1.1.1.1) and Google (8.8.8.8) to prevent single-provider false positives.", f_frame)
        f3.setStyleSheet("font-size: 12px; color: #1e293b;")
        fl.addWidget(f3)

        f4 = QLabel("• <b>Robust Statistical Modeling:</b> Automatically computes Median RTT, Minimum, Maximum, and MACD Jitter.", f_frame)
        f4.setStyleSheet("font-size: 12px; color: #1e293b;")
        fl.addWidget(f4)

        l.addWidget(f_frame)

        takeaway = QLabel("📊 Scientific Accuracy: Reproducible data you can trust when diagnosing real-world connection drops.", w)
        takeaway.setStyleSheet("font-size: 12px; font-weight: 600; color: #047857; background-color: #ecfdf5; padding: 10px; border-radius: 8px;")
        l.addWidget(takeaway)
        l.addStretch()
        return w

    def _create_slide_bufferbloat(self) -> QWidget:
        """Slide 3: Bufferbloat & Loaded Latency Analysis."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(14)

        title = QLabel("3. Bufferbloat & Loaded Latency Analysis", w)
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #0f172a;")
        l.addWidget(title)

        desc = QLabel(
            "Ever wondered why games freeze or Zoom calls drop the second someone downloads a video? "
            "That's Bufferbloat — router memory buffers becoming congested and holding packets hostage.",
            w,
        )
        desc.setStyleSheet("font-size: 13px; color: #475569;")
        desc.setWordWrap(True)
        l.addWidget(desc)

        bb_card = QFrame(w)
        bb_card.setStyleSheet("background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px;")
        bbl = QHBoxLayout(bb_card)
        bbl.setSpacing(16)

        left_box = QFrame(bb_card)
        left_box.setStyleSheet("background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 10px;")
        lbl = QVBoxLayout(left_box)
        lbl.addWidget(QLabel("🟢 Unloaded Baseline", left_box))
        lbl_rtt = QLabel("14 ms", left_box)
        lbl_rtt.setStyleSheet("font-size: 24px; font-weight: 800; color: #059669;")
        lbl.addWidget(lbl_rtt)
        lbl.addWidget(QLabel("Ideal idle connection latency", left_box))
        bbl.addWidget(left_box, 1)

        arrow_lbl = QLabel("vs", bb_card)
        arrow_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #94a3b8;")
        bbl.addWidget(arrow_lbl, 0)

        right_box = QFrame(bb_card)
        right_box.setStyleSheet("background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 10px;")
        rbl = QVBoxLayout(right_box)
        rbl.addWidget(QLabel("🔴 Loaded Latency (Congestion)", right_box))
        lbl_load = QLabel("+138 ms", right_box)
        lbl_load.setStyleSheet("font-size: 24px; font-weight: 800; color: #dc2626;")
        rbl.addWidget(lbl_load)
        rbl.addWidget(QLabel("Queuing delay under saturated load", right_box))
        bbl.addWidget(right_box, 1)

        l.addWidget(bb_card)

        takeaway = QLabel("⚡ Bufferbloat Grade: TicNet rates your router's queue management (A+ to F) so you know if you need SQM / CAKE.", w)
        takeaway.setStyleSheet("font-size: 12px; font-weight: 600; color: #b45309; background-color: #fffbeb; padding: 10px; border-radius: 8px;")
        l.addWidget(takeaway)
        l.addStretch()
        return w

    def _create_slide_verdicts_and_reports(self) -> QWidget:
        """Slide 4: Plain-English Diagnostic Verdicts & Reports."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(14)

        title = QLabel("4. Plain-English Verdicts & Exportable Reports", w)
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #0f172a;")
        l.addWidget(title)

        desc = QLabel(
            "No deciphering arcane terminal outputs. TicNet translates complex network telemetry into clear, "
            "actionable diagnostic conclusions backed by empirical evidence ratings.",
            w,
        )
        desc.setStyleSheet("font-size: 13px; color: #475569;")
        desc.setWordWrap(True)
        l.addWidget(desc)

        rep_frame = QFrame(w)
        rep_frame.setStyleSheet("background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 12px;")
        rl = QVBoxLayout(rep_frame)
        rl.setSpacing(10)

        v1 = QLabel("✅ <b>Plain-Language Verdict:</b> Instant classification (e.g. 'Wi-Fi Signal Degraded', 'ISP Fiber Packet Loss').", rep_frame)
        v1.setStyleSheet("font-size: 12px; color: #1e293b;")
        rl.addWidget(v1)

        v2 = QLabel("🎯 <b>Confidence Scoring:</b> Empirical evidence ratings show mathematical certainty (e.g., 99.4% Confidence).", rep_frame)
        v2.setStyleSheet("font-size: 12px; color: #1e293b;")
        rl.addWidget(v2)

        v3 = QLabel("📄 <b>Multi-Format Exports:</b> Generate PDF, Markdown, and JSON audit artifacts with cryptographic session IDs.", rep_frame)
        v3.setStyleSheet("font-size: 12px; color: #1e293b;")
        rl.addWidget(v3)

        v4 = QLabel("🛠️ <b>Actionable Steps:</b> Tells you exactly how to fix the issue or what evidence to email to your broadband provider.", rep_frame)
        v4.setStyleSheet("font-size: 12px; color: #1e293b;")
        rl.addWidget(v4)

        l.addWidget(rep_frame)

        takeaway = QLabel("🚀 Ready to test? Click 'Start Diagnosis' below to launch a comprehensive network health inspection!", w)
        takeaway.setStyleSheet("font-size: 12px; font-weight: 700; color: #4338ca; background-color: #e0e7ff; padding: 10px; border-radius: 8px;")
        l.addWidget(takeaway)
        l.addStretch()
        return w

    # -----------------------------------------------------------------------
    # Helper widgets
    # -----------------------------------------------------------------------
    def _create_diagram_node(self, title: str, subtitle: str, detail: str, color_hex: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: #ffffff;
                border: 2px solid {color_hex};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_t = QLabel(title, frame)
        lbl_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_t.setStyleSheet(f"font-size: 13px; font-weight: 800; color: {color_hex}; border: none;")
        layout.addWidget(lbl_t)

        lbl_s = QLabel(subtitle, frame)
        lbl_s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_s.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569; border: none;")
        layout.addWidget(lbl_s)

        lbl_d = QLabel(detail, frame)
        lbl_d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_d.setStyleSheet("font-size: 10px; color: #94a3b8; font-family: Consolas, monospace; border: none;")
        layout.addWidget(lbl_d)

        return frame

    def _create_diagram_arrow(self, top_txt: str, btm_txt: str) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(2)
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)

        t_lbl = QLabel(top_txt, w)
        t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #2563eb;")
        l.addWidget(t_lbl)

        arr = QLabel("──────▶", w)
        arr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arr.setStyleSheet("font-size: 14px; font-weight: bold; color: #cbd5e1;")
        l.addWidget(arr)

        b_lbl = QLabel(btm_txt, w)
        b_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b_lbl.setStyleSheet("font-size: 10px; color: #64748b;")
        l.addWidget(b_lbl)

        return w

    # -----------------------------------------------------------------------
    # Navigation & Animation Handlers
    # -----------------------------------------------------------------------
    def _update_step_view(self) -> None:
        self.stack.setCurrentIndex(self._current_step)
        self.lbl_step_counter.setText(f"Step {self._current_step + 1} of {self._total_steps}")
        self.progress_bar.setValue(self._current_step + 1)
        self.btn_prev.setEnabled(self._current_step > 0)
        if self._current_step == self._total_steps - 1:
            self.btn_next.setText("Finish  ✓")
        else:
            self.btn_next.setText("Next  ›")

    def _on_prev_clicked(self) -> None:
        if self._current_step > 0:
            self._current_step -= 1
            self._update_step_view()

    def _on_next_clicked(self) -> None:
        if self._current_step < self._total_steps - 1:
            self._current_step += 1
            self._update_step_view()
        else:
            self._stop_autoplay()
            self.accept()

    def _toggle_autoplay(self) -> None:
        if self._is_playing:
            self._stop_autoplay()
        else:
            self._start_autoplay()

    def _start_autoplay(self) -> None:
        self._is_playing = True
        self.btn_autoplay.setText("⏸ Pause")
        self.btn_autoplay.setStyleSheet("""
            QPushButton {
                background-color: #eff6ff;
                color: #2563eb;
                font-size: 12px;
                font-weight: 700;
                padding: 7px 14px;
                border: 1px solid #bfdbfe;
                border-radius: 16px;
            }
        """)
        self._auto_play_timer.start()

    def _stop_autoplay(self) -> None:
        self._is_playing = False
        self.btn_autoplay.setText("▶ Auto-Play (2 min)")
        self.btn_autoplay.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #334155;
                font-size: 12px;
                font-weight: 700;
                padding: 7px 14px;
                border: 1px solid #cbd5e1;
                border-radius: 16px;
            }
        """)
        if self._auto_play_timer.isActive():
            self._auto_play_timer.stop()

    def _on_auto_advance(self) -> None:
        if self._current_step < self._total_steps - 1:
            self._current_step += 1
            self._update_step_view()
        else:
            # Loop back or finish
            self._stop_autoplay()

    def _on_start_diag_clicked(self) -> None:
        self._stop_autoplay()
        self.accept()
        if self.on_start_diagnosis:
            self.on_start_diagnosis()

    def closeEvent(self, event) -> None:
        self._stop_autoplay()
        super().closeEvent(event)
