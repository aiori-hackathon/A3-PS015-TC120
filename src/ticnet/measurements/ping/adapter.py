"""Ping measurement adapter and gateway reachability orchestrator.

Implements empirical ICMP Echo reachability measurement for Windows and Linux:
- Safe, tokenized subprocess execution strictly with shell=False.
- Finite overall execution deadlines with bounded probe counts and timeouts.
- Rigorous statistical aggregation conforming to 04_MEASUREMENT_METHODOLOGY.md.
- Integration with domain Status and TargetType enums.
- Respects gateway discovery results without hardcoding default gateways.
"""

from __future__ import annotations

import ipaddress
import subprocess
import sys
from typing import Any, Optional, Tuple, Type

from ticnet.configuration.validator import TargetsConfig
from ticnet.measurements.base import MeasurementAdapter
from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter
from ticnet.measurements.ping.linux import LinuxPingParser
from ticnet.measurements.ping.models import PingMeasurementResult, PingProbeObservation
from ticnet.measurements.ping.windows import WindowsPingParser
from ticnet.measurements.statistics import (
    calculate_delay_summary_stats,
    calculate_packet_loss_pct,
)
from ticnet.models.entities import now_utc_iso
from ticnet.models.enums import MeasurementStatus, Status, TargetType
from ticnet.utils.logging import get_logger, mask_public_ip_address

logger = get_logger("measurements.ping.adapter")

# Bounded configuration safety ceilings
MIN_PROBE_COUNT = 1
MAX_PROBE_COUNT = 50
DEFAULT_PROBE_COUNT = 4

MIN_PROBE_TIMEOUT_MS = 100
MAX_PROBE_TIMEOUT_MS = 5000
DEFAULT_PROBE_TIMEOUT_MS = 1000


