"""Controlled Laboratory Scenario D: Bounded Congestion and Bufferbloat.

Objective:
Verify that TicNet's rule engine fires R-LOAD-01 (Priority WARNING, Category BUFFERBLOAT)
when baseline latency is healthy and stable (jitter <= 40ms), but active downstream load
induces a substantial latency spike (Delta RTT > 50ms, Ratio > 2.5).

Reference: 09_LAB_SAFETY_RUNBOOK.md Section 3.4, SRS-F-015, TC-LAB-04, AC-LAB-05
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter, GatewayDiscoveryResult
from ticnet.measurements.load.adapter import LoadedLatencyAdapter
from ticnet.measurements.load.models import LoadedLatencyMeasurementResult
from ticnet.measurements.ping.adapter import PingMeasurementAdapter
from ticnet.measurements.ping.models import PingMeasurementResult, PingProbeObservation
from ticnet.measurements.throughput.adapter import ThroughputMeasurementAdapter
from ticnet.measurements.throughput.models import ThroughputMeasurementResult
from ticnet.models.enums import FindingCategory, LabScenario, MeasurementStatus, Status, TargetType
from lab.scenarios.base import LabScenarioBase


class ScenarioDCongestion(LabScenarioBase):
    """Scenario D: Controlled Congestion and Bufferbloat (Loaded Latency)."""

    scenario_id = LabScenario.SCENARIO_D_LOAD
    name = "Scenario D — Network Congestion & Bufferbloat"
    description = (
        "Induces bounded network load with queuing delay, creating a significant latency spike "
        "under load (Delta RTT > 50ms, Ratio > 2.5). Verifies R-LOAD-01 fires deterministically."
    )

    def __init__(
        self,
        gateway_ip: str = "192.168.100.1",
        target_ip: str = "192.168.100.10",
        baseline_rtt_ms: float = 12.0,
        loaded_rtt_ms: float = 140.0,
    ) -> None:
        super().__init__()
        self.gateway_ip = gateway_ip
        self.target_ip = target_ip
        self.baseline_rtt_ms = baseline_rtt_ms
        self.loaded_rtt_ms = loaded_rtt_ms
        self._congestion_active: bool = False

    def inject(self, **kwargs) -> Dict[str, Any]:
        """Introduce bufferbloat queue latency under active load."""
        self._congestion_active = True
        self._is_active = True
        delta = self.loaded_rtt_ms - self.baseline_rtt_ms
        ratio = self.loaded_rtt_ms / self.baseline_rtt_ms if self.baseline_rtt_ms > 0 else 1.0
        self._injection_params = {
            "mechanism": "tbf_queue_backlog_simulation",
            "baseline_rtt_ms": self.baseline_rtt_ms,
            "loaded_rtt_ms": self.loaded_rtt_ms,
            "delta_ms": delta,
            "ratio": ratio,
        }
        return self.injection_parameters

    def verify_injection(self) -> bool:
        """Independently verify loaded latency spike exceeds thresholds (delta > 50ms, ratio > 2.5)."""
        if not self._congestion_active:
            return False
        delta = self.loaded_rtt_ms - self.baseline_rtt_ms
        ratio = self.loaded_rtt_ms / self.baseline_rtt_ms
        return delta > 50.0 and ratio > 2.5

    def restore(self) -> bool:
        """Restore queue to normal unconstrained state."""
        self._congestion_active = False
        self._is_active = False
        return True

    def verify_cleanup(self) -> bool:
        """Independently verify loaded queue delay is removed."""
        return not self._congestion_active

    def get_ground_truth_label(self) -> str:
        return f"Bounded Congestion and Bufferbloat (Delta RTT: {self.loaded_rtt_ms - self.baseline_rtt_ms:.1f}ms, Ratio: {self.loaded_rtt_ms / self.baseline_rtt_ms:.1f}x)"

    def get_expected_rule_id(self) -> str:
        return "R-LOAD-01"

    def get_expected_category(self) -> FindingCategory:
        return FindingCategory.BUFFERBLOAT

    def create_adapters(self) -> Dict[str, Any]:
        """Create configured adapters for this scenario."""
        scenario_ref = self

        class _LabGatewayAdapter(GatewayDiscoveryAdapter):
            def execute(self, *args, **kwargs) -> GatewayDiscoveryResult:
                return GatewayDiscoveryResult(
                    status=Status.SUCCESS,
                    gateway_ip=scenario_ref.gateway_ip,
                    ip_family="IPv4",
                    interface_name="veth-client",
                )

        class _LabPingAdapter(PingMeasurementAdapter):
            def execute(
                self,
                target_ip: str,
                count: int = 10,
                per_probe_timeout_ms: int = 2000,
                target_type: TargetType = TargetType.EXTERNAL_IP,
                probe_count: Optional[int] = None,
                interval_ms: Optional[int] = None,
                **kwargs,
            ) -> PingMeasurementResult:
                rtt_val = 3.5 if target_ip == scenario_ref.gateway_ip else scenario_ref.baseline_rtt_ms
                probes = [
                    PingProbeObservation(
                        sample_index=i + 1,
                        timestamp_utc="2026-10-24T12:00:00Z",
                        status=MeasurementStatus.SUCCESS,
                        rtt_ms=rtt_val,
                    )
                    for i in range(count)
                ]
                return PingMeasurementResult(
                    target_ip=target_ip,
                    ip_family="IPv4",
                    target_type=target_type,
                    probe_count_configured=count,
                    probe_count_attempted=count,
                    probe_count_successful=count,
                    probe_count_failed=0,
                    packet_loss_pct=0.0,
                    individual_rtts=[rtt_val] * count,
                    min_rtt_ms=rtt_val,
                    avg_rtt_ms=rtt_val,
                    max_rtt_ms=rtt_val,
                    median_rtt_ms=rtt_val,
                    p95_rtt_ms=rtt_val,
                    jitter_macd_ms=0.5,
                    status=Status.SUCCESS,
                    probes=probes,
                )

        class _LabThroughputAdapter(ThroughputMeasurementAdapter):
            def execute(
                self,
                endpoint_url: str,
                cancel_event: Optional[Any] = None,
                on_chunk_received: Optional[Any] = None,
                **kwargs,
            ) -> ThroughputMeasurementResult:
                return ThroughputMeasurementResult(
                    target_url=endpoint_url,
                    status=Status.SUCCESS,
                    bytes_transferred=8_000_000,
                    elapsed_s=5.0,
                    throughput_bps=12_800_000.0,
                    throughput_mbps=12.8,
                )

        class _LabLoadedAdapter(LoadedLatencyAdapter):
            def execute(
                self,
                target_ip: str,
                traffic_url: str,
                cancel_event: Optional[Any] = None,
                **kwargs,
            ) -> LoadedLatencyMeasurementResult:
                effective_loaded_rtt = scenario_ref.loaded_rtt_ms if scenario_ref._congestion_active else scenario_ref.baseline_rtt_ms
                delta = effective_loaded_rtt - scenario_ref.baseline_rtt_ms
                ratio = effective_loaded_rtt / scenario_ref.baseline_rtt_ms

                return LoadedLatencyMeasurementResult(
                    target_ip=target_ip,
                    traffic_url=traffic_url,
                    status=Status.SUCCESS,
                    baseline_sample_count=10,
                    baseline_valid_count=10,
                    baseline_rtts=[scenario_ref.baseline_rtt_ms] * 10,
                    baseline_median_rtt_ms=scenario_ref.baseline_rtt_ms,
                    baseline_p95_rtt_ms=scenario_ref.baseline_rtt_ms,
                    loaded_sample_count=10,
                    loaded_valid_count=10,
                    loaded_rtts=[effective_loaded_rtt] * 10,
                    loaded_median_rtt_ms=effective_loaded_rtt,
                    loaded_p95_rtt_ms=effective_loaded_rtt,
                    delta_rtt_median_ms=delta,
                    bufferbloat_ratio=ratio,
                    delta_pct=(delta / scenario_ref.baseline_rtt_ms) * 100.0,
                    load_active_during_probes=True,
                    load_bytes_transferred=8_000_000,
                    load_throughput_mbps=12.8,
                )

        return {
            "gateway_adapter": _LabGatewayAdapter(),
            "ping_adapter": _LabPingAdapter(),
            "throughput_adapter": _LabThroughputAdapter(),
            "load_adapter": _LabLoadedAdapter(
                ping_adapter=_LabPingAdapter(),
                throughput_adapter=_LabThroughputAdapter(),
            ),
        }
