"""Workflow events and progress notifications for TicNet.

Defines typed events emitted by the WorkflowOrchestrator and OrchestrationWorker
for decoupled UI progress rendering, structured telemetry, and stage tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from ticnet.models.entities import now_utc_iso


class WorkflowEventType(str, Enum):
    """Categorized workflow events for diagnostic run tracking."""
    RUN_STARTED = "RUN_STARTED"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_PROGRESS = "STAGE_PROGRESS"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    STAGE_SKIPPED = "STAGE_SKIPPED"
    STAGE_FAILED = "STAGE_FAILED"
    STAGE_CANCELLED = "STAGE_CANCELLED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_PARTIAL = "RUN_PARTIAL"
    RUN_CANCELLED = "RUN_CANCELLED"
    RUN_FAILED = "RUN_FAILED"
    LOG = "LOG"


@dataclass(frozen=True)
class WorkflowEvent:
    """Immutable event emitted during workflow execution."""
    event_type: WorkflowEventType
    run_id: str
    timestamp_utc: str = field(default_factory=now_utc_iso)
    stage_id: Optional[str] = None
    stage_name: Optional[str] = None
    stage_order: Optional[int] = None
    status: Optional[str] = None
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary format for serialization."""
        return {
            "event_type": self.event_type.value,
            "run_id": self.run_id,
            "timestamp_utc": self.timestamp_utc,
            "stage_id": self.stage_id,
            "stage_name": self.stage_name,
            "stage_order": self.stage_order,
            "status": self.status,
            "message": self.message,
            "data": self.data or {},
        }
