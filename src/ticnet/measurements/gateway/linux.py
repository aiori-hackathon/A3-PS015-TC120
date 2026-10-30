"""Linux-specific default gateway and network interface discovery parser.

Uses unprivileged OS utilities (`ip route show default` and `ip -6 route show default`)
via tokenized subprocess calls without shell execution, with fallback to `/proc/net/route`.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import socket
import struct
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from ticnet.measurements.base import GatewayDiscoveryResult
from ticnet.models.enums import Status
from ticnet.utils.logging import get_logger, mask_public_ip_address

logger = get_logger("measurements.gateway.linux")

# Matches: default via 192.168.1.1 dev eth0 proto dhcp metric 100
# or: default dev ppp0 scope link
# or: default via fe80::1 dev eth0 metric 1024
IP_ROUTE_DEFAULT_PATTERN = re.compile(
    r"^default(?:\s+via\s+(?P<gateway>\S+))?(?:\s+dev\s+(?P<dev>\S+))?(?:.*?(?:\s+src\s+(?P<src>\S+)))?(?:.*?(?:\s+metric\s+(?P<metric>\d+)))?",
    re.MULTILINE,
)


@dataclass(frozen=True)
class LinuxRouteCandidate:
    """Internal candidate representation of a parsed Linux default route."""
    gateway_ip: Optional[str]
    interface_name: Optional[str]
    local_ip: Optional[str]
    metric: int
    ip_family: str  # "IPv4" or "IPv6"


class LinuxGatewayDiscovery:
    """Discovers the default gateway on Linux platforms."""

    def __init__(self, timeout_s: float = 3.0, proc_route_path: Optional[Path] = None) -> None:
        self.timeout_s = timeout_s
        self.proc_route_path = proc_route_path or Path("/proc/net/route")

    def execute_command(self, cmd: List[str]) -> Tuple[int, str, str]:
        """Safely execute a Linux command with tokenized arguments."""
        logger.debug("Executing Linux command: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            timeout=self.timeout_s,
            check=False,
        )
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode, stdout, stderr

    def parse_ip_route(self, output: str, ip_family: str = "IPv4") -> List[LinuxRouteCandidate]:
        """Parse Linux 'ip route' or 'ip -6 route' default output."""
        candidates: List[LinuxRouteCandidate] = []
        for line in output.splitlines():
            line_str = line.strip()
            if not line_str.startswith("default"):
                continue

            match = IP_ROUTE_DEFAULT_PATTERN.match(line_str)
            if not match:
                continue

            gw_str = match.group("gateway")
            dev_str = match.group("dev")
            src_str = match.group("src")
            metric_str = match.group("metric")

            validated_gw: Optional[str] = None
            if gw_str:
                try:
                    ip_obj = ipaddress.ip_address(gw_str)
                    if not ip_obj.is_loopback:
                        validated_gw = str(ip_obj)
                except ValueError:
                    validated_gw = None

            validated_src: Optional[str] = None
            if src_str:
                try:
                    src_obj = ipaddress.ip_address(src_str)
                    validated_src = str(src_obj)
                except ValueError:
                    validated_src = None

            metric = int(metric_str) if metric_str else 0

            candidates.append(
                LinuxRouteCandidate(
                    gateway_ip=validated_gw,
                    interface_name=dev_str,
                    local_ip=validated_src,
                    metric=metric,
                    ip_family=ip_family,
                )
            )

        return candidates

    def parse_proc_net_route(self) -> List[LinuxRouteCandidate]:
        """Fallback parser for /proc/net/route when 'ip' executable is unavailable."""
        candidates: List[LinuxRouteCandidate] = []
        if not self.proc_route_path.is_file():
            return candidates

        try:
            content = self.proc_route_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            if len(lines) <= 1:
                return candidates

            # Header: Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT
            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) < 8:
                    continue
                iface, dest_hex, gw_hex, flags_hex, _, _, metric_str, _ = parts[:8]

                # Destination 00000000 indicates default route
                if dest_hex == "00000000":
                    try:
                        # Gateway is little-endian 32-bit hex
                        gw_int = int(gw_hex, 16)
                        if gw_int == 0:
                            continue
                        gw_ip = socket.inet_ntoa(struct.pack("<L", gw_int))
                        metric = int(metric_str)
                        candidates.append(
                            LinuxRouteCandidate(
                                gateway_ip=gw_ip,
                                interface_name=iface,
                                local_ip=None,
                                metric=metric,
                                ip_family="IPv4",
                            )
                        )
                    except (ValueError, OSError):
                        continue
        except Exception as e:
            logger.debug("Error reading %s: %s", self.proc_route_path, e)

        return candidates

    def discover(self) -> GatewayDiscoveryResult:
        """Execute Linux route discovery and return normalized GatewayDiscoveryResult."""
        candidates: List[LinuxRouteCandidate] = []
        ip_cmd_available = True

        # 1. Try 'ip route show default' for IPv4
        try:
            returncode, stdout, stderr = self.execute_command(["ip", "route", "show", "default"])
            if returncode == 0:
                candidates.extend(self.parse_ip_route(stdout, ip_family="IPv4"))
            else:
                logger.debug("Linux 'ip route show default' returned %d: %s", returncode, stderr)
        except FileNotFoundError:
            ip_cmd_available = False
            logger.debug("'ip' command not found, falling back to /proc/net/route")
        except subprocess.TimeoutExpired:
            return GatewayDiscoveryResult(
                status=Status.TIMEOUT,
                error_message=f"Linux 'ip route' command timed out after {self.timeout_s} seconds",
            )
        except PermissionError as e:
            return GatewayDiscoveryResult(
                status=Status.FAILED,
                error_message=f"Permission denied executing Linux route discovery: {e}",
            )

        # 2. Try 'ip -6 route show default' for IPv6 if no IPv4 routes found
        if ip_cmd_available and not candidates:
            try:
                code_v6, out_v6, _ = self.execute_command(["ip", "-6", "route", "show", "default"])
                if code_v6 == 0:
                    candidates.extend(self.parse_ip_route(out_v6, ip_family="IPv6"))
            except Exception as e:
                logger.debug("IPv6 route query failed: %s", e)

        # 3. Fallback to /proc/net/route if no candidates found or 'ip' unavailable
        if not candidates and not ip_cmd_available:
            candidates.extend(self.parse_proc_net_route())
            if not candidates:
                return GatewayDiscoveryResult(
                    status=Status.FAILED,
                    error_message="'ip' command is unavailable and /proc/net/route contains no default route",
                )

        if not candidates:
            return GatewayDiscoveryResult(
                status=Status.FAILED,
                error_message="No default gateway route found in Linux routing table",
            )

        # 4. Filter routes with valid gateway IPs
        routes_with_gw = [c for c in candidates if c.gateway_ip is not None]
        if not routes_with_gw:
            # e.g., Point-to-point interface without gateway address (default dev ppp0)
            best_incomplete = candidates[0]
            return GatewayDiscoveryResult(
                status=Status.INCOMPLETE,
                interface_name=best_incomplete.interface_name,
                metric=best_incomplete.metric,
                error_message="Default route exists but specifies no next-hop gateway IP address",
            )

        # 5. Route Selection Policy: Lowest metric wins
        routes_with_gw.sort(key=lambda c: c.metric)
        best_route = routes_with_gw[0]

        # Ambiguity check: multiple distinct gateways tied at lowest metric
        tied_lowest = [c for c in routes_with_gw if c.metric == best_route.metric]
        unique_gateways = {c.gateway_ip for c in tied_lowest}
        if len(unique_gateways) > 1:
            logger.warning(
                "Ambiguous Linux routing table: %d distinct default gateways share lowest metric (%d)",
                len(unique_gateways),
                best_route.metric,
            )
            return GatewayDiscoveryResult(
                status=Status.INCOMPLETE,
                error_message=(
                    f"Ambiguous routing table: {len(unique_gateways)} distinct default gateways "
                    f"share the lowest metric ({best_route.metric})"
                ),
                metric=best_route.metric,
            )

        logger.info(
            "Linux default gateway discovered: %s (dev=%s, metric=%d, family=%s)",
            mask_public_ip_address(best_route.gateway_ip or ""),
            best_route.interface_name or "unknown",
            best_route.metric,
            best_route.ip_family,
        )

        return GatewayDiscoveryResult(
            status=Status.SUCCESS,
            gateway_ip=best_route.gateway_ip,
            ip_family=best_route.ip_family,
            interface_name=best_route.interface_name,
            local_ip=best_route.local_ip,
            metric=best_route.metric,
            discovery_source="OS_ROUTE_TABLE",
            raw_route_info={
                "gateway": best_route.gateway_ip,
                "interface": best_route.interface_name,
                "metric": best_route.metric,
                "candidate_count": len(candidates),
            },
        )
