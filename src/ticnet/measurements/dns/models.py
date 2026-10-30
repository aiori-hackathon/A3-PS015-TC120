"""Data models and response code representations for DNS measurements.

Defines typed dataclasses for individual DNS query results, response code mappings,
and domain entity conversions (Observation) for database persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Union

import dns.rcode

from ticnet.models.entities import Observation, new_id, now_utc_iso
from ticnet.models.enums import MeasurementStatus, Status, TargetType


# Standard DNS RCODE Names (RFC 1035, RFC 2136, RFC 6895)
RCODE_NAMES: Dict[int, str] = {
    0: "NOERROR",
    1: "FORMERR",
    2: "SERVFAIL",
    3: "NXDOMAIN",
    4: "NOTIMP",
    5: "REFUSED",
    6: "YXDOMAIN",
    7: "YXRRSET",
    8: "NXRRSET",
    9: "NOTAUTH",
    10: "NOTZONE",
}


class DNSResolutionStatus(str, Enum):
    """Semantic outcome of DNS name resolution distinguishing transport from resolution."""
    RESOLVED = "RESOLVED"       # Query succeeded and returned at least one matching answer
    NO_ANSWER = "NO_ANSWER"     # Query succeeded at transport but domain has no matching records (NOERROR + 0 matching answers)
    NO_DATA = "NO_ANSWER"       # Backward-compatible alias for NO_ANSWER
    NXDOMAIN = "NXDOMAIN"       # Non-existent domain (RCODE=3)
    SERVFAIL = "SERVFAIL"       # Name server failure (RCODE=2)
    REFUSED = "REFUSED"         # Query refused by server (RCODE=5)
    FORMERR = "FORMERR"         # Format error reported by server (RCODE=1)
    OTHER_ERROR = "OTHER_ERROR" # Valid response with other non-zero RCODE
    TRUNCATED = "TRUNCATED"     # Response truncated over UDP (TC=1)
    TIMEOUT = "TIMEOUT"         # Query timed out waiting for resolver
    FAILED = "FAILED"           # Transport error or malformed packet


def determine_resolution_status(
    status: Status,
    rcode: Optional[int] = None,
    has_answer: bool = False,
    is_truncated: bool = False,
) -> DNSResolutionStatus:
    """Determine the semantic resolution outcome distinguishing transport from resolution."""
    if status == Status.TIMEOUT:
        return DNSResolutionStatus.TIMEOUT
    if status == Status.FAILED:
        return DNSResolutionStatus.FAILED
    if is_truncated or status == Status.INCOMPLETE:
        return DNSResolutionStatus.TRUNCATED
    if rcode is None:
        return DNSResolutionStatus.FAILED
    if rcode == 0:
        return DNSResolutionStatus.RESOLVED if has_answer else DNSResolutionStatus.NO_ANSWER
    if rcode == 1:
        return DNSResolutionStatus.FORMERR
    if rcode == 2:
        return DNSResolutionStatus.SERVFAIL
    if rcode == 3:
        return DNSResolutionStatus.NXDOMAIN
    if rcode == 5:
        return DNSResolutionStatus.REFUSED
    return DNSResolutionStatus.OTHER_ERROR


def get_rcode_name(rcode: int) -> str:
    """Return canonical DNS RCODE name from numeric integer.
    
    Falls back to dnspython lookup or 'UNKNOWN_<codeid>'.
    """
    if rcode in RCODE_NAMES:
        return RCODE_NAMES[rcode]
    try:
        return dns.rcode.to_text(dns.rcode.Rcode(rcode))
    except Exception:
        return f"UNKNOWN_{rcode}"


@dataclass(frozen=True)
class DNSMeasurementResult:
    """Normalized empirical result of a direct DNS query against a designated resolver."""

    resolver_ip: str
    resolver_port: int
    ip_family: str  # "IPv4" or "IPv6"
    query_domain: str
    query_record_type: str = "A"
    resolver_id: Optional[str] = None
    start_time_utc: str = field(default_factory=now_utc_iso)
    end_time_utc: str = field(default_factory=now_utc_iso)
    elapsed_ms: Optional[float] = None
    rcode: Optional[int] = None
    rcode_name: Optional[str] = None
    status: Status = Status.SUCCESS
    resolution_status: Union[DNSResolutionStatus, str] = DNSResolutionStatus.RESOLVED
    is_truncated: bool = False
    has_answer: bool = False
    answer_count: int = 0
    answer_ips: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    transaction_id: Optional[int] = None

    def __post_init__(self):
        # Normalize resolution_status if passed as string
        if isinstance(self.resolution_status, str) and not isinstance(self.resolution_status, DNSResolutionStatus):
            object.__setattr__(self, "resolution_status", DNSResolutionStatus(self.resolution_status))
        # Strict Invariant: Missingness is NOT zero
        if self.status not in (Status.SUCCESS, Status.INCOMPLETE) and self.elapsed_ms is not None:
            raise ValueError(
                f"elapsed_ms must be None when query status is {self.status.value}, got {self.elapsed_ms}"
            )
        if self.elapsed_ms is not None and self.elapsed_ms < 0:
            raise ValueError(f"elapsed_ms cannot be negative, got {self.elapsed_ms}")

    def to_observation(
        self,
        run_id: str,
        stage_id: Optional[str] = None,
        target_id: Optional[str] = None,
        sample_index: int = 1,
    ) -> Observation:
        """Convert DNS measurement result to domain Observation entity for SQLite persistence."""
        obs_status = (
            MeasurementStatus.SUCCESS
            if self.status == Status.SUCCESS
            else (
                MeasurementStatus.TIMEOUT
                if self.status == Status.TIMEOUT
                else MeasurementStatus.FAILED
            )
        )

        metadata_dict: Dict[str, Any] = {
            "resolver_ip": self.resolver_ip,
            "resolver_port": self.resolver_port,
            "domain": self.query_domain,
            "record_type": self.query_record_type,
            "rcode": self.rcode,
            "rcode_name": self.rcode_name,
            "transport_status": self.status.value,
            "resolution_status": self.resolution_status.value if isinstance(self.resolution_status, Enum) else self.resolution_status,
            "is_truncated": self.is_truncated,
            "has_answer": self.has_answer,
            "answer_count": self.answer_count,
            "answer_ips": self.answer_ips,
            "transaction_id": self.transaction_id,
        }

        return Observation(
            observation_id=new_id(),
            run_id=run_id,
            stage_id=stage_id,
            target_id=target_id,
            sample_index=sample_index,
            timestamp_utc=self.start_time_utc,
            duration_ms=self.elapsed_ms if self.elapsed_ms is not None else 0.0,
            metric_name="dns_query_time",
            value_numeric=self.elapsed_ms if obs_status == MeasurementStatus.SUCCESS else None,
            unit="ms",
            status=obs_status,
            error_message=self.error_message,
            method_metadata=json.dumps(metadata_dict),
        )
