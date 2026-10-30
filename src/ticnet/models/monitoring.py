"""Domain entities and enums for scheduled network monitoring."""

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


class IntervalType(str, Enum):
    """Supported monitoring schedule intervals."""
    M30 = "30M"
    H1 = "1H"
    H2 = "2H"
    DAILY = "DAILY"

    @classmethod
    def display_labels(cls) -> dict[str, str]:
        return {
            cls.M30.value: "Every 30 minutes",
            cls.H1.value: "Every 1 hour",
            cls.H2.value: "Every 2 hours",
            cls.DAILY.value: "Daily (scheduled time)",
        }


class MonitoringStatus(str, Enum):
    """High-level monitoring operational status."""
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ENABLED = "ENABLED"
    PAUSED = "PAUSED"


class MonitoringTriggerType(str, Enum):
    """Trigger mode for a monitoring run."""
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class MonitoringRunStatus(str, Enum):
    """Execution status of a monitoring run."""
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


@dataclass
class MonitoringSchedule:
    """Configuration and state of the periodic network monitoring schedule."""
    schedule_id: str = "default"
    enabled: bool = False
    interval_type: str = IntervalType.H1.value
    daily_time_utc: Optional[str] = "09:00"
    status: str = MonitoringStatus.NOT_CONFIGURED.value
    next_run_due_utc: Optional[str] = None
    last_run_at_utc: Optional[str] = None
    last_run_id: Optional[str] = None
    updated_at_utc: str = field(default_factory=now_utc_iso)


@dataclass
class MonitoringRun:
    """Historical record of an individual scheduled or manual monitoring test."""
    monitoring_run_id: str = field(default_factory=new_id)
    schedule_id: str = "default"
    trigger_type: str = MonitoringTriggerType.SCHEDULED.value
    scheduled_for_utc: str = field(default_factory=now_utc_iso)
    started_at_utc: Optional[str] = None
    completed_at_utc: Optional[str] = None
    status: str = MonitoringRunStatus.SCHEDULED.value
    run_id: Optional[str] = None
    skip_reason: Optional[str] = None
    error_message: Optional[str] = None
    download_mbps: Optional[float] = None
    upload_mbps: Optional[float] = None
    latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None

