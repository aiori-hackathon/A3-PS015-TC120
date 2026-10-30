"""Run ID badge widget featuring monospaced typography and copy-to-clipboard functionality.

Conforms to TicNet Unified Professional Run ID standard:
- Strict Monospaced Typography (Consolas / monospace).
- Prominent 'RUN ID:' label and formatted identifier.
- Instant copy-to-clipboard button with timed visual confirmation ('✓ Copied').
- No raw or random UUIDs in the presentation layer.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens


class RunIdBadge(QFrame):
    """Clean professional widget displaying Run ID with copy-to-clipboard action."""

    def __init__(
        self,
        run_id: Optional[str] = None,
        parent: Optional[QWidget] = None,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._run_id: Optional[str] = run_id
        self._compact: bool = compact

        self.setStyleSheet(
            "background-color: transparent; "
            "border: none;"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.lbl_prefix = QLabel("RUN ID:", self)
        self.lbl_prefix.setStyleSheet(
            f"font-size: {'10px' if compact else '11px'}; font-weight: 700; "
            f"color: {ThemeTokens.TEXT_MUTED}; letter-spacing: 0.5px;"
        )
        layout.addWidget(self.lbl_prefix)

        self.lbl_id = QLabel(run_id or "--", self)
        self.lbl_id.setStyleSheet(
            f"font-family: {ThemeTokens.FONT_MONO}; "
            f"font-size: {'11px' if compact else '12px'}; "
            f"font-weight: 700; "
            f"color: {ThemeTokens.BRAND_PRIMARY}; "
            f"background-color: #eff6ff; "
            f"border: 1px solid #bfdbfe; "
            f"border-radius: 6px; "
            f"padding: 2px 8px;"
        )
        self.lbl_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.lbl_id)

        self.btn_copy = QPushButton("Copy", self)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet(self._normal_button_style())
        self.btn_copy.setEnabled(bool(run_id and run_id != "--"))
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(self.btn_copy)

        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self._reset_copy_label)

    def _normal_button_style(self) -> str:
        return (
            f"QPushButton {{ "
            f"  background-color: #ffffff; "
            f"  border: 1px solid #cbd5e1; "
            f"  border-radius: 6px; "
            f"  font-size: 11px; "
            f"  font-weight: 600; "
            f"  color: #475569; "
            f"  padding: 2px 10px; "
            f"}} "
            f"QPushButton:hover {{ "
            f"  background-color: #f8fafc; "
            f"  border-color: #94a3b8; "
            f"}}"
        )

    def set_run_id(self, run_id: Optional[str]) -> None:
        """Update displayed Run ID and toggle copy button availability."""
        self._run_id = run_id
        display_text = run_id if run_id else "--"
        self.lbl_id.setText(display_text)
        self.btn_copy.setEnabled(bool(run_id and run_id != "--"))

    def get_run_id(self) -> Optional[str]:
        """Return the current active Run ID string."""
        return self._run_id

    def _copy_to_clipboard(self) -> None:
        if not self._run_id or self._run_id == "--":
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self._run_id)
            self.btn_copy.setText("✓ Copied")
            self.btn_copy.setStyleSheet(
                "background-color: #ecfdf5; "
                "border: 1px solid #a7f3d0; "
                "color: #059669; "
                "font-size: 11px; font-weight: 700; padding: 2px 10px; border-radius: 6px;"
            )
            self._reset_timer.start(1500)

    def _reset_copy_label(self) -> None:
        self.btn_copy.setText("Copy")
        self.btn_copy.setStyleSheet(self._normal_button_style())
