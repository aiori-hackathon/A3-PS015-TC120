"""Domain entity dataclasses for TicNet.

These typed dataclasses represent runs, stages, targets, observations,
summaries, findings, and lab evaluation records.

Key Invariants:
- All timestamps are timezone-aware UTC strings (ISO 8601).
- Missing, failed, or timed-out measurements are represented with value_numeric=None.
- Zero is a valid measured number only when a probe legitimately returned 0.0.
- Raw observations are preserved separately from summaries and findings.
- Lab ground truth is strictly quarantined in LabTrial.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Optional, Dict, Any, Union
import uuid

from ticnet.models.enums import (
    RunStatus,
    StageStatus,
    RunMode,
    TargetType,
    MeasurementStatus,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    EvidenceStrength,
    EvidenceRelationship,
    LabScenario,
    TrialVerificationStatus,
    TrialCleanupStatus,
    ExportFormat,
    ExportStatus,
)


from ticnet.core.timezone import (
    IST,
    UTC,
    format_ist,
    now_ist,
    now_ist_iso,
    to_ist,
    to_ist_iso,
    today_ist,
)


def now_utc_iso() -> str:
    """Application-wide migrated timestamp utility: returns current IST time (Asia/Kolkata) as ISO 8601 string.
    
    Maintained as a seamless migration alias so all existing callers produce verified IST timestamps.
    """
    return now_ist_iso()


def new_id() -> str:
    """Generate a random UUID4 string for entity primary keys."""
    return str(uuid.uuid4())


def generate_run_id(
    target_date: Optional[Union[datetime, date, str]] = None,
    sequence: int = 1,
) -> str:
    """Generate a readable, sequential Run ID conforming to TCN-YYYYMMDD-XXXXXX using IST calendar date boundaries.

    Args:
        target_date: Optional creation datetime, date, or date string. Defaults to current IST date.
        sequence: Six-digit integer sequence number (e.g. 1 -> 000001).

    Returns:
        Formatted Run ID string, e.g. 'TCN-20261030-000001'.
    """
    if target_date is None:
        date_str = now_ist().strftime("%Y%m%d")
    elif isinstance(target_date, datetime):
        date_str = to_ist(target_date).strftime("%Y%m%d")
    elif isinstance(target_date, date):
        date_str = target_date.strftime("%Y%m%d")
    elif isinstance(target_date, str):
        cleaned = "".join(c for c in target_date if c.isdigit())[:8]
        date_str = cleaned if len(cleaned) == 8 else now_ist().strftime("%Y%m%d")
    else:
        date_str = now_ist().strftime("%Y%m%d")
    return f"TCN-{date_str}-{sequence:06d}"


@dataclass
class TestRun:
    """Represents a diagnostic test session containing multiple test stages."""
    __test__ = False

    run_id: str
    start_time_utc: str
    status: RunStatus
    app_version: str
    config_version: str
    run_mode: RunMode = RunMode.OPERATIONAL
    end_time_utc: Optional[str] = None
    interface_name: Optional[str] = None
    interface_type: Optional[str] = None
    gateway_ip: Optional[str] = None
    overall_error: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = RunStatus(self.status)
        if isinstance(self.run_mode, str):
            self.run_mode = RunMode(self.run_mode)


@dataclass
class TestStage:
    """Represents an individual stage within a test run."""
    __test__ = False

    stage_id: str
    run_id: str
    stage_name: str
    stage_order: int
    start_time_utc: str
    status: StageStatus = StageStatus.PENDING
    end_time_utc: Optional[str] = None
    skip_reason: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = StageStatus(self.status)
        if self.stage_order < 1:
            raise ValueError("stage_order must be a positive integer >= 1")


@dataclass
class Target:
    """Represents a probed destination (gateway, external IP, resolver, etc.)."""
    target_id: str
    target_type: TargetType
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    port: Optional[int] = None
    provider_label: Optional[str] = None
    is_active: bool = True

    def __post_init__(self):
        if isinstance(self.target_type, str):
            self.target_type = TargetType(self.target_type)


@dataclass
class Observation:
    """Represents an empirical, raw measurement observation.
    
    Invariant: If status is not SUCCESS, value_numeric MUST be None.
    A missing measurement must never be coerced to 0.0.
    """
    observation_id: str
    run_id: str
    sample_index: int
    timestamp_utc: str
    duration_ms: float
    metric_name: str
    unit: str
    status: MeasurementStatus
    value_numeric: Optional[float] = None
    stage_id: Optional[str] = None
    target_id: Optional[str] = None
    error_message: Optional[str] = None
    method_metadata: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = MeasurementStatus(self.status)
        if self.sample_index < 1:
            raise ValueError("sample_index must be >= 1")
        if self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")
        
        # Enforce missingness honesty:
        if self.status != MeasurementStatus.SUCCESS and self.value_numeric is not None:
            raise ValueError(
                f"value_numeric must be None when status is {self.status.value}; "
                "failed/timed-out measurements must never be coerced to 0.0"
            )


@dataclass
class Summary:
    """Represents a derived statistical metric computed from valid observations.
    
    Invariant: Discloses total attempted and valid sample counts.
    If sample_count_valid is 0, value_numeric must be None.
    """
    summary_id: str
    run_id: str
    metric_name: str
    statistic_name: str
    unit: str
    sample_count_total: int
    sample_count_valid: int
    calculation_method: str
    calculation_version: str
    value_numeric: Optional[float] = None
    target_id: Optional[str] = None

    def __post_init__(self):
        if self.sample_count_total < 0 or self.sample_count_valid < 0:
            raise ValueError("Sample counts cannot be negative")
        if self.sample_count_valid > self.sample_count_total:
            raise ValueError("sample_count_valid cannot exceed sample_count_total")
        if self.sample_count_valid == 0 and self.value_numeric is not None:
            raise ValueError("value_numeric must be None when sample_count_valid is 0")


@dataclass
class Finding:
    """Represents an explainable conclusion derived by the rule engine."""
    finding_id: str
    run_id: str
    category: FindingCategory
    priority: FindingPriority
    rule_id: str
    rule_version: str
    status: FindingStatus
    evidence_strength: EvidenceStrength
    plain_summary: str
    limitations: str
    recommended_action: str

    def __post_init__(self):
        if isinstance(self.category, str):
            self.category = FindingCategory(self.category)
        if isinstance(self.priority, str):
            self.priority = FindingPriority(self.priority)
        if isinstance(self.status, str):
            self.status = FindingStatus(self.status)
        if isinstance(self.evidence_strength, str):
            self.evidence_strength = EvidenceStrength(self.evidence_strength)


@dataclass
class FindingEvidence:
    """Junction entity linking a finding to its supporting/conflicting observations."""
    evidence_id: str
    finding_id: str
    relationship_type: EvidenceRelationship
    observation_id: Optional[str] = None
    summary_id: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.relationship_type, str):
            self.relationship_type = EvidenceRelationship(self.relationship_type)
        if self.observation_id is None and self.summary_id is None:
            raise ValueError("FindingEvidence must reference an observation_id or a summary_id")


@dataclass
class LabTrial:
    """Records ground truth and evaluation outcomes for isolated laboratory trials.
    
    Quarantine rule: Ground truth is for evaluation only and must NEVER be
    supplied as an input to the operational DiagnosisEngine.
    """
    trial_id: str
    run_id: str
    scenario_id: LabScenario
    ground_truth_label: str
    injection_mechanism: str
    injection_parameters: str
    verification_status: TrialVerificationStatus
    cleanup_status: TrialCleanupStatus
    operator_notes: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.scenario_id, str):
            self.scenario_id = LabScenario(self.scenario_id)
        if isinstance(self.verification_status, str):
            self.verification_status = TrialVerificationStatus(self.verification_status)
        if isinstance(self.cleanup_status, str):
            self.cleanup_status = TrialCleanupStatus(self.cleanup_status)


@dataclass
class ReportExport:
    """Audit log of generated reports and user exports."""
    export_id: str
    run_id: str
    export_format: ExportFormat
    export_timestamp_utc: str
    file_path_redacted: str
    file_size_bytes: Optional[int] = None
    export_status: ExportStatus = ExportStatus.SUCCESS
    error_message: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.export_format, str):
            self.export_format = ExportFormat(self.export_format)
        if isinstance(self.export_status, str):
            self.export_status = ExportStatus(self.export_status)
