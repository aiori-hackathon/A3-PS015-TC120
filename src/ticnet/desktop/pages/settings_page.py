"""Settings page for TicNet desktop presentation layer.

Presents configurable diagnostic probe parameters, safety bounds, and active
path information with atomic TOML persistence and strict validation.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ticnet.configuration.validator import AppConfig, ConfigValidationError
from ticnet.desktop.pages.base_page import BasePage
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.desktop.services.settings_service import SettingsService
from ticnet.desktop.theme import ThemeTokens
from ticnet.models.notifications import NotificationPreferences


class SettingsPage(BasePage):
    """Settings page presenting configuration parameters, safety bounds, and path information."""

    def __init__(
        self,
        settings_service: Optional[SettingsService] = None,
        monitoring_service: Optional[MonitoringService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="Settings",
            subtitle="Diagnostic Probe Thresholds, Safety Ceilings, and Persistent Configuration",
            parent=parent,
        )
        self.settings_service = settings_service
        self.monitoring_service = monitoring_service
        self._populate_content()
        self.load_from_service()

    def _populate_content(self) -> None:
        # 1. Probe & Measurement Parameters Card
        probe_card, probe_layout = self.create_card(
            title="Diagnostic Measurement Parameters & Safety Ceilings",
            description=(
                "Parameters are constrained by immutable safety bounds. Values outside safe limits, "
                "excessive probe counts, or unbounded bandwidth tests are strictly rejected."
            ),
        )

        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(3, 1)

        # Gateway Probe Count (1 - 50)
        lbl_gw_count = QLabel("<b>Gateway Probe Count:</b>", probe_card)
        lbl_gw_count.setTextFormat(Qt.TextFormat.RichText)
        lbl_gw_count.setWordWrap(True)
        self.spin_gw_count = QSpinBox(probe_card)
        self.spin_gw_count.setRange(1, 50)
        self.spin_gw_count.setValue(10)
        grid.addWidget(lbl_gw_count, 0, 0)
        grid.addWidget(self.spin_gw_count, 0, 1)

        # Gateway Probe Timeout (100 - 5000 ms)
        lbl_gw_timeout = QLabel("<b>Gateway Probe Timeout (ms):</b>", probe_card)
        lbl_gw_timeout.setTextFormat(Qt.TextFormat.RichText)
        lbl_gw_timeout.setWordWrap(True)
        self.spin_gw_timeout = QSpinBox(probe_card)
        self.spin_gw_timeout.setRange(100, 5000)
        self.spin_gw_timeout.setSingleStep(100)
        self.spin_gw_timeout.setValue(1000)
        grid.addWidget(lbl_gw_timeout, 0, 2)
        grid.addWidget(self.spin_gw_timeout, 0, 3)

        # Gateway Auto-Discover Checkbox
        self.chk_gw_autodiscover = QCheckBox("Auto-Discover Default Gateway via Routing Table", probe_card)
        self.chk_gw_autodiscover.setChecked(True)
        grid.addWidget(self.chk_gw_autodiscover, 1, 0, 1, 2)

        # Gateway Manual IP Override
        lbl_gw_ip = QLabel("<b>Manual Gateway IP Override:</b>", probe_card)
        lbl_gw_ip.setTextFormat(Qt.TextFormat.RichText)
        lbl_gw_ip.setWordWrap(True)
        self.txt_gw_override = QLineEdit(probe_card)
        self.txt_gw_override.setPlaceholderText("Optional (e.g. 192.168.1.1); leave empty for auto")
        grid.addWidget(lbl_gw_ip, 1, 2)
        grid.addWidget(self.txt_gw_override, 1, 3)

        # External Targets Probe Count (1 - 50)
        lbl_ext_count = QLabel("<b>External Target Probe Count:</b>", probe_card)
        lbl_ext_count.setTextFormat(Qt.TextFormat.RichText)
        lbl_ext_count.setWordWrap(True)
        self.spin_ext_count = QSpinBox(probe_card)
        self.spin_ext_count.setRange(1, 50)
        self.spin_ext_count.setValue(10)
        grid.addWidget(lbl_ext_count, 2, 0)
        grid.addWidget(self.spin_ext_count, 2, 1)

        # External Targets Probe Timeout (100 - 5000 ms)
        lbl_ext_timeout = QLabel("<b>External Probe Timeout (ms):</b>", probe_card)
        lbl_ext_timeout.setTextFormat(Qt.TextFormat.RichText)
        lbl_ext_timeout.setWordWrap(True)
        self.spin_ext_timeout = QSpinBox(probe_card)
        self.spin_ext_timeout.setRange(100, 5000)
        self.spin_ext_timeout.setSingleStep(100)
        self.spin_ext_timeout.setValue(1000)
        grid.addWidget(lbl_ext_timeout, 2, 2)
        grid.addWidget(self.spin_ext_timeout, 2, 3)

        # DNS Query Timeout (100 - 5000 ms)
        lbl_dns_timeout = QLabel("<b>DNS Query Timeout (ms):</b>", probe_card)
        lbl_dns_timeout.setTextFormat(Qt.TextFormat.RichText)
        lbl_dns_timeout.setWordWrap(True)
        self.spin_dns_timeout = QSpinBox(probe_card)
        self.spin_dns_timeout.setRange(100, 5000)
        self.spin_dns_timeout.setSingleStep(100)
        self.spin_dns_timeout.setValue(2000)
        grid.addWidget(lbl_dns_timeout, 3, 0)
        grid.addWidget(self.spin_dns_timeout, 3, 1)

        # Throughput Max Duration (1 - 10 s)
        lbl_tp_duration = QLabel("<b>Throughput Max Duration (s):</b>", probe_card)
        lbl_tp_duration.setTextFormat(Qt.TextFormat.RichText)
        lbl_tp_duration.setWordWrap(True)
        self.spin_tp_duration = QSpinBox(probe_card)
        self.spin_tp_duration.setRange(1, 10)
        self.spin_tp_duration.setValue(10)
        grid.addWidget(lbl_tp_duration, 3, 2)
        grid.addWidget(self.spin_tp_duration, 3, 3)

        probe_layout.addLayout(grid)

        # Action Buttons Row
        btn_row = QWidget(probe_card)
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.setSpacing(10)

        self.btn_save = QPushButton("Save Settings", btn_row)
        self.btn_save.setObjectName("primaryButton")
        self.btn_save.clicked.connect(self._handle_save_clicked)

        self.btn_reset = QPushButton("Reset to Factory Defaults", btn_row)
        self.btn_reset.setObjectName("secondaryButton")
        self.btn_reset.clicked.connect(self._handle_reset_clicked)

        self.lbl_feedback = QLabel("", btn_row)
        self.lbl_feedback.setStyleSheet("font-size: 12px; font-weight: 600;")

        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addWidget(self.lbl_feedback)
        btn_layout.addStretch()

        probe_layout.addWidget(btn_row)
        self.content_layout.addWidget(probe_card)

        # 2. Diagnostic Thresholds Card
        thresh_card, thresh_layout = self.create_card(
            title="Diagnostic Classification Thresholds",
            description="Thresholds utilized by the deterministic diagnosis engine to classify anomalies:",
        )

        thresh_grid = QGridLayout()
        thresh_grid.setSpacing(10)
        thresh_grid.setContentsMargins(0, 8, 0, 0)
        thresh_grid.setColumnStretch(0, 2)
        thresh_grid.setColumnStretch(1, 1)
        thresh_grid.setColumnStretch(2, 2)
        thresh_grid.setColumnStretch(3, 1)

        # Gateway Loss Warning (%)
        lbl_gw_loss = QLabel("<b>Gateway Packet Loss Warning (%):</b>", thresh_card)
        lbl_gw_loss.setTextFormat(Qt.TextFormat.RichText)
        lbl_gw_loss.setWordWrap(True)
        self.spin_gw_loss = QDoubleSpinBox(thresh_card)
        self.spin_gw_loss.setRange(1.0, 100.0)
        self.spin_gw_loss.setValue(10.0)
        thresh_grid.addWidget(lbl_gw_loss, 0, 0)
        thresh_grid.addWidget(self.spin_gw_loss, 0, 1)

        # External Loss Warning (%)
        lbl_ext_loss = QLabel("<b>External Path Loss Warning (%):</b>", thresh_card)
        lbl_ext_loss.setTextFormat(Qt.TextFormat.RichText)
        lbl_ext_loss.setWordWrap(True)
        self.spin_ext_loss = QDoubleSpinBox(thresh_card)
        self.spin_ext_loss.setRange(1.0, 100.0)
        self.spin_ext_loss.setValue(15.0)
        thresh_grid.addWidget(lbl_ext_loss, 0, 2)
        thresh_grid.addWidget(self.spin_ext_loss, 0, 3)

        # Bufferbloat Delta Threshold (ms)
        lbl_bb_delta = QLabel("<b>Bufferbloat Latency Inflation Threshold (ms):</b>", thresh_card)
        lbl_bb_delta.setTextFormat(Qt.TextFormat.RichText)
        lbl_bb_delta.setWordWrap(True)
        self.spin_bb_delta = QDoubleSpinBox(thresh_card)
        self.spin_bb_delta.setRange(10.0, 500.0)
        self.spin_bb_delta.setValue(50.0)
        thresh_grid.addWidget(lbl_bb_delta, 1, 0)
        thresh_grid.addWidget(self.spin_bb_delta, 1, 1)

        thresh_layout.addLayout(thresh_grid)
        self.content_layout.addWidget(thresh_card)

        # 3. Notification & Alert Preferences Card (Stage 3)
        if self.monitoring_service:
            notif_card, notif_layout = self.create_card(
                title="Incident Notifications & Alert Preferences",
                description="Configure native OS alert banners and audio cues for detected network slowdowns and recoveries:",
            )

            notif_grid = QGridLayout()
            notif_grid.setSpacing(10)
            notif_grid.setContentsMargins(0, 8, 0, 0)
            notif_grid.setColumnStretch(0, 2)
            notif_grid.setColumnStretch(1, 2)

            self.chk_notif_desktop = QCheckBox("Enable Desktop Notifications", notif_card)
            notif_grid.addWidget(self.chk_notif_desktop, 0, 0)

            self.chk_notif_sound = QCheckBox("Enable Sound Alerts", notif_card)
            notif_grid.addWidget(self.chk_notif_sound, 0, 1)

            self.chk_notif_confirmed = QCheckBox("Alert on Confirmed Slowdowns (Sustained)", notif_card)
            notif_grid.addWidget(self.chk_notif_confirmed, 1, 0)

            self.chk_notif_recovery = QCheckBox("Alert on Confirmed Recovery", notif_card)
            notif_grid.addWidget(self.chk_notif_recovery, 1, 1)

            self.chk_notif_suspected = QCheckBox("Alert on Suspected Slowdowns (Single Spike)", notif_card)
            notif_grid.addWidget(self.chk_notif_suspected, 2, 0)

            cooldown_layout = QHBoxLayout()
            cooldown_layout.setSpacing(6)
            lbl_cd = QLabel("<b>Alert Cooldown:</b>", notif_card)
            self.spin_notif_cooldown = QSpinBox(notif_card)
            self.spin_notif_cooldown.setRange(5, 360)
            self.spin_notif_cooldown.setValue(30)
            self.spin_notif_cooldown.setSuffix(" min")
            cooldown_layout.addWidget(lbl_cd)
            cooldown_layout.addWidget(self.spin_notif_cooldown)
            cooldown_layout.addStretch()
            notif_grid.addLayout(cooldown_layout, 2, 1)

            notif_layout.addLayout(notif_grid)

            # Notif Save Button
            notif_btn_row = QWidget(notif_card)
            notif_btn_layout = QHBoxLayout(notif_btn_row)
            notif_btn_layout.setContentsMargins(0, 8, 0, 0)
            notif_btn_layout.setSpacing(10)

            self.btn_save_notifs = QPushButton("Save Notification Preferences", notif_btn_row)
            self.btn_save_notifs.setObjectName("primaryButton")
            self.btn_save_notifs.clicked.connect(self._handle_save_notifs_clicked)
            notif_btn_layout.addWidget(self.btn_save_notifs)

            self.lbl_notif_feedback = QLabel("", notif_btn_row)
            self.lbl_notif_feedback.setStyleSheet("font-size: 12px; font-weight: 600;")
            notif_btn_layout.addWidget(self.lbl_notif_feedback)
            notif_btn_layout.addStretch()

            notif_layout.addWidget(notif_btn_row)
            self.content_layout.addWidget(notif_card)

    def load_from_service(self) -> None:
        """Populate input controls from the active configuration."""
        if not self.settings_service:
            return

        cfg = self.settings_service.active_config
        t = cfg.targets
        th = cfg.thresholds

        self.spin_gw_count.setValue(t.gateway.probe_count)
        self.spin_gw_timeout.setValue(t.gateway.probe_timeout_ms)
        self.chk_gw_autodiscover.setChecked(t.gateway.auto_discover)
        self.txt_gw_override.setText(t.gateway.manual_override_ip or "")

        self.spin_ext_count.setValue(t.external_targets.probe_count)
        self.spin_ext_timeout.setValue(t.external_targets.probe_timeout_ms)

        self.spin_dns_timeout.setValue(t.dns_resolvers.timeout_ms)
        self.spin_tp_duration.setValue(t.throughput.max_duration_seconds)

        self.spin_gw_loss.setValue(th.gateway_thresholds.packet_loss_warning_pct)
        self.spin_ext_loss.setValue(th.external_thresholds.packet_loss_warning_pct)
        self.spin_bb_delta.setValue(th.loaded_thresholds.bufferbloat_rtt_delta_ms)

        if self.monitoring_service and hasattr(self, "chk_notif_desktop"):
            prefs = self.monitoring_service.get_notification_preferences()
            self.chk_notif_desktop.setChecked(prefs.desktop_enabled)
            self.chk_notif_sound.setChecked(prefs.sound_enabled)
            self.chk_notif_confirmed.setChecked(prefs.notify_confirmed)
            self.chk_notif_recovery.setChecked(prefs.notify_recovery)
            self.chk_notif_suspected.setChecked(prefs.notify_suspected)
            self.spin_notif_cooldown.setValue(prefs.cooldown_minutes)

    def _handle_save_notifs_clicked(self) -> None:
        """Save notification preferences via MonitoringService."""
        if not self.monitoring_service or not hasattr(self, "chk_notif_desktop"):
            return

        prefs = NotificationPreferences(
            preference_id="default",
            desktop_enabled=self.chk_notif_desktop.isChecked(),
            sound_enabled=self.chk_notif_sound.isChecked(),
            notify_confirmed=self.chk_notif_confirmed.isChecked(),
            notify_recovery=self.chk_notif_recovery.isChecked(),
            notify_suspected=self.chk_notif_suspected.isChecked(),
            cooldown_minutes=self.spin_notif_cooldown.value(),
        )
        try:
            self.monitoring_service.save_notification_preferences(prefs)
            self.lbl_notif_feedback.setText("Notification preferences saved!")
            self.lbl_notif_feedback.setStyleSheet(f"color: {ThemeTokens.COLOR_SUCCESS}; font-size: 12px; font-weight: 600;")
        except Exception as exc:
            self.lbl_notif_feedback.setText(f"Error saving preferences: {exc}")
            self.lbl_notif_feedback.setStyleSheet(f"color: {ThemeTokens.COLOR_ERROR}; font-size: 12px; font-weight: 600;")

    def _handle_save_clicked(self) -> None:
        """Validate input values and atomically persist user settings."""
        if not self.settings_service:
            return

        current_cfg = self.settings_service.active_config
        t_dict = dataclasses.asdict(current_cfg.targets)
        th_dict = dataclasses.asdict(current_cfg.thresholds)

        # Apply updated field values
        gw_override = self.txt_gw_override.text().strip()
        t_dict["gateway"]["probe_count"] = self.spin_gw_count.value()
        t_dict["gateway"]["probe_timeout_ms"] = self.spin_gw_timeout.value()
        t_dict["gateway"]["auto_discover"] = self.chk_gw_autodiscover.isChecked()
        t_dict["gateway"]["manual_override_ip"] = gw_override if gw_override else None

        t_dict["external_targets"]["probe_count"] = self.spin_ext_count.value()
        t_dict["external_targets"]["probe_timeout_ms"] = self.spin_ext_timeout.value()

        t_dict["dns_resolvers"]["timeout_ms"] = self.spin_dns_timeout.value()
        t_dict["throughput"]["max_duration_seconds"] = self.spin_tp_duration.value()

        th_dict["gateway_thresholds"]["packet_loss_warning_pct"] = self.spin_gw_loss.value()
        th_dict["external_thresholds"]["packet_loss_warning_pct"] = self.spin_ext_loss.value()
        th_dict["loaded_thresholds"]["bufferbloat_rtt_delta_ms"] = self.spin_bb_delta.value()

        try:
            new_cfg = self.settings_service.save_settings(t_dict, th_dict)
            self.lbl_feedback.setText("Settings saved successfully!")
            self.lbl_feedback.setStyleSheet(f"color: {ThemeTokens.COLOR_SUCCESS}; font-size: 12px; font-weight: 600;")
            QMessageBox.information(
                self,
                "Settings Saved",
                "Diagnostic configuration settings were successfully validated and saved.\n\n"
                "New settings will take effect on the next diagnostic run.",
            )
        except ConfigValidationError as val_err:
            self.lbl_feedback.setText("Validation failed: check parameters.")
            self.lbl_feedback.setStyleSheet(f"color: {ThemeTokens.COLOR_ERROR}; font-size: 12px; font-weight: 600;")
            QMessageBox.warning(
                self,
                "Invalid Configuration",
                f"Configuration parameter validation failed:\n\n{val_err}\n\n"
                f"Previous valid settings have been preserved.",
            )
        except Exception as exc:
            self.lbl_feedback.setText(f"Save error: {exc}")
            self.lbl_feedback.setStyleSheet(f"color: {ThemeTokens.COLOR_ERROR}; font-size: 12px; font-weight: 600;")
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Failed to persist configuration to disk:\n\n{exc}\n\n"
                f"Previous valid settings have been preserved by atomic write rollback.",
            )

    def _handle_reset_clicked(self) -> None:
        """Confirm and restore factory default configuration."""
        if not self.settings_service:
            return

        reply = QMessageBox.question(
            self,
            "Restore Factory Defaults",
            "Are you sure you want to restore all diagnostic settings to factory defaults?\n\n"
            "This will remove user configuration overrides.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.settings_service.reset_to_defaults()
            self.load_from_service()
            self.lbl_feedback.setText("Settings restored to factory defaults.")
            self.lbl_feedback.setStyleSheet(f"color: {ThemeTokens.BRAND_PRIMARY}; font-size: 12px; font-weight: 600;")
