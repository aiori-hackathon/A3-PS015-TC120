"""Domain entities and enums for configurable alerts and network incidents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


from ticnet.core.timezone import now_ist_iso


def now_utc_iso() -> str:
    """Application-wide migrated timestamp utility: returns current IST time (Asia/Kolkata) as ISO 8601 string."""
    return now_ist_iso()


def new_id() -> str:
    """Generate a random UUID4 string."""
    return str(uuid.uuid4())


class IncidentSeverity(str, Enum):
    """Severity classification of a network incident."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    """Lifecycle status of a network incident."""
    SUSPECTED = "SUSPECTED"
    CONFIRMED = "CONFIRMED"
    RESOLVED = "RESOLVED"


@dataclass
class NotificationPreferences:
    """User configuration for desktop and sound notifications."""
    preference_id: str = "default"
    desktop_enabled: bool = True
    sound_enabled: bool = False
    notify_suspected: bool = False
    notify_confirmed: bool = True
    notify_recovery: bool = True
    cooldown_minutes: int = 30
    updated_at_utc: str = field(default_factory=now_utc_iso)


@dataclass
class NetworkIncident:
    """Persistent audit record of an active or historical network incident."""
    incident_id: str = field(default_factory=new_id)
    monitoring_run_id: Optional[str] = None
    metric_name: str = "overall"
    severity: str = IncidentSeverity.WARNING.value
    status: str = IncidentStatus.SUSPECTED.value
    baseline_value: Optional[float] = None
    observed_value: Optional[float] = None
    deviation_pct: Optional[float] = None
    headline: str = ""
    description: str = ""
    started_at_utc: str = field(default_factory=now_utc_iso)
    last_seen_at_utc: str = field(default_factory=now_utc_iso)
    resolved_at_utc: Optional[str] = None
    last_notified_at_utc: Optional[str] = None
    notification_count: int = 0


@dataclass
class NotificationPayload:
    """In-memory payload for dispatching user-facing alerts."""
    title: str
    message: str
    severity: IncidentSeverity
    timestamp_utc: str
    metric_name: str
    observed_value: Optional[float] = None
    baseline_value: Optional[float] = None
    incident_id: Optional[str] = None

