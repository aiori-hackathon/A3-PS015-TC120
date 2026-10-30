"""Desktop styling system, design tokens, and Qt Style Sheets (QSS) for TicNet.

Provides a polished, professional network-monitoring appearance inspired by
PingPlotter and modern diagnostic consoles with high-contrast dark surfaces,
crisp typography, and clear visual hierarchy.
"""

from __future__ import annotations


class ThemeTokens:
    """Color, spacing, and font tokens for the desktop application."""

    # Surfaces (Clean modern SaaS palette matching reference image)
    BG_WINDOW = "#f4f6fb"
    BG_SIDEBAR = "#ffffff"
    BG_SIDEBAR_HOVER = "#f1f5f9"
    BG_SIDEBAR_ACTIVE = "#eff6ff"
    BG_CARD = "#ffffff"
    BG_MUTED = "#f8fafc"
    BG_INPUT = "#ffffff"

    # Borders
    BORDER_DEFAULT = "#e2e8f0"
    BORDER_INPUT = "#cbd5e1"
    BORDER_FOCUS = "#2563eb"

    # Typography
    TEXT_PRIMARY = "#0f172a"
    TEXT_SECONDARY = "#475569"
    TEXT_MUTED = "#64748b"
    TEXT_SIDEBAR = "#64748b"
    TEXT_SIDEBAR_ACTIVE = "#2563eb"
    TEXT_INVERSE = "#ffffff"

    # Typography Sizing Standards (Section 2.C Specification)
    FONT_SIZE_PAGE_TITLE = "22px"      # Page title (20-24px)
    FONT_SIZE_SECTION_TITLE = "16px"   # Section heading (15-17px)
    FONT_SIZE_CARD_TITLE = "14px"      # Card heading (13-15px)
    FONT_SIZE_METRIC = "24px"          # Main metric (22-28px)
    FONT_SIZE_BODY = "13px"            # Body text (12-14px)
    FONT_SIZE_SECONDARY = "12px"       # Secondary text (11-12px)
    FONT_SIZE_CAPTION = "11px"         # Captions / metadata
    FONT_SIZE_TABLE = "12px"           # Table content (11-13px)
    FONT_SIZE_BUTTON = "13px"          # Buttons (12-14px)

    # Accents & Brand
    BRAND_PRIMARY = "#2563eb"
    BRAND_HOVER = "#1d4ed8"
    BRAND_PRESSED = "#1e40af"
    BRAND_LIGHT = "#eff6ff"

    # Semantic Status Colors (Network monitoring standards)
    COLOR_SUCCESS = "#10b981"
    BG_SUCCESS_LIGHT = "#ecfdf5"
    BORDER_SUCCESS = "#a7f3d0"

    COLOR_WARNING = "#f59e0b"
    BG_WARNING_LIGHT = "#fffbeb"
    BORDER_WARNING = "#fde68a"

    COLOR_ERROR = "#ef4444"
    BG_ERROR_LIGHT = "#fef2f2"
    BORDER_ERROR = "#fecaca"

    COLOR_INFO = "#0284c7"
    BG_INFO_LIGHT = "#f0f9ff"
    BORDER_INFO = "#bae6fd"

    COLOR_INCONCLUSIVE = "#6366f1"
    BG_INCONCLUSIVE_LIGHT = "#eef2ff"
    BORDER_INCONCLUSIVE = "#c7d2fe"

    # Metrics & Sizing
    FONT_FAMILY = '"Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif'
    FONT_MONO = '"Cascadia Code", "Consolas", "Courier New", monospace'
    RADIUS_DEFAULT = "6px"
    RADIUS_LARGE = "8px"
    RADIUS_PILL = "9999px"


