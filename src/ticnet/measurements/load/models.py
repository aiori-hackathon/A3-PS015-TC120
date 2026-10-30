"""Data models for loaded latency / bufferbloat adapter (TC-ADPT-05).

Defines structured measurement results comparing idle baseline round-trip
latency against latency during active, bounded throughput load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional

from ticnet.models.entities import Observation, new_id, now_utc_iso
from ticnet.models.enums import MeasurementStatus, Status


@dataclass(frozen=True)
class LoadedLatencyMeasurementResult:
    """Result of an idle baseline vs. active traffic loaded latency measurement.
    
    Invariants:
    - Real evidence: Uses empirical baseline and loaded RTT distributions.
    - Zero/invalid denominator safety: Ratio and percentage delta are None if
      baseline latency is zero or unavailable.
    - Missingness honesty: If status is not SUCCESS, delta metrics are None.
    - Temporal overlap tracking: Explicitly discloses whether traffic load was
      actively running during the latency probe sample window.
    """
    target_ip: str
    traffic_url: str
    status: Status = Status.SUCCESS
    baseline_sample_count: int = 0
    baseline_valid_count: int = 0
    baseline_rtts: List[float] = field(default_factory=list)
    baseline_median_rtt_ms: Optional[float] = None
    baseline_p95_rtt_ms: Optional[float] = None
    loaded_sample_count: int = 0
    loaded_valid_count: int = 0
    loaded_rtts: List[float] = field(default_factory=list)
    loaded_median_rtt_ms: Optional[float] = None
    loaded_p95_rtt_ms: Optional[float] = None
    delta_rtt_median_ms: Optional[float] = None
    bufferbloat_ratio: Optional[float] = None
    delta_pct: Optional[float] = None
    load_active_during_probes: bool = False
    load_bytes_transferred: int = 0
    load_throughput_mbps: Optional[float] = None
    error_message: Optional[str] = None
    start_time_utc: str = field(default_factory=now_utc_iso)
    end_time_utc: str = field(default_factory=now_utc_iso)

    def __post_init__(self):
        if self.baseline_sample_count < 0 or self.loaded_sample_count < 0:
            raise ValueError("Sample counts cannot be negative")
        if self.baseline_valid_count > self.baseline_sample_count:
            raise ValueError("baseline_valid_count cannot exceed baseline_sample_count")
        if self.loaded_valid_count > self.loaded_sample_count:
            raise ValueError("loaded_valid_count cannot exceed loaded_sample_count")

        # Non-zero nulls on failed measurements
        if self.status != Status.SUCCESS and (
            self.delta_rtt_median_ms is not None
            or self.bufferbloat_ratio is not None
            or self.delta_pct is not None
        ):
            raise ValueError(
                f"Delta and ratio metrics must be None when status is {self.status.value}"
            )

    def to_observations(
        self,
        run_id: str,
        stage_id: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> List[Observation]:
        """Convert loaded latency results to domain Observation entities."""
        obs_list: List[Observation] = []
        idx = 1

        # 1. Summary delta observation
        meas_status = (
            MeasurementStatus.SUCCESS
            if self.status == Status.SUCCESS
            else (
                MeasurementStatus.TIMEOUT
                if self.status == Status.TIMEOUT
                else MeasurementStatus.FAILED
            )
        )

        metadata: Dict[str, Any] = {
            "target_ip": self.target_ip,
            "traffic_url": self.traffic_url,
            "baseline_median_rtt_ms": self.baseline_median_rtt_ms,
            "baseline_p95_rtt_ms": self.baseline_p95_rtt_ms,
            "baseline_valid_count": self.baseline_valid_count,
            "baseline_sample_count": self.baseline_sample_count,
            "loaded_median_rtt_ms": self.loaded_median_rtt_ms,
            "loaded_p95_rtt_ms": self.loaded_p95_rtt_ms,
            "loaded_valid_count": self.loaded_valid_count,
            "loaded_sample_count": self.loaded_sample_count,
            "delta_rtt_median_ms": self.delta_rtt_median_ms,
            "bufferbloat_ratio": self.bufferbloat_ratio,
            "delta_pct": self.delta_pct,
            "load_active_during_probes": self.load_active_during_probes,
            "load_bytes_transferred": self.load_bytes_transferred,
            "load_throughput_mbps": self.load_throughput_mbps,
        }

        delta_obs = Observation(
            observation_id=new_id(),
            run_id=run_id,
            stage_id=stage_id,
            target_id=target_id,
            sample_index=idx,
            timestamp_utc=self.end_time_utc,
            duration_ms=0.0,
            metric_name="loaded_latency_delta",
            unit="ms",
            status=meas_status,
            value_numeric=self.delta_rtt_median_ms if meas_status == MeasurementStatus.SUCCESS else None,
            error_message=self.error_message,
            method_metadata=json.dumps(metadata, separators=(",", ":")),
        )
        obs_list.append(delta_obs)

        # 2. Individual loaded probe observations
        for loaded_rtt in self.loaded_rtts:
            idx += 1
            obs_list.append(
                Observation(
                    observation_id=new_id(),
                    run_id=run_id,
                    stage_id=stage_id,
                    target_id=target_id,
                    sample_index=idx,
                    timestamp_utc=self.end_time_utc,
                    duration_ms=loaded_rtt,
                    metric_name="loaded_rtt",
                    unit="ms",
                    status=MeasurementStatus.SUCCESS,
                    value_numeric=loaded_rtt,
                    error_message=None,
                    method_metadata=json.dumps({"phase": "loaded", "target_ip": self.target_ip}),
                )
            )

        return obs_list
