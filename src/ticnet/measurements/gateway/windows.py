"""Windows-specific default gateway and network interface discovery parser.

Uses unprivileged OS utilities (`route print` and `ipconfig`) via tokenized subprocess
calls without shell execution. Correlates route metrics, local interface IPs, and
adapter descriptions.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from ticnet.measurements.base import GatewayDiscoveryResult
from ticnet.models.enums import Status
from ticnet.utils.logging import get_logger, mask_public_ip_address

logger = get_logger("measurements.gateway.windows")

# IPv4 Active Route Regex: Network Destination, Netmask, Gateway, Interface, Metric
# Matches standard Windows route print output lines:
# 0.0.0.0          0.0.0.0      192.168.0.1    192.168.0.102     50
IPV4_DEFAULT_ROUTE_PATTERN = re.compile(
    r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\S+)\s+(\S+)\s+(\d+)",
    re.MULTILINE,
)

# IPv6 Default Route Pattern: Interface Index, Metric, Destination, Gateway
# Matches:
# 11    50 ::/0                     fe80::1
IPV6_DEFAULT_ROUTE_PATTERN = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+::/0\s+(\S+)",
    re.MULTILINE,
)

# Interface List Pattern in route print:
# 11...40 d1 33 06 97 a8 ......Intel(R) Wi-Fi 6 AX101
INTERFACE_LIST_PATTERN = re.compile(
    r"^\s*(\d+)\.\.\.[0-9a-fA-F ]*\.{2,}\s*(.+)$",
    re.MULTILINE,
)

# ipconfig Adapter Section Pattern:
# Matches adapter header and key-value lines
ADAPTER_HEADER_PATTERN = re.compile(
    r"^(?:Ethernet adapter|Wireless LAN adapter|PPP adapter)\s+(.+?):",
    re.MULTILINE,
)


from dataclasses import dataclass

@dataclass(frozen=True)
class WindowsRouteCandidate:
    """Internal candidate representation of a parsed default route."""
    gateway_ip: str
    interface_ip: Optional[str]
    metric: int
    ip_family: str  # "IPv4" or "IPv6"
    interface_index: Optional[str] = None


class WindowsGatewayDiscovery:
    """Discovers the default gateway on Windows platforms using native CLI output."""

    def __init__(self, timeout_s: float = 3.0) -> None:
        self.timeout_s = timeout_s

    def execute_command(self, cmd: List[str]) -> Tuple[int, str, str]:
        """Safely execute a Windows command with tokenized arguments."""
        logger.debug("Executing Windows command: %s", " ".join(cmd))
        kwargs: Dict[str, Any] = {
            "shell": False,
            "capture_output": True,
            "timeout": self.timeout_s,
            "check": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = startupinfo

        proc = subprocess.run(cmd, **kwargs)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode, stdout, stderr

    def parse_route_print(self, output: str) -> List[WindowsRouteCandidate]:
        """Parse IPv4 and IPv6 candidate default routes from 'route print' output.
        
        Route Selection Policy:
        - Filters out non-routable gateways (e.g. 'On-link', '0.0.0.0', loopback).
        - Preserves all valid candidate routes with their metrics and local interface IPs.
        """
        candidates: List[WindowsRouteCandidate] = []

        # 1. Parse IPv4 Default Routes (0.0.0.0)
        for match in IPV4_DEFAULT_ROUTE_PATTERN.finditer(output):
            gateway_str = match.group(1).strip()
            interface_str = match.group(2).strip()
            metric_str = match.group(3).strip()

            # Ignore On-link or non-routable addresses
            if gateway_str.lower() in {"on-link", "0.0.0.0"}:
                continue

            try:
                gw_ip = ipaddress.ip_address(gateway_str)
                if gw_ip.is_loopback:
                    continue
                if gw_ip.version != 4:
                    continue
            except ValueError:
                continue

            try:
                metric = int(metric_str)
            except ValueError:
                continue

            # Validate interface IP if provided
            valid_interface_ip = None
            try:
                if_ip = ipaddress.ip_address(interface_str)
                if if_ip.version == 4:
                    valid_interface_ip = str(if_ip)
            except ValueError:
                valid_interface_ip = None

            candidates.append(
                WindowsRouteCandidate(
                    gateway_ip=str(gw_ip),
                    interface_ip=valid_interface_ip,
                    metric=metric,
                    ip_family="IPv4",
                )
            )

        # 2. Parse IPv6 Default Routes (::/0) if no IPv4 candidates or for IPv6 analysis
        for match in IPV6_DEFAULT_ROUTE_PATTERN.finditer(output):
            if_idx = match.group(1).strip()
            metric_str = match.group(2).strip()
            gateway_str = match.group(3).strip()

            if gateway_str.lower() in {"on-link", "::"}:
                continue

            try:
                gw_ip = ipaddress.ip_address(gateway_str)
                if gw_ip.is_loopback or gw_ip.version != 6:
                    continue
            except ValueError:
                continue

            try:
                metric = int(metric_str)
            except ValueError:
                continue

            candidates.append(
                WindowsRouteCandidate(
                    gateway_ip=str(gw_ip),
                    interface_ip=None,
                    metric=metric,
                    ip_family="IPv6",
                    interface_index=if_idx,
                )
            )

        return candidates

    def correlate_interface_name(
        self,
        local_ip: Optional[str],
        gateway_ip: Optional[str],
        ipconfig_output: str,
    ) -> Optional[str]:
        """Correlate adapter connection name from ipconfig output using local IP or gateway IP."""
        if not ipconfig_output or not (local_ip or gateway_ip):
            return None

        current_adapter: Optional[str] = None
        adapter_sections: Dict[str, List[str]] = {}

        for line in ipconfig_output.splitlines():
            line_str = line.strip()
            # Check for adapter header (e.g. "Wireless LAN adapter Wi-Fi:")
            match = ADAPTER_HEADER_PATTERN.match(line.strip())
            if match and match.group(1):
                adapter_name = match.group(1).strip()
                current_adapter = adapter_name
                adapter_sections[adapter_name] = []
            elif current_adapter and line_str:
                adapter_sections[current_adapter].append(line_str)

        for adapter_name, lines in adapter_sections.items():
            section_text = " ".join(lines)
            if local_ip and local_ip in section_text:
                return adapter_name
            if gateway_ip and gateway_ip in section_text:
                return adapter_name

        return None

    def discover(self) -> GatewayDiscoveryResult:
        """Execute Windows route discovery and return normalized GatewayDiscoveryResult."""
        try:
            # 1. Execute route print 0.0.0.0
            returncode, stdout, stderr = self.execute_command(["route", "print", "0.0.0.0"])
            if returncode != 0:
                logger.error("Windows 'route print' failed with code %d: %s", returncode, stderr)
                return GatewayDiscoveryResult(
                    status=Status.FAILED,
                    error_message=f"Windows 'route print' command failed with exit code {returncode}",
                )

            # 2. Parse candidate default routes
            candidates = self.parse_route_print(stdout)
            if not candidates:
                logger.warning("No valid default route candidates found in Windows route print output")
                return GatewayDiscoveryResult(
                    status=Status.FAILED,
                    error_message="No valid default gateway route (0.0.0.0 or ::/0) found in Windows routing table",
                )

            # 3. Route Selection Policy: Lowest metric wins
            candidates.sort(key=lambda c: c.metric)
            best_route = candidates[0]

            # Ambiguity check: if multiple distinct gateways have the identical lowest metric
            tied_lowest = [c for c in candidates if c.metric == best_route.metric]
            unique_tied_gateways = {c.gateway_ip for c in tied_lowest}
            if len(unique_tied_gateways) > 1:
                logger.warning(
                    "Ambiguous routing table: multiple default routes tied at lowest metric %d with gateways: %s",
                    best_route.metric,
                    unique_tied_gateways,
                )
                return GatewayDiscoveryResult(
                    status=Status.INCOMPLETE,
                    error_message=(
                        f"Ambiguous routing table: {len(unique_tied_gateways)} distinct default gateways "
                        f"share the lowest metric ({best_route.metric})"
                    ),
                    metric=best_route.metric,
                )

            # 4. Optional interface name correlation via ipconfig
            interface_name = None
            try:
                ip_code, ip_out, _ = self.execute_command(["ipconfig"])
                if ip_code == 0:
                    interface_name = self.correlate_interface_name(
                        local_ip=best_route.interface_ip,
                        gateway_ip=best_route.gateway_ip,
                        ipconfig_output=ip_out,
                    )
            except Exception as e:
                logger.debug("Failed correlating interface name via ipconfig: %s", e)

            logger.info(
                "Windows default gateway discovered: %s (interface=%s, metric=%d, family=%s)",
                mask_public_ip_address(best_route.gateway_ip),
                interface_name or best_route.interface_ip or "unknown",
                best_route.metric,
                best_route.ip_family,
            )

            return GatewayDiscoveryResult(
                status=Status.SUCCESS,
                gateway_ip=best_route.gateway_ip,
                ip_family=best_route.ip_family,
                interface_name=interface_name,
                local_ip=best_route.interface_ip,
                metric=best_route.metric,
                discovery_source="OS_ROUTE_TABLE",
                raw_route_info={
                    "gateway": best_route.gateway_ip,
                    "interface_ip": best_route.interface_ip,
                    "metric": best_route.metric,
                    "candidate_count": len(candidates),
                },
            )

        except subprocess.TimeoutExpired:
            logger.error("Windows gateway discovery timed out after %ss", self.timeout_s)
            return GatewayDiscoveryResult(
                status=Status.TIMEOUT,
                error_message=f"Windows route discovery command timed out after {self.timeout_s} seconds",
            )
        except FileNotFoundError:
            logger.error("Windows 'route' command executable not found in system PATH")
            return GatewayDiscoveryResult(
                status=Status.FAILED,
                error_message="Windows 'route' command executable not found in PATH",
            )
        except PermissionError as e:
            logger.error("Permission denied executing Windows routing command: %s", e)
            return GatewayDiscoveryResult(
                status=Status.FAILED,
                error_message=f"Permission denied executing Windows routing command: {e}",
            )
        except Exception as e:
            logger.error("Unexpected error during Windows gateway discovery: %s", e)
            return GatewayDiscoveryResult(
                status=Status.FAILED,
                error_message=f"Unexpected error during Windows gateway discovery: {e}",
            )