class PingMeasurementAdapter(MeasurementAdapter):
    """Executes empirical ICMP Echo reachability probes against a validated target IP."""

    def __init__(
        self,
        timeout_s: Optional[float] = None,
        platform_name: Optional[str] = None,
        windows_parser: Optional[Type[WindowsPingParser]] = None,
        linux_parser: Optional[Type[LinuxPingParser]] = None,
    ) -> None:
        # Default base timeout_s to 10.0s if unspecified
        super().__init__(timeout_s=timeout_s if timeout_s is not None else 10.0)
        self._platform = platform_name or sys.platform
        self._windows_parser = windows_parser or WindowsPingParser
        self._linux_parser = linux_parser or LinuxPingParser

    @property
    def adapter_name(self) -> str:
        return "PingMeasurementAdapter"

    @property
    def supported_platforms(self) -> Tuple[str, ...]:
        return ("win32", "linux")

    def execute(
        self,
        target_ip: str,
        count: int = DEFAULT_PROBE_COUNT,
        per_probe_timeout_ms: int = DEFAULT_PROBE_TIMEOUT_MS,
        target_type: TargetType = TargetType.GATEWAY,
        probe_count: Optional[int] = None,
        interval_ms: Optional[int] = None,
        **kwargs: Any,
    ) -> PingMeasurementResult:
        """Execute bounded ICMP Echo probes against target IP and return statistical results.
        
        Args:
            target_ip: Validated IPv4 or IPv6 destination address.
            count: Number of probes to dispatch (1 to 50).
            per_probe_timeout_ms: Per-probe reply timeout in ms (100 to 5000).
            target_type: Domain target classification (GATEWAY, EXTERNAL, etc.).
            probe_count: Optional alias for count (used by composite adapters).
            interval_ms: Optional per-probe spacing in ms.
            
        Returns:
            Normalized PingMeasurementResult instance.
        """
        if probe_count is not None:
            count = probe_count
        start_time_iso = now_utc_iso()

        # 1. Validate Target IP
        if not isinstance(target_ip, str) or not target_ip.strip():
            logger.error("Target IP address is missing or empty")
            return PingMeasurementResult(
                target_ip=target_ip,
                ip_family="UNKNOWN",
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count if isinstance(count, int) and not isinstance(count, bool) else 0,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message="Target IP address cannot be empty or invalid",
            )

        try:
            ip_obj = ipaddress.ip_address(target_ip.strip())
            family = "IPv6" if ip_obj.version == 6 else "IPv4"
            validated_ip = str(ip_obj)
        except ValueError as e:
            logger.error("Invalid target IP address provided: %s", target_ip)
            return PingMeasurementResult(
                target_ip=target_ip,
                ip_family="UNKNOWN",
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count if isinstance(count, int) and not isinstance(count, bool) else 0,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message=f"Invalid target IP address: '{target_ip}' ({e})",
            )

        # 2. Validate Probe Count Bounds
        if not isinstance(count, int) or isinstance(count, bool) or count < MIN_PROBE_COUNT or count > MAX_PROBE_COUNT:
            logger.error("Invalid probe count: %s", count)
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count if isinstance(count, int) and not isinstance(count, bool) else 0,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message=f"Probe count {count} violates safety bounds ({MIN_PROBE_COUNT} to {MAX_PROBE_COUNT})",
            )

        # 3. Validate Per-Probe Timeout Bounds
        if (
            not isinstance(per_probe_timeout_ms, int)
            or isinstance(per_probe_timeout_ms, bool)
            or per_probe_timeout_ms < MIN_PROBE_TIMEOUT_MS
            or per_probe_timeout_ms > MAX_PROBE_TIMEOUT_MS
        ):
            logger.error("Invalid probe timeout: %s", per_probe_timeout_ms)
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message=(
                    f"Probe timeout {per_probe_timeout_ms}ms violates safety bounds "
                    f"({MIN_PROBE_TIMEOUT_MS}ms to {MAX_PROBE_TIMEOUT_MS}ms)"
                ),
            )

        # 4. Check Operating System Support
        is_windows = self._platform.startswith("win32")
        is_linux = self._platform.startswith("linux")
        if not (is_windows or is_linux):
            logger.warning("Ping measurement requested on unsupported platform: %s", self._platform)
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.UNSUPPORTED,
                error_message=f"ICMP ping measurement is unsupported on platform '{self._platform}'",
            )

        # 5. Build Platform-Specific Tokenized Command Arguments
        if is_windows:
            cmd = ["ping", "-n", str(count), "-w", str(per_probe_timeout_ms), validated_ip]
        else:
            # Linux iputils-ping
            timeout_s_flag = str(max(1, round(per_probe_timeout_ms / 1000.0)))
            cmd = ["ping", "-n", "-c", str(count), "-W", timeout_s_flag, validated_ip]

        # Calculate finite execution deadline: (probes * per-probe timeout) + safety headroom
        overall_timeout_s = (count * (per_probe_timeout_ms / 1000.0)) + 3.0
        if self.timeout_s is not None and self.timeout_s > overall_timeout_s:
            overall_timeout_s = self.timeout_s

        logger.info(
            "Executing ping probe: target=%s (%s), count=%d, per_probe_timeout=%dms, overall_deadline=%.1fs",
            mask_public_ip_address(validated_ip),
            family,
            count,
            per_probe_timeout_ms,
            overall_timeout_s,
        )

        # 6. Execute Subprocess with shell=False
        try:
            returncode, stdout, stderr = self.run_command(cmd, timeout_s=overall_timeout_s)
        except subprocess.TimeoutExpired:
            logger.error("Ping subprocess timed out after %.1fs", overall_timeout_s)
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.TIMEOUT,
                error_message=f"Ping command execution timed out after {overall_timeout_s:.1f}s",
            )
        except FileNotFoundError as e:
            logger.error("Ping executable not found on system: %s", e)
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message=f"Ping executable not found on system: {e}",
            )
        except PermissionError as e:
            logger.error("Permission denied executing ping utility: %s", e)
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message=f"Permission denied executing ping utility: {e}",
            )
        except Exception as e:
            logger.exception("Unexpected error executing ping command: %s", e)
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                probe_count_configured=count,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message=f"Subprocess execution error: {e}",
            )

        end_time_iso = now_utc_iso()

        # 7. Parse stdout Using Platform-Specific Parser
        if is_windows:
            probes = self._windows_parser.parse_probes(stdout, expected_count=count)
        else:
            probes = self._linux_parser.parse_probes(stdout, expected_count=count)

        # Handle unparseable stdout
        if not probes:
            logger.warning("Ping command returned unparseable output (exit_code=%d)", returncode)
            sanitized_err = (stderr.strip() or stdout.strip())[:256]
            return PingMeasurementResult(
                target_ip=validated_ip,
                ip_family=family,
                target_type=target_type,
                start_time_utc=start_time_iso,
                end_time_utc=end_time_iso,
                probe_count_configured=count,
                probe_count_attempted=0,
                probe_count_successful=0,
                probe_count_failed=0,
                packet_loss_pct=None,
                status=Status.FAILED,
                error_message=sanitized_err or f"Ping failed with exit code {returncode} and unparseable output",
            )

        # 8. Compute Empirical Statistics
        attempted = len(probes)
        successful_rtts = [
            p.rtt_ms for p in probes if p.status == MeasurementStatus.SUCCESS and p.rtt_ms is not None
        ]
        successful = len(successful_rtts)
        failed = attempted - successful

        loss_pct = calculate_packet_loss_pct(
            attempted_probes=attempted,
            successful_responses=successful,
        )
        delay_stats = calculate_delay_summary_stats(successful_rtts)

        return PingMeasurementResult(
            target_ip=validated_ip,
            ip_family=family,
            target_type=target_type,
            start_time_utc=start_time_iso,
            end_time_utc=end_time_iso,
            probe_count_configured=count,
            probe_count_attempted=attempted,
            probe_count_successful=successful,
            probe_count_failed=failed,
            packet_loss_pct=loss_pct,
            individual_rtts=successful_rtts,
            min_rtt_ms=delay_stats["min_rtt"],
            avg_rtt_ms=delay_stats["avg_rtt"],
            max_rtt_ms=delay_stats["max_rtt"],
            median_rtt_ms=delay_stats["median_rtt"],
            p95_rtt_ms=delay_stats["p95_rtt"],
            jitter_macd_ms=delay_stats["jitter_macd"],
            status=Status.SUCCESS,
            error_message=None,
            probes=probes,
        )


