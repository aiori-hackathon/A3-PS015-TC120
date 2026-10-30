"""Base page widget providing consistent layout, headers, and honest empty states."""

from __future__ import annotations

from typing import Optional
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens


class BasePage(QWidget):
    """Base class for all application page shells."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.title_text = title
        self.subtitle_text = subtitle

        self._setup_layout()

    def _setup_layout(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Page Header Container
        header_widget = QWidget(self)
        header_widget.setObjectName("headerContainer")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 14, 20, 12)
        header_layout.setSpacing(2)

        self.lbl_title = QLabel(self.title_text, header_widget)
        self.lbl_title.setObjectName("pageTitle")
        self.lbl_title.setWordWrap(True)
        header_layout.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel(self.subtitle_text, header_widget)
        self.lbl_subtitle.setObjectName("pageSubtitle")
        self.lbl_subtitle.setWordWrap(True)
        header_layout.addWidget(self.lbl_subtitle)

        main_layout.addWidget(header_widget)

        # Scrollable Content Area - ZERO horizontal scrolling tolerance
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("contentScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.content_widget = QWidget()
        self.content_widget.setObjectName("contentContainer")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(20, 14, 20, 14)
        self.content_layout.setSpacing(14)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.content_widget)
        self.scroll_area.viewport().installEventFilter(self)
        main_layout.addWidget(self.scroll_area)

    def eventFilter(self, watched, event):
        """Constrain content widget to viewport width so zero horizontal scrolling occurs."""
        if watched == self.scroll_area.viewport() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            vp_w = self.scroll_area.viewport().width()
            if vp_w > 0:
                self.content_widget.setMaximumWidth(vp_w)
        return super().eventFilter(watched, event)

    def create_card(self, title: str, description: Optional[str] = None) -> tuple[QFrame, QVBoxLayout]:
        """Create a styled card widget with optional title and description."""
        card = QFrame(self.content_widget)
        card.setProperty("class", "Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        if title:
            lbl_card_title = QLabel(title, card)
            lbl_card_title.setProperty("class", "CardTitle")
            lbl_card_title.setWordWrap(True)
            card_layout.addWidget(lbl_card_title)

        if description:
            lbl_desc = QLabel(description, card)
            lbl_desc.setProperty("class", "CardDescription")
            lbl_desc.setWordWrap(True)
            card_layout.addWidget(lbl_desc)

        return card, card_layout

    def create_empty_state(
        self,
        title: str,
        message: str,
        action_widget: Optional[QWidget] = None,
    ) -> QFrame:
        """Create a styled honest empty state banner without synthetic data."""
        container = QFrame(self.content_widget)
        container.setProperty("class", "EmptyStateContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_title = QLabel(title, container)
        lbl_title.setProperty("class", "EmptyStateTitle")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setWordWrap(True)
        layout.addWidget(lbl_title)

        lbl_msg = QLabel(message, container)
        lbl_msg.setProperty("class", "EmptyStateText")
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_msg.setWordWrap(True)
        layout.addWidget(lbl_msg)

        if action_widget is not None:
            layout.addSpacing(6)
            layout.addWidget(action_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        return container
