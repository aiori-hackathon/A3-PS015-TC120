"""Data models for throughput measurement adapter (TC-ADPT-04).

Defines structured measurement results for bounded HTTP response streaming,
throughput calculations (bps, Mbps), duration and byte cap indicators,
and mapping to core domain entities (Observation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional

from ticnet.models.entities import Observation, new_id, now_utc_iso
from ticnet.models.enums import MeasurementStatus, Status


@dataclass(frozen=True)
class ThroughputMeasurementResult:
    """Result of a bounded HTTP download throughput measurement.
    
    Invariants:
    - Never fabricates throughput: If status is not SUCCESS, throughput_bps and
      throughput_mbps MUST be None.
    - Captures actual payload bytes received and measured monotonic elapsed time.
    - Explicitly flags when hard duration cap (10s) or byte cap (25MB) was reached.
    """
    target_url: str
    status: Status = Status.SUCCESS
    bytes_transferred: int = 0
    elapsed_s: Optional[float] = None
    throughput_bps: Optional[float] = None
    throughput_mbps: Optional[float] = None
    http_status_code: Optional[int] = None
    duration_cap_hit: bool = False
    byte_cap_hit: bool = False
    chunks_received: int = 0
    error_message: Optional[str] = None
    start_time_utc: str = field(default_factory=now_utc_iso)
    end_time_utc: str = field(default_factory=now_utc_iso)

    def __post_init__(self):
        if self.bytes_transferred < 0:
            raise ValueError("bytes_transferred cannot be negative")
        if self.elapsed_s is not None and self.elapsed_s < 0:
            raise ValueError("elapsed_s cannot be negative")
        if self.chunks_received < 0:
            raise ValueError("chunks_received cannot be negative")

        # Enforce non-zero null invariant on failures/timeouts
        if self.status != Status.SUCCESS and (
            self.throughput_bps is not None or self.throughput_mbps is not None
        ):
            raise ValueError(
                f"throughput values must be None when status is {self.status.value}"
            )

    def to_observation(
        self,
        run_id: str,
        stage_id: Optional[str] = None,
        target_id: Optional[str] = None,
        sample_index: int = 1,
    ) -> Observation:
        """Convert result to a persistent domain Observation entity."""
        obs_status = (
            MeasurementStatus.SUCCESS
            if self.status == Status.SUCCESS
            else (
                MeasurementStatus.TIMEOUT
                if self.status == Status.TIMEOUT
                else MeasurementStatus.FAILED
            )
        )

        metadata: Dict[str, Any] = {
            "target_url": self.target_url,
            "bytes_transferred": self.bytes_transferred,
            "elapsed_s": self.elapsed_s,
            "throughput_bps": self.throughput_bps,
            "throughput_mbps": self.throughput_mbps,
            "http_status_code": self.http_status_code,
            "duration_cap_hit": self.duration_cap_hit,
            "byte_cap_hit": self.byte_cap_hit,
            "chunks_received": self.chunks_received,
            "direction": "download",
            "unit_standard": "decimal_mbps",
        }

        duration_ms = (
            round(self.elapsed_s * 1000.0, 3)
            if self.elapsed_s is not None and self.elapsed_s >= 0
            else 0.0
        )

        return Observation(
            observation_id=new_id(),
            run_id=run_id,
            stage_id=stage_id,
            target_id=target_id,
            sample_index=sample_index,
            timestamp_utc=self.start_time_utc,
            duration_ms=duration_ms,
            metric_name="download_throughput",
            unit="Mbps",
            status=obs_status,
            value_numeric=self.throughput_mbps if obs_status == MeasurementStatus.SUCCESS else None,
            error_message=self.error_message,
            method_metadata=json.dumps(metadata, separators=(",", ":")),
        )

    def to_observations(
        self,
        run_id: str,
        stage_id: Optional[str] = None,
        target_id: Optional[str] = None,
        sample_index: int = 1,
    ) -> List[Observation]:
        """Convert throughput measurement to a list containing a single Observation entity."""
        return [self.to_observation(run_id=run_id, stage_id=stage_id, target_id=target_id, sample_index=sample_index)]
