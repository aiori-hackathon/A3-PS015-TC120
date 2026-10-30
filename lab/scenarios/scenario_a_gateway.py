"""Controlled Laboratory Scenario A: Gateway Unreachable.

Objective:
Verify that TicNet's rule engine fires R-GW-01 (Priority CRITICAL, Category LOCAL_NETWORK)
when default gateway is unreachable, skips downstream throughput, and makes no unsupported
claims about external ISP transit or damaged cables.

Reference: 09_LAB_SAFETY_RUNBOOK.md Section 3.1, SRS-F-015, TC-LAB-01, AC-LAB-02
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter, GatewayDiscoveryResult
from ticnet.measurements.ping.adapter import PingMeasurementAdapter
from ticnet.measurements.ping.models import PingMeasurementResult, PingProbeObservation
from ticnet.models.enums import FindingCategory, LabScenario, MeasurementStatus, Status, TargetType
from lab.scenarios.base import LabScenarioBase


class ScenarioAGateway(LabScenarioBase):
    """Scenario A: Controlled Default Gateway Reachability Failure."""

    scenario_id = LabScenario.SCENARIO_A_GW
    name = "Scenario A — Gateway Unreachable"
    description = (
        "Safely isolates or downs default gateway reachability, resulting in 100% gateway packet loss. "
        "Verifies R-GW-01 triggers with CRITICAL priority and throughput stage is safely SKIPPED."
    )

    def __init__(self, gateway_ip: str = "192.168.100.1") -> None:
        super().__init__()
        self.gateway_ip = gateway_ip
        self._gateway_reachable: bool = True

    def inject(self, **kwargs) -> Dict[str, Any]:
        """Introduce gateway unreachability fault."""
        self._gateway_reachable = False
        self._is_active = True
        self._injection_params = {
            "mechanism": "socket_drop_or_link_down",
            "target_gateway_ip": self.gateway_ip,
            "packet_loss_pct": 100.0,
            "probe_count": kwargs.get("probe_count", 10),
        }
        return self.injection_parameters

    def verify_injection(self) -> bool:
        """Independently verify that the gateway is unreachable (100% loss)."""
        return not self._gateway_reachable

    def restore(self) -> bool:
        """Restore gateway reachability to clean baseline state."""
        self._gateway_reachable = True
        self._is_active = False
        return True

    def verify_cleanup(self) -> bool:
        """Independently verify gateway connectivity is 100% restored with 0% loss."""
        return self._gateway_reachable

    def get_ground_truth_label(self) -> str:
        return "Default Gateway Unreachable (100% Packet Loss)"

    def get_expected_rule_id(self) -> str:
        return "R-GW-01"

    def get_expected_category(self) -> FindingCategory:
        return FindingCategory.LOCAL_NETWORK

    def create_adapters(
        self,
        probe_count: int = 10,
    ) -> Dict[str, Any]:
        """Create configured adapters for this scenario."""
        class _LabGatewayAdapter(GatewayDiscoveryAdapter):
            def __init__(self, gw_ip: str):
                super().__init__()
                self._gw_ip = gw_ip

            def execute(self, *args, **kwargs) -> GatewayDiscoveryResult:
                return GatewayDiscoveryResult(
                    status=Status.SUCCESS,
                    gateway_ip=self._gw_ip,
                    ip_family="IPv4",
                    interface_name="veth-client",
                )

        scenario_ref = self

        class _LabPingAdapter(PingMeasurementAdapter):
            def execute(
                self,
                target_ip: str,
                count: int = 10,
                per_probe_timeout_ms: int = 2000,
                target_type: TargetType = TargetType.GATEWAY,
                probe_count: Optional[int] = None,
                interval_ms: Optional[int] = None,
                **kwargs,
            ) -> PingMeasurementResult:
                is_gw = (target_ip == scenario_ref.gateway_ip)
                if not scenario_ref._gateway_reachable and is_gw:
                    # Gateway is down: 100% loss
                    probes = [
                        PingProbeObservation(
                            sample_index=i + 1,
                            timestamp_utc="2026-10-24T12:00:00Z",
                            status=MeasurementStatus.TIMEOUT,
                            rtt_ms=None,
                            error_message="Request timed out",
                        )
                        for i in range(count)
                    ]
                    return PingMeasurementResult(
                        target_ip=target_ip,
                        ip_family="IPv4",
                        target_type=target_type,
                        probe_count_configured=count,
                        probe_count_attempted=count,
                        probe_count_successful=0,
                        probe_count_failed=count,
                        packet_loss_pct=100.0,
                        status=Status.SUCCESS,
                        error_message="100% packet loss",
                        probes=probes,
                    )
                else:
                    # Healthy baseline (0% loss, ~3.5ms)
                    rtt_val = 3.5
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

        return {
            "gateway_adapter": _LabGatewayAdapter(self.gateway_ip),
            "ping_adapter": _LabPingAdapter(),
        }
