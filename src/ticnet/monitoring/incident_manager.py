"""Incident tracking and explainable alert notification manager.

Integrates with Stage 2 Slowdown Detection results without duplicating detection logic.
Enforces user notification preferences, cooldown deduplication, and non-blocking desktop alerts.
Zero telemetry, cloud calls, or automatic network configuration changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import sys
from typing import Callable, List, Optional, Tuple

from ticnet.models.notifications import (
    IncidentSeverity,
    IncidentStatus,
    NetworkIncident,
    NotificationPayload,
    NotificationPreferences,
    now_utc_iso,
)
from ticnet.monitoring.analytics import DetectionStatus, MetricEvaluation, SlowdownReport
from ticnet.persistence.repositories.incident_repo import IncidentRepository
from ticnet.utils.logging import get_logger

logger = get_logger("monitoring.incidents")


from ticnet.core.timezone import IST, now_ist, now_ist_iso, to_ist

def parse_iso_utc(iso_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string to a timezone-aware IST datetime (Asia/Kolkata)."""
    return to_ist(iso_str)


class IncidentManager:
    """Evaluates slowdown reports against active incidents and manages notification dispatch."""

    def __init__(
        self,
        incident_repo: IncidentRepository,
        notification_dispatcher: Optional[Callable[[NotificationPayload, NotificationPreferences], None]] = None,
    ) -> None:
        self.repo = incident_repo
        self.dispatcher = notification_dispatcher or self.default_dispatcher

    def process_slowdown_report(
        self,
        report: SlowdownReport,
        monitoring_run_id: Optional[str] = None,
        now_utc: Optional[datetime] = None,
    ) -> List[NetworkIncident]:
        """Process Stage 2 SlowdownReport, updating incidents and dispatching alerts.

        Args:
            report: Consolidated SlowdownReport from Stage 2 AnalyticsEngine.
            monitoring_run_id: Optional monitoring run ID associated with the measurements.
            now_utc: Optional reference datetime (defaults to current UTC time).

        Returns:
            List of created or updated NetworkIncident instances.
        """
        if now_utc is None:
            now_utc = now_ist()

        prefs = self.repo.get_preferences()
        processed_incidents: List[NetworkIncident] = []

        for metric_name, evaluation in report.evaluations.items():
            incident = self._evaluate_metric_incident(
                metric_name=metric_name,
                evaluation=evaluation,
                monitoring_run_id=monitoring_run_id,
                prefs=prefs,
                now_utc=now_utc,
            )
            if incident:
                processed_incidents.append(incident)

        return processed_incidents

    def _evaluate_metric_incident(
        self,
        metric_name: str,
        evaluation: MetricEvaluation,
        monitoring_run_id: Optional[str],
        prefs: NotificationPreferences,
        now_utc: datetime,
    ) -> Optional[NetworkIncident]:
        """Evaluate an individual metric against ongoing and historical incidents."""
        active = self.repo.get_active_incident_for_metric(metric_name)
        now_iso = now_utc.isoformat()

        # ---------------------------------------------------------------------
        # Case 1: CONFIRMED_SLOWDOWN (Sustained Degradation)
        # ---------------------------------------------------------------------
        if evaluation.status == DetectionStatus.CONFIRMED_SLOWDOWN:
            headline = f"Confirmed Network Slowdown: {metric_name.replace('_', ' ').title()}"
            description = evaluation.explanation
            severity = IncidentSeverity.CRITICAL.value

            if active:
                was_suspected = (active.status == IncidentStatus.SUSPECTED.value)
                active.status = IncidentStatus.CONFIRMED.value
                active.severity = severity
                active.observed_value = evaluation.observed_value
                active.baseline_value = evaluation.baseline_value
                active.deviation_pct = evaluation.deviation_pct
                active.headline = headline
                active.description = description
                active.last_seen_at_utc = now_iso
                active.monitoring_run_id = monitoring_run_id

                # Notify if newly escalated to CONFIRMED or cooldown elapsed
                should_notify = False
                if prefs.desktop_enabled and prefs.notify_confirmed:
                    if was_suspected:
                        should_notify = True
                    else:
                        should_notify = self._is_cooldown_elapsed(active.last_notified_at_utc, prefs.cooldown_minutes, now_utc)

                if should_notify:
                    self._dispatch_alert(active, prefs, headline, description, IncidentSeverity.CRITICAL, now_iso)
                    active.last_notified_at_utc = now_iso
                    active.notification_count += 1

                self.repo.update_incident(active)
                return active
            else:
                # Brand new confirmed incident
                incident = NetworkIncident(
                    monitoring_run_id=monitoring_run_id,
                    metric_name=metric_name,
                    severity=severity,
                    status=IncidentStatus.CONFIRMED.value,
                    baseline_value=evaluation.baseline_value,
                    observed_value=evaluation.observed_value,
                    deviation_pct=evaluation.deviation_pct,
                    headline=headline,
                    description=description,
                    started_at_utc=now_iso,
                    last_seen_at_utc=now_iso,
                )
                if prefs.desktop_enabled and prefs.notify_confirmed:
                    self._dispatch_alert(incident, prefs, headline, description, IncidentSeverity.CRITICAL, now_iso)
                    incident.last_notified_at_utc = now_iso
                    incident.notification_count = 1

                return self.repo.create_incident(incident)

        # ---------------------------------------------------------------------
        # Case 2: SUSPECTED_SLOWDOWN (Single Isolated Deviation)
        # ---------------------------------------------------------------------
        elif evaluation.status == DetectionStatus.SUSPECTED_SLOWDOWN:
            headline = f"Suspected Performance Variation: {metric_name.replace('_', ' ').title()}"
            description = evaluation.explanation
            severity = IncidentSeverity.WARNING.value

            if active:
                active.observed_value = evaluation.observed_value
                active.baseline_value = evaluation.baseline_value
                active.deviation_pct = evaluation.deviation_pct
                active.last_seen_at_utc = now_iso
                active.monitoring_run_id = monitoring_run_id

                should_notify = (
                    prefs.desktop_enabled
                    and prefs.notify_suspected
                    and self._is_cooldown_elapsed(active.last_notified_at_utc, prefs.cooldown_minutes, now_utc)
                )
                if should_notify:
                    self._dispatch_alert(active, prefs, headline, description, IncidentSeverity.WARNING, now_iso)
                    active.last_notified_at_utc = now_iso
                    active.notification_count += 1

                self.repo.update_incident(active)
                return active
            else:
                incident = NetworkIncident(
                    monitoring_run_id=monitoring_run_id,
                    metric_name=metric_name,
                    severity=severity,
                    status=IncidentStatus.SUSPECTED.value,
                    baseline_value=evaluation.baseline_value,
                    observed_value=evaluation.observed_value,
                    deviation_pct=evaluation.deviation_pct,
                    headline=headline,
                    description=description,
                    started_at_utc=now_iso,
                    last_seen_at_utc=now_iso,
                )
                if prefs.desktop_enabled and prefs.notify_suspected:
                    self._dispatch_alert(incident, prefs, headline, description, IncidentSeverity.WARNING, now_iso)
                    incident.last_notified_at_utc = now_iso
                    incident.notification_count = 1

                return self.repo.create_incident(incident)

        # ---------------------------------------------------------------------
        # Case 3: CONFIRMED_RECOVERY (Performance Returned to Baseline)
        # ---------------------------------------------------------------------
        elif evaluation.status == DetectionStatus.CONFIRMED_RECOVERY:
            if active:
                active.status = IncidentStatus.RESOLVED.value
                active.resolved_at_utc = now_iso
                active.last_seen_at_utc = now_iso
                active.monitoring_run_id = monitoring_run_id
                headline = f"Network Performance Recovered: {metric_name.replace('_', ' ').title()}"
                description = (
                    f"Performance has returned to normal baseline thresholds "
                    f"({evaluation.observed_value} vs baseline {evaluation.baseline_value})."
                )

                if prefs.desktop_enabled and prefs.notify_recovery:
                    self._dispatch_alert(active, prefs, headline, description, IncidentSeverity.INFO, now_iso)
                    active.last_notified_at_utc = now_iso
                    active.notification_count += 1

                self.repo.update_incident(active)
                return active

        # ---------------------------------------------------------------------
        # Case 4: NORMAL (Nominal Performance)
        # ---------------------------------------------------------------------
        elif evaluation.status == DetectionStatus.NORMAL:
            if active:
                # If an active incident existed but latest run is normal, resolve it cleanly
                active.status = IncidentStatus.RESOLVED.value
                active.resolved_at_utc = now_iso
                active.last_seen_at_utc = now_iso
                self.repo.update_incident(active)
                return active

        return None

    def _is_cooldown_elapsed(
        self,
        last_notified_iso: Optional[str],
        cooldown_minutes: int,
        now_utc: datetime,
    ) -> bool:
        """Return True if cooldown interval has elapsed since last alert."""
        if not last_notified_iso:
            return True
        last_dt = parse_iso_utc(last_notified_iso)
        if not last_dt:
            return True
        delta = now_utc - last_dt
        return delta >= timedelta(minutes=cooldown_minutes)

    def _dispatch_alert(
        self,
        incident: NetworkIncident,
        prefs: NotificationPreferences,
        title: str,
        message: str,
        severity: IncidentSeverity,
        timestamp_utc: str,
    ) -> None:
        """Package notification payload and invoke non-blocking dispatcher."""
        payload = NotificationPayload(
            title=title,
            message=message,
            severity=severity,
            timestamp_utc=timestamp_utc,
            metric_name=incident.metric_name,
            observed_value=incident.observed_value,
            baseline_value=incident.baseline_value,
            incident_id=incident.incident_id,
        )
        try:
            self.dispatcher(payload, prefs)
        except Exception as exc:
            logger.warning("Error dispatching alert notification: %s", exc)

    @staticmethod
    def default_dispatcher(payload: NotificationPayload, prefs: NotificationPreferences) -> None:
        """Safe desktop and sound alert dispatcher using standard PySide6 / OS APIs."""
        logger.info("ALERT [%s]: %s — %s", payload.severity.value, payload.title, payload.message)

        # 1. Sound Alert (if enabled)
        if prefs.sound_enabled:
            try:
                if sys.platform == "win32":
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if payload.severity == IncidentSeverity.CRITICAL else winsound.MB_ICONASTERISK)
                else:
                    from PySide6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app:
                        app.beep()
            except Exception as exc:
                logger.debug("Sound alert could not be emitted: %s", exc)

        # 2. Desktop Notification (if enabled and GUI is available)
        if prefs.desktop_enabled:
            try:
                from PySide6.QtWidgets import QApplication, QSystemTrayIcon
                app = QApplication.instance()
                if app and QSystemTrayIcon.isSystemTrayAvailable():
                    tray = getattr(app, "_ticnet_tray_icon", None)
                    if not tray:
                        tray = QSystemTrayIcon(app)
                        app._ticnet_tray_icon = tray
                        tray.show()

                    icon = QSystemTrayIcon.MessageIcon.Information
                    if payload.severity == IncidentSeverity.CRITICAL:
                        icon = QSystemTrayIcon.MessageIcon.Critical
                    elif payload.severity == IncidentSeverity.WARNING:
                        icon = QSystemTrayIcon.MessageIcon.Warning

                    tray.showMessage(payload.title, payload.message, icon, 5000)
            except Exception as exc:
                logger.debug("Desktop tray notification suppressed or unavailable: %s", exc)

