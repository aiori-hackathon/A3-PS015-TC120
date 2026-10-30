"""Domain enumerations for TicNet.

This module defines typed enumerations used across measurement, workflow,
persistence, and diagnostic layers.

Status Semantics:
- TIMEOUT: A measurement did not complete within its allowed timeout window.
- BLOCKED: Probing or resolution was blocked, prohibited, or refused by environment/firewall.
- FAILED: Operation failed for a recorded system, socket, or execution error.
- UNSUPPORTED: Capability is unavailable on the current platform or configuration.
- INCOMPLETE: Operation produced only partial results before terminating.
- CANCELLED: Operation was explicitly cancelled by user or workflow controller.
- SKIPPED: Operation was deliberately not run (e.g. skipped due to upstream failure).
- SUCCESS: Operation completed according to its defined measurement contract;
           does NOT mean the network is healthy.
"""

from enum import Enum


class Status(str, Enum):
    """General workflow and execution status enumeration."""
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    INCOMPLETE = "INCOMPLETE"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class MeasurementStatus(str, Enum):
    """Status outcomes for individual measurement probe attempts."""
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class RunStatus(str, Enum):
    """Lifecycle status of a diagnostic test run."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageStatus(str, Enum):
    """Lifecycle status of an individual diagnostic stage."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class RunMode(str, Enum):
    """Execution mode of the diagnostic run."""
    OPERATIONAL = "OPERATIONAL"
    LAB = "LAB"


class TargetType(str, Enum):
    """Classification of network target destinations."""
    GATEWAY = "GATEWAY"
    EXTERNAL_IP = "EXTERNAL_IP"
    DNS_RESOLVER = "DNS_RESOLVER"
    THROUGHPUT_ENDPOINT = "THROUGHPUT_ENDPOINT"


class FindingCategory(str, Enum):
    """Functional failure domain category for diagnostic findings."""
    LOCAL_NETWORK = "LOCAL_NETWORK"
    DNS_SUBSYSTEM = "DNS_SUBSYSTEM"
    EXTERNAL_PATH = "EXTERNAL_PATH"
    BUFFERBLOAT = "BUFFERBLOAT"
    INCONCLUSIVE = "INCONCLUSIVE"


class FindingPriority(str, Enum):
    """Triage priority for diagnostic findings."""
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class FindingStatus(str, Enum):
    """Evaluation status of a diagnostic rule."""
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceStrength(str, Enum):
    """Qualitative evidence strength rating.
    
    Must not be confused with uncalibrated probabilities.
    """
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    LIMITED = "LIMITED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceRelationship(str, Enum):
    """Relationship between a diagnostic finding and stored observation/summary."""
    SUPPORTING = "SUPPORTING"
    CONFLICTING = "CONFLICTING"
    REQUIRED_MISSING = "REQUIRED_MISSING"


class LabScenario(str, Enum):
    """Controlled fault scenarios in the isolated laboratory."""
    SCENARIO_A_GW = "SCENARIO_A_GW"
    SCENARIO_B_DNS = "SCENARIO_B_DNS"
    SCENARIO_C_LOSS = "SCENARIO_C_LOSS"
    SCENARIO_D_LOAD = "SCENARIO_D_LOAD"


class TrialVerificationStatus(str, Enum):
    """Outcome of comparing diagnostic findings against laboratory ground truth."""
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    INCONCLUSIVE = "INCONCLUSIVE"
    INVALID_TRIAL = "INVALID_TRIAL"


class TrialCleanupStatus(str, Enum):
    """Post-trial network restoration verification status."""
    CLEANED_VERIFIED = "CLEANED_VERIFIED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class ExportFormat(str, Enum):
    """Supported serialized report export formats."""
    JSON = "JSON"
    CSV = "CSV"
    MARKDOWN = "MARKDOWN"


class ExportStatus(str, Enum):
    """Outcome of report serialization and export."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
