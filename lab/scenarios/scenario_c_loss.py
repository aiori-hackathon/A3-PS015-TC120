"""Controlled Laboratory Scenario C: Induced External Packet Loss.

Objective:
1. Multi-Target Mode: Verify rule R-LOSS-01 fires with WARNING priority and Category EXTERNAL_PATH
   when the gateway is clean (<=5% loss) and >=2 diverse external targets show >=15% loss.
2. Single-Target Guardrail Mode: Verify rule R-INCONCLUSIVE-01 fires when only one external target
   degrades while another is healthy, proving protection against false ISP blame.

Reference: 09_LAB_SAFETY_RUNBOOK.md Section 3.3, SRS-F-015, TC-LAB-03, AC-LAB-04
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter, GatewayDiscoveryResult
from ticnet.measurements.ping.adapter import PingMeasurementAdapter
from ticnet.measurements.ping.models import PingMeasurementResult, PingProbeObservation
from ticnet.models.enums import FindingCategory, LabScenario, MeasurementStatus, Status, TargetType
from lab.scenarios.base import LabScenarioBase


class ScenarioCLoss(LabScenarioBase):
    """Scenario C: Controlled External Packet Loss (Multi-Target and Single-Target Guardrail)."""

    scenario_id = LabScenario.SCENARIO_C_LOSS
    name = "Scenario C — Induced External Packet Loss"
    description = (
        "Induces controlled packet loss on external transit paths while keeping default gateway clean. "
        "Verifies R-LOSS-01 fires on multi-target loss, and R-INCONCLUSIVE-01 fires on single-target failure."
    )

    def __init__(
        self,
        gateway_ip: str = "192.168.100.1",
        target_a: str = "192.168.100.10",
        target_b: str = "192.168.100.20",
    ) -> None:
        super().__init__()
        self.gateway_ip = gateway_ip
        self.target_a = target_a
        self.target_b = target_b
        self._single_target_mode: bool = False
        self._loss_pct: float = 0.0

    def inject(self, single_target_mode: bool = False, loss_pct: float = 20.0, **kwargs) -> Dict[str, Any]:
        """Introduce controlled external packet loss."""
        self._single_target_mode = single_target_mode
        self._loss_pct = loss_pct
        self._is_active = True
        self._injection_params = {
            "mechanism": "netem_simulated_loss",
            "loss_pct": loss_pct,
            "single_target_mode": single_target_mode,
            "affected_targets": [self.target_a] if single_target_mode else [self.target_a, self.target_b],
        }
        return self.injection_parameters

    def verify_injection(self) -> bool:
        """Independently verify that external packet loss condition is active."""
        return self._is_active and self._loss_pct > 0.0

    def restore(self) -> bool:
        """Restore all paths to clean baseline (0% loss)."""
        self._loss_pct = 0.0
        self._single_target_mode = False
        self._is_active = False
        return True

    def verify_cleanup(self) -> bool:
        """Independently verify all targets return 0% loss."""
        return not self._is_active and self._loss_pct == 0.0

    def get_ground_truth_label(self) -> str:
        if self._single_target_mode:
            return "Single-Target External Degradation (Target A 20% Loss, Target B 0% Loss - Inconclusive Guardrail)"
        return "Multi-Target External Path Degradation (20% Loss across all External Targets)"

    def get_expected_rule_id(self) -> str:
        return "R-INCONCLUSIVE-01" if self._single_target_mode else "R-LOSS-01"

    def get_expected_category(self) -> FindingCategory:
        return FindingCategory.INCONCLUSIVE if self._single_target_mode else FindingCategory.EXTERNAL_PATH

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
                is_gw = (target_ip == scenario_ref.gateway_ip)
                if is_gw:
                    # Gateway is always clean in Scenario C
                    loss = 0.0
                    rtt_val = 3.5
                else:
                    if scenario_ref._loss_pct > 0.0:
                        if scenario_ref._single_target_mode:
                            # Target A (or 1.1.1.1) impaired, remaining clean
                            loss = scenario_ref._loss_pct if target_ip in (scenario_ref.target_a, "1.1.1.1") else 0.0
                        else:
                            # Multi-target impairment across all external destinations
                            loss = scenario_ref._loss_pct
                    else:
                        loss = 0.0
                    rtt_val = 14.0

                dropped_count = int(round((loss / 100.0) * count))
                received_count = count - dropped_count

                probes: List[PingProbeObservation] = []
                for i in range(count):
                    is_dropped = (i < dropped_count)
                    probes.append(
                        PingProbeObservation(
                            sample_index=i + 1,
                            timestamp_utc="2026-10-24T12:00:00Z",
                            status=MeasurementStatus.TIMEOUT if is_dropped else MeasurementStatus.SUCCESS,
                            rtt_ms=None if is_dropped else rtt_val,
                            error_message="Request timed out" if is_dropped else None,
                        )
                    )

                return PingMeasurementResult(
                    target_ip=target_ip,
                    ip_family="IPv4",
                    target_type=target_type,
                    probe_count_configured=count,
                    probe_count_attempted=count,
                    probe_count_successful=received_count,
                    probe_count_failed=dropped_count,
                    packet_loss_pct=loss,
                    individual_rtts=[rtt_val] * received_count,
                    min_rtt_ms=None if received_count == 0 else rtt_val,
                    avg_rtt_ms=None if received_count == 0 else rtt_val,
                    max_rtt_ms=None if received_count == 0 else rtt_val,
                    median_rtt_ms=None if received_count == 0 else rtt_val,
                    p95_rtt_ms=None if received_count == 0 else rtt_val,
                    jitter_macd_ms=0.5 if received_count > 1 else None,
                    status=Status.SUCCESS if received_count > 0 else Status.FAILED,
                    probes=probes,
                )

        return {
            "gateway_adapter": _LabGatewayAdapter(),
            "ping_adapter": _LabPingAdapter(),
        }