def get_application_stylesheet() -> str:
    """Return the global Qt Style Sheet (QSS) for the application."""
    t = ThemeTokens
    return f"""
    QMainWindow {{
        background-color: {t.BG_WINDOW};
    }}

    QWidget {{
        font-family: {t.FONT_FAMILY};
        font-size: {t.FONT_SIZE_BODY};
        color: {t.TEXT_PRIMARY};
    }}

    /* High-contrast accessible tooltips avoiding black-screen / dark-mode fallback bugs */
    QToolTip {{
        background-color: #0f172a;
        color: #ffffff;
        border: 1px solid #334155;
        border-radius: 6px;
        padding: 6px 10px;
        font-family: {t.FONT_FAMILY};
        font-size: {t.FONT_SIZE_SECONDARY};
    }}

    /* Sidebar Navigation */
    #sidebarContainer {{
        background-color: transparent;
        border: none;
    }}

    #sidebarBrandTitle {{
        color: {t.TEXT_PRIMARY};
        font-size: 20px;
        font-weight: 800;
        letter-spacing: -0.5px;
    }}

    #sidebarBrandSubtitle {{
        color: {t.TEXT_MUTED};
        font-size: {t.FONT_SIZE_CAPTION};
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}

    #navButton {{
        text-align: left;
        padding: 8px 12px;
        border: none;
        border-radius: {t.RADIUS_DEFAULT};
        color: {t.TEXT_SIDEBAR};
        font-size: {t.FONT_SIZE_BODY};
        font-weight: 500;
        background-color: transparent;
    }}

    #navButton:hover {{
        background-color: {t.BG_SIDEBAR_HOVER};
        color: {t.TEXT_PRIMARY};
    }}

    #navButton:checked {{
        background-color: #18181b;
        color: #ffffff;
        border-radius: 18px;
        font-weight: 600;
    }}

    /* Main Header */
    #headerContainer {{
        background-color: {t.BG_CARD};
        border-bottom: 1px solid {t.BORDER_DEFAULT};
    }}

    #pageTitle {{
        font-size: {t.FONT_SIZE_PAGE_TITLE};
        font-weight: 700;
        color: {t.TEXT_PRIMARY};
        letter-spacing: -0.3px;
    }}

    #pageSubtitle {{
        font-size: {t.FONT_SIZE_SECONDARY};
        color: {t.TEXT_SECONDARY};
    }}

    /* Content Area & Cards */
    #contentScroll {{
        background-color: {t.BG_WINDOW};
        border: none;
    }}

    #contentContainer {{
        background-color: {t.BG_WINDOW};
    }}

    .Card {{
        background-color: {t.BG_CARD};
        border: 1px solid {t.BORDER_DEFAULT};
        border-radius: {t.RADIUS_LARGE};
    }}

    .CardTitle {{
        font-size: {t.FONT_SIZE_SECTION_TITLE};
        font-weight: 700;
        color: {t.TEXT_PRIMARY};
        margin-bottom: 4px;
    }}

    .CardDescription {{
        font-size: {t.FONT_SIZE_BODY};
        color: {t.TEXT_SECONDARY};
        line-height: 1.35;
    }}

    /* Honest Empty State */
    .EmptyStateContainer {{
        background-color: {t.BG_CARD};
        border: 1px dashed #cbd5e1;
        border-radius: {t.RADIUS_LARGE};
        padding: 24px 16px;
    }}

    .EmptyStateTitle {{
        font-size: {t.FONT_SIZE_SECTION_TITLE};
        font-weight: 600;
        color: {t.TEXT_PRIMARY};
    }}

    .EmptyStateText {{
        font-size: {t.FONT_SIZE_SECONDARY};
        color: {t.TEXT_SECONDARY};
        max-width: 520px;
    }}

    /* Buttons */
    QPushButton {{
        font-family: {t.FONT_FAMILY};
        font-size: {t.FONT_SIZE_BUTTON};
        font-weight: 500;
        padding: 6px 14px;
        border-radius: {t.RADIUS_DEFAULT};
        border: 1px solid {t.BORDER_INPUT};
        background-color: #ffffff;
        color: {t.TEXT_PRIMARY};
    }}

    QPushButton:hover {{
        background-color: #f8fafc;
        border-color: #94a3b8;
    }}

    QPushButton:pressed {{
        background-color: #f1f5f9;
    }}

    QPushButton:disabled {{
        background-color: #f8fafc;
        color: {t.TEXT_MUTED};
        border-color: {t.BORDER_DEFAULT};
    }}

    #primaryButton {{
        background-color: {t.BRAND_PRIMARY};
        border: 1px solid {t.BRAND_PRIMARY};
        color: #ffffff;
        font-weight: 600;
    }}

    #primaryButton:hover {{
        background-color: {t.BRAND_HOVER};
        border-color: {t.BRAND_HOVER};
    }}

    #primaryButton:pressed {{
        background-color: {t.BRAND_PRESSED};
        border-color: {t.BRAND_PRESSED};
    }}

    #primaryButton:disabled {{
        background-color: #93c5fd;
        border-color: #93c5fd;
        color: #ffffff;
    }}

    #dangerButton {{
        background-color: {t.COLOR_ERROR};
        border: 1px solid {t.COLOR_ERROR};
        color: #ffffff;
        font-weight: 600;
    }}

    #dangerButton:hover {{
        background-color: #dc2626;
        border-color: #dc2626;
    }}

    #dangerButton:disabled {{
        background-color: #fca5a5;
        border-color: #fca5a5;
        color: #ffffff;
    }}

    /* Status Bar */
    QStatusBar {{
        background-color: {t.BG_SIDEBAR};
        border-top: 1px solid {t.BORDER_DEFAULT};
        color: {t.TEXT_MUTED};
        font-size: {t.FONT_SIZE_CAPTION};
        padding: 3px 10px;
    }}

    /* Tables */
    QTableWidget {{
        background-color: {t.BG_CARD};
        border: 1px solid {t.BORDER_DEFAULT};
        border-radius: {t.RADIUS_DEFAULT};
        gridline-color: {t.BORDER_DEFAULT};
        color: {t.TEXT_PRIMARY};
        font-size: {t.FONT_SIZE_TABLE};
        selection-background-color: {t.BG_SIDEBAR_ACTIVE};
        selection-color: {t.BRAND_PRIMARY};
    }}

    QTableWidget::item {{
        padding: 6px 10px;
        border-bottom: 1px solid {t.BORDER_DEFAULT};
    }}

    QTableWidget::item:selected {{
        background-color: {t.BG_SIDEBAR_ACTIVE};
        color: {t.BRAND_PRIMARY};
    }}

    QHeaderView::section {{
        background-color: {t.BG_MUTED};
        color: {t.TEXT_SECONDARY};
        border: none;
        border-bottom: 1px solid {t.BORDER_DEFAULT};
        border-right: 1px solid {t.BORDER_DEFAULT};
        padding: 6px 10px;
        font-weight: 600;
        font-size: {t.FONT_SIZE_CAPTION};
    }}

    /* Inputs & Selectors */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {t.BG_INPUT};
        border: 1px solid {t.BORDER_INPUT};
        border-radius: {t.RADIUS_DEFAULT};
        padding: 5px 8px;
        color: {t.TEXT_PRIMARY};
        font-size: {t.FONT_SIZE_BODY};
        selection-background-color: {t.BRAND_PRIMARY};
    }}

    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {t.BORDER_FOCUS};
    }}

    /* Scrollbars - Sleek non-obtrusive styling */
    QScrollBar:vertical {{
        background-color: transparent;
        width: 8px;
        margin: 0px;
    }}

    QScrollBar::handle:vertical {{
        background-color: #cbd5e1;
        min-height: 24px;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: #94a3b8;
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar:horizontal {{
        background-color: transparent;
        height: 8px;
        margin: 0px;
    }}

    QScrollBar::handle:horizontal {{
        background-color: #cbd5e1;
        min-width: 24px;
        border-radius: 4px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background-color: #94a3b8;
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    """
