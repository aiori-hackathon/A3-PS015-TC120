"""Data models for ICMP ping measurements and observations.

Defines typed dataclasses for individual probe observations and aggregated measurement runs,
with bidirectional mapping to core domain entities (Observation, Summary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ticnet.models.entities import Observation, Summary, new_id, now_utc_iso
from ticnet.models.enums import MeasurementStatus, Status, TargetType


@dataclass(frozen=True)
class PingProbeObservation:
    """Individual probe observation within a multi-probe ping measurement."""
    sample_index: int
    timestamp_utc: str
    status: MeasurementStatus
    rtt_ms: Optional[float] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.sample_index < 1:
            raise ValueError("sample_index must be >= 1")
        # Enforce non-zero null invariant
        if self.status != MeasurementStatus.SUCCESS and self.rtt_ms is not None:
            raise ValueError("rtt_ms must be None when probe status is not SUCCESS")


@dataclass(frozen=True)
class PingMeasurementResult:
    """Aggregated result of an ICMP ping measurement sequence against a network target."""
    target_ip: str
    ip_family: str  # "IPv4" or "IPv6"
    target_type: TargetType = TargetType.GATEWAY
    start_time_utc: str = field(default_factory=now_utc_iso)
    end_time_utc: str = field(default_factory=now_utc_iso)
    probe_count_configured: int = 4
    probe_count_attempted: int = 0
    probe_count_successful: int = 0
    probe_count_failed: int = 0
    packet_loss_pct: Optional[float] = None
    individual_rtts: List[float] = field(default_factory=list)
    min_rtt_ms: Optional[float] = None
    avg_rtt_ms: Optional[float] = None
    max_rtt_ms: Optional[float] = None
    median_rtt_ms: Optional[float] = None
    p95_rtt_ms: Optional[float] = None
    jitter_macd_ms: Optional[float] = None
    status: Status = Status.SUCCESS
    error_message: Optional[str] = None
    probes: List[PingProbeObservation] = field(default_factory=list)

    def to_observations(
        self,
        run_id: str,
        stage_id: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> List[Observation]:
        """Convert individual probe observations to domain Observation entities for persistence."""
        observations: List[Observation] = []
        for p in self.probes:
            obs = Observation(
                observation_id=new_id(),
                run_id=run_id,
                stage_id=stage_id,
                target_id=target_id,
                sample_index=p.sample_index,
                timestamp_utc=p.timestamp_utc,
                duration_ms=p.rtt_ms if p.rtt_ms is not None else 0.0,
                metric_name="round_trip_time",
                value_numeric=p.rtt_ms if p.status == MeasurementStatus.SUCCESS else None,
                unit="ms",
                status=p.status,
                error_message=p.error_message,
                method_metadata=f"ICMP Echo ({self.ip_family})",
            )
            observations.append(obs)
        return observations

    def to_summaries(
        self,
        run_id: str,
        target_id: Optional[str] = None,
    ) -> List[Summary]:
        """Convert statistical summary metrics to domain Summary entities for persistence."""
        summaries: List[Summary] = []

        # 1. Packet Loss Summary
        if self.packet_loss_pct is not None:
            summaries.append(
                Summary(
                    summary_id=new_id(),
                    run_id=run_id,
                    target_id=target_id,
                    metric_name="packet_loss",
                    statistic_name="loss_percentage",
                    unit="%",
                    sample_count_total=self.probe_count_attempted,
                    sample_count_valid=self.probe_count_attempted,
                    calculation_method="Standard Probe Ratio",
                    calculation_version="1.0.0",
                    value_numeric=self.packet_loss_pct,
                )
            )

        # 2. Median RTT Summary (requires valid samples >= 3)
        if self.median_rtt_ms is not None:
            summaries.append(
                Summary(
                    summary_id=new_id(),
                    run_id=run_id,
                    target_id=target_id,
                    metric_name="round_trip_time",
                    statistic_name="median",
                    unit="ms",
                    sample_count_total=self.probe_count_attempted,
                    sample_count_valid=self.probe_count_successful,
                    calculation_method="Sample Median (n>=3)",
                    calculation_version="1.0.0",
                    value_numeric=self.median_rtt_ms,
                )
            )

        # 3. 95th Percentile RTT Summary (requires valid samples >= 10)
        if self.p95_rtt_ms is not None:
            summaries.append(
                Summary(
                    summary_id=new_id(),
                    run_id=run_id,
                    target_id=target_id,
                    metric_name="round_trip_time",
                    statistic_name="p95",
                    unit="ms",
                    sample_count_total=self.probe_count_attempted,
                    sample_count_valid=self.probe_count_successful,
                    calculation_method="Nearest-Rank Percentile (n>=10)",
                    calculation_version="1.0.0",
                    value_numeric=self.p95_rtt_ms,
                )
            )

        # 4. MACD Delay Variation (Jitter) Summary (requires valid samples >= 2)
        if self.jitter_macd_ms is not None:
            summaries.append(
                Summary(
                    summary_id=new_id(),
                    run_id=run_id,
                    target_id=target_id,
                    metric_name="delay_variation",
                    statistic_name="macd_jitter",
                    unit="ms",
                    sample_count_total=self.probe_count_attempted,
                    sample_count_valid=self.probe_count_successful,
                    calculation_method="Mean Absolute Consecutive Difference (MACD)",
                    calculation_version="1.0.0",
                    value_numeric=self.jitter_macd_ms,
                )
            )

        return summaries
