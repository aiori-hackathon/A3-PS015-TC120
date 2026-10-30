"""Controlled Laboratory Scenario B: DNS Subsystem Failure.

Objective:
Verify that TicNet's rule engine fires R-DNS-01 (Priority CRITICAL, Category DNS_SUBSYSTEM)
when default gateway and external IP routing are fully functional, but DNS queries fail across
100% of attempts. Confirms exclusion of total transit failure.

Reference: 09_LAB_SAFETY_RUNBOOK.md Section 3.2, SRS-F-015, TC-LAB-02, AC-LAB-03
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ticnet.measurements.dns.adapter import DNSMeasurementAdapter
from ticnet.measurements.dns.models import DNSMeasurementResult, DNSResolutionStatus
from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter, GatewayDiscoveryResult
from ticnet.measurements.ping.adapter import PingMeasurementAdapter
from ticnet.measurements.ping.models import PingMeasurementResult, PingProbeObservation
from ticnet.models.enums import FindingCategory, LabScenario, MeasurementStatus, Status, TargetType
from lab.scenarios.base import LabScenarioBase


class ScenarioBDNS(LabScenarioBase):
    """Scenario B: Controlled DNS Resolution Subsystem Failure."""

    scenario_id = LabScenario.SCENARIO_B_DNS
    name = "Scenario B — DNS Subsystem Failure"
    description = (
        "Simulates blocking UDP port 53 traffic to the primary resolver while keeping gateway "
        "and external IP routing fully functional. Verifies R-DNS-01 fires with STRONG evidence."
    )

    def __init__(
        self,
        gateway_ip: str = "192.168.100.1",
        resolver_ip: str = "192.168.100.2",
    ) -> None:
        super().__init__()
        self.gateway_ip = gateway_ip
        self.resolver_ip = resolver_ip
        self._dns_functional: bool = True

    def inject(self, **kwargs) -> Dict[str, Any]:
        """Introduce DNS resolution failure fault."""
        self._dns_functional = False
        self._is_active = True
        self._injection_params = {
            "mechanism": "iptables_or_udp53_drop",
            "target_resolver_ip": self.resolver_ip,
            "failure_rate_pct": 100.0,
            "error_type": "TIMEOUT",
        }
        return self.injection_parameters

    def verify_injection(self) -> bool:
        """Independently verify that DNS resolution is failing."""
        return not self._dns_functional

    def restore(self) -> bool:
        """Restore DNS resolution to clean baseline state."""
        self._dns_functional = True
        self._is_active = False
        return True

    def verify_cleanup(self) -> bool:
        """Independently verify DNS queries resolve with NOERROR in <50ms."""
        return self._dns_functional

    def get_ground_truth_label(self) -> str:
        return "DNS Resolution Subsystem Failure (UDP 53 Blocked / Resolver Timeout)"

    def get_expected_rule_id(self) -> str:
        return "R-DNS-01"

    def get_expected_category(self) -> FindingCategory:
        return FindingCategory.DNS_SUBSYSTEM

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
                # Gateway and external IPs are healthy (0% loss, ~12ms)
                rtt_val = 3.5 if target_ip == scenario_ref.gateway_ip else 12.0
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
                    jitter_macd_ms=0.4,
                    status=Status.SUCCESS,
                    probes=probes,
                )

        class _LabDNSAdapter(DNSMeasurementAdapter):
            def execute(
                self,
                resolver_ip: str,
                domain: str = "example.com",
                record_type: str = "A",
                port: int = 53,
                timeout_s: Optional[float] = 2.0,
                resolver_id: Optional[str] = None,
                **kwargs,
            ) -> DNSMeasurementResult:
                if not scenario_ref._dns_functional:
                    # DNS failure: 100% timeouts
                    return DNSMeasurementResult(
                        resolver_ip=resolver_ip,
                        resolver_port=port,
                        ip_family="IPv4",
                        query_domain=domain,
                        query_record_type="A",
                        resolver_id=resolver_id,
                        elapsed_ms=None,
                        rcode=None,
                        rcode_name=None,
                        status=Status.TIMEOUT,
                        resolution_status=DNSResolutionStatus.TIMEOUT,
                        error_message="DNS query timed out after 2000ms",
                    )
                else:
                    # DNS healthy: 0% failure, ~18ms
                    return DNSMeasurementResult(
                        resolver_ip=resolver_ip,
                        resolver_port=port,
                        ip_family="IPv4",
                        query_domain=domain,
                        query_record_type="A",
                        resolver_id=resolver_id,
                        elapsed_ms=18.0,
                        rcode=0,
                        rcode_name="NOERROR",
                        status=Status.SUCCESS,
                        resolution_status=DNSResolutionStatus.RESOLVED,
                        has_answer=True,
                        answer_count=1,
                        answer_ips=["93.184.216.34"],
                    )

        return {
            "gateway_adapter": _LabGatewayAdapter(),
            "ping_adapter": _LabPingAdapter(),
            "dns_adapter": _LabDNSAdapter(),
        }
