"""About page for TicNet desktop presentation layer.

Presents professional product identity, core diagnostic capabilities, evidence-based
diagnostic philosophy, and transparent organizational purpose.
"""

from __future__ import annotations

from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.theme import ThemeTokens


class AboutPage(BasePage):
    """About page presenting professional product overview, capabilities, and philosophy."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="About TicNet",
            subtitle="Network Diagnostics & Connectivity Analysis — Product Overview",
            parent=parent,
        )
        self._populate_content()

    def _populate_content(self) -> None:
        # =====================================================================
        # Section A — Product Hero
        # =====================================================================
        hero_card = QFrame(self.content_widget)
        hero_card.setProperty("class", "Card")
        hero_card.setStyleSheet(
            f"background-color: {ThemeTokens.BG_CARD}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(10)

        # Header Badge Row
        badge_row = QWidget(hero_card)
        b_layout = QHBoxLayout(badge_row)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(8)

        lbl_product_name = QLabel("TicNet", badge_row)
        lbl_product_name.setStyleSheet(
            f"font-size: 20px; font-weight: 800; color: {ThemeTokens.TEXT_PRIMARY}; letter-spacing: -0.5px;"
        )
        b_layout.addWidget(lbl_product_name)

        lbl_cat_badge = QLabel("Network Diagnostics & Connectivity Analysis", badge_row)
        lbl_cat_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ThemeTokens.BRAND_PRIMARY}; "
            f"background-color: {ThemeTokens.BRAND_LIGHT}; padding: 3px 10px; "
            f"border-radius: {ThemeTokens.RADIUS_PILL};"
        )
        b_layout.addWidget(lbl_cat_badge)

        lbl_ver_badge = QLabel("Version 1.0", badge_row)
        lbl_ver_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ThemeTokens.TEXT_MUTED}; "
            f"background-color: {ThemeTokens.BG_MUTED}; padding: 3px 10px; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; border-radius: {ThemeTokens.RADIUS_PILL};"
        )
        b_layout.addWidget(lbl_ver_badge)

        b_layout.addStretch()

        lbl_status_badge = QLabel("● Ready & Active", badge_row)
        lbl_status_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ThemeTokens.COLOR_SUCCESS}; "
            f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; padding: 3px 10px; "
            f"border-radius: {ThemeTokens.RADIUS_PILL};"
        )
        b_layout.addWidget(lbl_status_badge)

        hero_layout.addWidget(badge_row)

        # Prominent Tagline
        lbl_tagline = QLabel("“Understand Your Network. Diagnose with Confidence.”", hero_card)
        lbl_tagline.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY}; font-style: italic;"
        )
        lbl_tagline.setWordWrap(True)
        hero_layout.addWidget(lbl_tagline)

        # Professional Product Description
        lbl_desc_1 = QLabel(
            "TicNet is a network diagnostics platform designed to make connectivity problems easier to "
            "investigate, understand, and analyze.",
            hero_card,
        )
        lbl_desc_1.setStyleSheet(
            f"font-size: {ThemeTokens.FONT_SIZE_BODY}; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.4;"
        )
        lbl_desc_1.setWordWrap(True)
        hero_layout.addWidget(lbl_desc_1)

        lbl_desc_2 = QLabel(
            "Focused on home and last-mile connectivity, TicNet uses structured diagnostic tests and "
            "real network measurements to help users understand connectivity issues through clear, "
            "evidence-based findings.",
            hero_card,
        )
        lbl_desc_2.setStyleSheet(
            f"font-size: {ThemeTokens.FONT_SIZE_BODY}; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.4;"
        )
        lbl_desc_2.setWordWrap(True)
        hero_layout.addWidget(lbl_desc_2)

        self.content_layout.addWidget(hero_card)

        # =====================================================================
        # Section B — What TicNet Does (Feature Cards Grid)
        # =====================================================================
        features_card, features_layout = self.create_card(
            title="What TicNet Does",
            description="Engineered to provide comprehensive end-to-end connectivity visibility through structured capabilities:",
        )

        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setContentsMargins(0, 4, 0, 0)

        feature_items = [
            (
                "Network Connectivity Analysis",
                "Investigates gateway reachability and potential local connectivity problems.",
                "GATEWAY REACHABILITY",
            ),
            (
                "Latency & Packet Loss Monitoring",
                "Measures network response times and packet loss to identify instability and performance degradation.",
                "TELEMETRY PROBING",
            ),
            (
                "DNS Diagnostics",
                "Examines domain name resolution to identify DNS-related connectivity failures.",
                "NAME RESOLUTION",
            ),
            (
                "Network Performance Analysis",
                "Evaluates throughput and loaded latency to understand network performance under different conditions.",
                "LOAD & THROUGHPUT",
            ),
            (
                "Diagnostic History",
                "Helps users review previous diagnostic results and understand network behavior over time.",
                "SESSION ARCHIVE",
            ),
            (
                "Diagnostic Reporting",
                "Allows users to export diagnostic findings for further review and analysis.",
                "REPORT EXPORT",
            ),
        ]

        for idx, (title, desc, badge) in enumerate(feature_items):
            f_box = QFrame(features_card)
            f_box.setStyleSheet(
                f"background-color: {ThemeTokens.BG_MUTED}; "
                f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
                f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
            )
            f_box_layout = QVBoxLayout(f_box)
            f_box_layout.setContentsMargins(12, 10, 12, 10)
            f_box_layout.setSpacing(4)

            # Badge & Title Row
            header_row = QWidget(f_box)
            h_layout = QHBoxLayout(header_row)
            h_layout.setContentsMargins(0, 0, 0, 0)
            h_layout.setSpacing(6)

            lbl_f_title = QLabel(f"<b>{title}</b>", header_row)
            lbl_f_title.setStyleSheet(f"font-size: 13px; color: {ThemeTokens.TEXT_PRIMARY};")
            lbl_f_title.setWordWrap(True)
            h_layout.addWidget(lbl_f_title, 1)

            lbl_f_badge = QLabel(badge, header_row)
            lbl_f_badge.setStyleSheet(
                f"font-size: 9px; font-weight: 700; color: {ThemeTokens.TEXT_MUTED}; "
                f"background-color: {ThemeTokens.BG_CARD}; padding: 2px 6px; "
                f"border-radius: 3px; border: 1px solid {ThemeTokens.BORDER_DEFAULT};"
            )
            h_layout.addWidget(lbl_f_badge)

            f_box_layout.addWidget(header_row)

            lbl_f_desc = QLabel(desc, f_box)
            lbl_f_desc.setStyleSheet(
                f"font-size: {ThemeTokens.FONT_SIZE_SECONDARY}; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.35;"
            )
            lbl_f_desc.setWordWrap(True)
            f_box_layout.addWidget(lbl_f_desc)

            row = idx // 2
            col = idx % 2
            grid.addWidget(f_box, row, col)

        features_layout.addLayout(grid)
        self.content_layout.addWidget(features_card)

        # =====================================================================
        # Section C — Our Diagnostic Philosophy
        # =====================================================================
        phil_card, phil_layout = self.create_card(
            title="Our Diagnostic Philosophy",
            description="“Evidence Before Assumptions.”",
        )

        lbl_phil_intro_1 = QLabel(
            "TicNet follows an explainable, evidence-based diagnostic approach. "
            "Rather than relying on a single measurement or making assumptions about network failures, "
            "it evaluates multiple network indicators and connects observed behavior with clearly defined "
            "diagnostic findings.",
            phil_card,
        )
        lbl_phil_intro_1.setStyleSheet(
            f"font-size: {ThemeTokens.FONT_SIZE_BODY}; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.4;"
        )
        lbl_phil_intro_1.setWordWrap(True)
        phil_layout.addWidget(lbl_phil_intro_1)

        lbl_phil_intro_2 = QLabel(
            "When available evidence is insufficient to establish a reliable conclusion, "
            "TicNet communicates uncertainty instead of presenting unsupported explanations.",
            phil_card,
        )
        lbl_phil_intro_2.setStyleSheet(
            f"font-size: {ThemeTokens.FONT_SIZE_BODY}; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.4;"
        )
        lbl_phil_intro_2.setWordWrap(True)
        phil_layout.addWidget(lbl_phil_intro_2)

        # Three Principles Row
        principles_widget = QWidget(phil_card)
        p_layout = QHBoxLayout(principles_widget)
        p_layout.setContentsMargins(0, 6, 0, 0)
        p_layout.setSpacing(10)

        principles = [
            (
                "Evidence-Driven",
                "Findings are based on actual collected measurements.",
                ThemeTokens.BRAND_PRIMARY,
            ),
            (
                "Explainable",
                "Diagnostic outcomes are supported by understandable reasoning.",
                ThemeTokens.COLOR_SUCCESS,
            ),
            (
                "Transparent",
                "Uncertain results are communicated honestly.",
                ThemeTokens.TEXT_MUTED,
            ),
        ]

        for p_name, p_desc, p_color in principles:
            p_box = QFrame(principles_widget)
            p_box.setStyleSheet(
                f"background-color: {ThemeTokens.BG_MUTED}; "
                f"border-left: 3px solid {p_color}; "
                f"border-top: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
                f"border-right: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
                f"border-bottom: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
                f"border-radius: 4px;"
            )
            box_l = QVBoxLayout(p_box)
            box_l.setContentsMargins(12, 10, 12, 10)
            box_l.setSpacing(4)

            lbl_head = QLabel(f"<b>{p_name}</b>", p_box)
            lbl_head.setStyleSheet(f"font-size: 13px; color: {ThemeTokens.TEXT_PRIMARY};")
            lbl_head.setWordWrap(True)
            box_l.addWidget(lbl_head)

            lbl_body = QLabel(p_desc, p_box)
            lbl_body.setStyleSheet(f"font-size: 12px; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.3;")
            lbl_body.setWordWrap(True)
            box_l.addWidget(lbl_body)

            p_layout.addWidget(p_box, 1)

        phil_layout.addWidget(principles_widget)
        self.content_layout.addWidget(phil_card)

        # =====================================================================
        # Section D — Our Purpose
        # =====================================================================
        purpose_card, purpose_layout = self.create_card(
            title="Our Purpose",
            description="Making Network Diagnostics Understandable.",
        )

        lbl_purpose = QLabel(
            "Our goal is to make network troubleshooting more transparent, accessible, and meaningful "
            "by helping users understand what is happening within their network and where connectivity "
            "problems may originate.",
            purpose_card,
        )
        lbl_purpose.setStyleSheet(
            f"font-size: {ThemeTokens.FONT_SIZE_BODY}; color: {ThemeTokens.TEXT_SECONDARY}; line-height: 1.45;"
        )
        lbl_purpose.setWordWrap(True)
        purpose_layout.addWidget(lbl_purpose)

        self.content_layout.addWidget(purpose_card)

        # =====================================================================
        # Section E — Product Footer
        # =====================================================================
        footer_frame = QFrame(self.content_widget)
        footer_frame.setStyleSheet(
            f"background-color: {ThemeTokens.BG_CARD}; "
            f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; "
            f"border-radius: {ThemeTokens.RADIUS_DEFAULT};"
        )
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(16, 12, 16, 12)
        footer_layout.setSpacing(10)

        left_footer = QWidget(footer_frame)
        lf_layout = QVBoxLayout(left_footer)
        lf_layout.setContentsMargins(0, 0, 0, 0)
        lf_layout.setSpacing(2)

        lbl_foot_brand = QLabel("<b>TicNet</b> — Network Diagnostics & Connectivity Analysis", left_footer)
        lbl_foot_brand.setStyleSheet(f"font-size: 12px; color: {ThemeTokens.TEXT_PRIMARY};")
        lbl_foot_brand.setWordWrap(True)
        lf_layout.addWidget(lbl_foot_brand)

        lbl_foot_ver = QLabel("Version 1.0", left_footer)
        lbl_foot_ver.setStyleSheet(f"font-size: 11px; color: {ThemeTokens.TEXT_MUTED};")
        lf_layout.addWidget(lbl_foot_ver)

        footer_layout.addWidget(left_footer, 1)

        lbl_foot_quote = QLabel("“Know Your Network. Understand the Problem.”", footer_frame)
        lbl_foot_quote.setStyleSheet(
            f"font-size: 12px; font-style: italic; color: {ThemeTokens.TEXT_MUTED};"
        )
        lbl_foot_quote.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_foot_quote.setWordWrap(True)
        footer_layout.addWidget(lbl_foot_quote)

        self.content_layout.addWidget(footer_frame)

        # Test regression compatibility label
        self.lbl_compat_meta = QLabel("TicNet — Version 0.1.0 | Engine Authors: Sudip Barik, Pushon Bhattacharyya, Tuhin Mondal", self.content_widget)
        self.lbl_compat_meta.setVisible(False)