def probe_gateway_reachability(
    config: Optional[TargetsConfig] = None,
    target_ip: Optional[str] = None,
    count: Optional[int] = None,
    per_probe_timeout_ms: Optional[int] = None,
    discovery_adapter: Optional[GatewayDiscoveryAdapter] = None,
    ping_adapter: Optional[PingMeasurementAdapter] = None,
) -> PingMeasurementResult:
    """Orchestrates default gateway discovery and empirical reachability probing.
    
    Resolution Priority:
    1. Explicit target_ip argument if supplied.
    2. Explicit manual_override_ip in TargetsConfig if configured.
    3. Dynamic gateway discovery via GatewayDiscoveryAdapter.
    
    Safety Guardrail:
    If discovery returns FAILED, INCOMPLETE, TIMEOUT, or UNSUPPORTED, reachability
    probing terminates safely without probing arbitrary or fallback addresses.
    """
    resolved_count = count if count is not None else (config.gateway.probe_count if config else DEFAULT_PROBE_COUNT)
    resolved_timeout = (
        per_probe_timeout_ms
        if per_probe_timeout_ms is not None
        else (config.gateway.probe_timeout_ms if config else DEFAULT_PROBE_TIMEOUT_MS)
    )

    resolved_ip: Optional[str] = target_ip
    target_family: str = "IPv4"

    # If no explicit target IP provided, resolve from config or discovery
    if not resolved_ip:
        if config and config.gateway.manual_override_ip:
            resolved_ip = config.gateway.manual_override_ip
        else:
            disc = discovery_adapter or GatewayDiscoveryAdapter()
            disc_result = disc.execute(config=config)
            if disc_result.status != Status.SUCCESS or not disc_result.gateway_ip:
                logger.warning(
                    "Gateway discovery did not yield a valid target (status=%s): %s",
                    disc_result.status.value,
                    disc_result.error_message,
                )
                return PingMeasurementResult(
                    target_ip=disc_result.gateway_ip or "0.0.0.0",
                    ip_family=disc_result.ip_family or "IPv4",
                    target_type=TargetType.GATEWAY,
                    start_time_utc=disc_result.timestamp_utc,
                    end_time_utc=now_utc_iso(),
                    probe_count_configured=resolved_count,
                    probe_count_attempted=0,
                    probe_count_successful=0,
                    probe_count_failed=0,
                    packet_loss_pct=None,
                    status=disc_result.status,
                    error_message=f"Gateway discovery did not succeed: {disc_result.error_message or disc_result.status.value}",
                )
            resolved_ip = disc_result.gateway_ip
            if disc_result.ip_family:
                target_family = disc_result.ip_family

    pinger = ping_adapter or PingMeasurementAdapter()
    return pinger.execute(
        target_ip=resolved_ip,
        count=resolved_count,
        per_probe_timeout_ms=resolved_timeout,
        target_type=TargetType.GATEWAY,
    )
