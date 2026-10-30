"""Unit tests for Phase 3: Platform Network Adapter and Gateway Discovery.

Tests deterministic parsing of Windows and Linux routing table outputs,
route-selection policies (lowest metric), failure and timeout behaviors,
subprocess security (no shell=True), and configuration integration.
"""

import ipaddress
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from ticnet.configuration.validator import GatewayConfig, TargetsConfig
from ticnet.measurements.base import GatewayDiscoveryResult, MeasurementAdapter
from ticnet.measurements.gateway.discovery import (
    GatewayDiscoveryAdapter,
    discover_gateway,
)
from ticnet.measurements.gateway.linux import (
    LinuxGatewayDiscovery,
    LinuxRouteCandidate,
)
from ticnet.measurements.gateway.windows import (
    WindowsGatewayDiscovery,
    WindowsRouteCandidate,
)
from ticnet.models.enums import Status, TargetType


# ============================================================================
# Synthetic Mock Outputs
# ============================================================================

WINDOWS_ROUTE_PRINT_STANDARD = """
===========================================================================
Interface List
  4...d4 93 90 63 3d af ......Realtek PCIe GbE Family Controller
 11...40 d1 33 06 97 a8 ......Intel(R) Wi-Fi 6 AX101
  1...........................Software Loopback Interface 1
===========================================================================

IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.0.1    192.168.0.102     50
        127.0.0.0        255.0.0.0         On-link         127.0.0.1    331
===========================================================================
Persistent Routes:
  None
"""

WINDOWS_ROUTE_PRINT_MULTIPLE_METRICS = """
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      10.8.0.1       10.8.0.42         20
          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.50      50
          0.0.0.0          0.0.0.0      192.168.0.1    192.168.0.102     100
===========================================================================
"""

WINDOWS_ROUTE_PRINT_TIED_METRICS = """
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.50      25
          0.0.0.0          0.0.0.0      10.0.0.1       10.0.0.50         25
===========================================================================
"""

WINDOWS_ROUTE_PRINT_IPV6 = """
IPv6 Route Table
===========================================================================
Active Routes:
 If Metric Network Destination      Gateway
 11     50 ::/0                     fe80::1
===========================================================================
"""

WINDOWS_ROUTE_PRINT_NO_DEFAULT = """
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
        127.0.0.0        255.0.0.0         On-link         127.0.0.1    331
      192.168.0.0    255.255.255.0         On-link     192.168.0.102    306
===========================================================================
"""

WINDOWS_IPCONFIG_STANDARD = """
Windows IP Configuration

Ethernet adapter Ethernet:
   Media State . . . . . . . . . . . : Media disconnected

Wireless LAN adapter Wi-Fi:
   Connection-specific DNS Suffix  . : home
   IPv4 Address. . . . . . . . . . . : 192.168.0.102
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.0.1
"""

LINUX_IP_ROUTE_STANDARD = (
    "default via 192.168.1.1 dev eth0 proto dhcp metric 100 \n"
    "192.168.1.0/24 dev eth0 proto kernel scope link src 192.168.1.50 metric 100 \n"
)

LINUX_IP_ROUTE_MULTIPLE = (
    "default via 10.0.0.1 dev enp3s0 proto dhcp metric 50 \n"
    "default via 192.168.1.1 dev wlan0 proto dhcp metric 600 \n"
)

LINUX_IP_ROUTE_TIED = (
    "default via 192.168.1.1 dev eth0 metric 100 \n"
    "default via 10.0.0.1 dev eth1 metric 100 \n"
)

LINUX_IP_ROUTE_NO_GATEWAY = (
    "default dev ppp0 scope link \n"
)

LINUX_IP_ROUTE_IPV6 = (
    "default via 2001:db8::1 dev eth0 metric 1024 pref medium \n"
)

PROC_NET_ROUTE_SYNTHETIC = (
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
    "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
)


# ============================================================================
# 1. Windows Parsing Unit Tests
# ============================================================================

@pytest.mark.unit
def test_windows_parse_standard_default_route():
    """Verify standard Windows IPv4 route print extracts gateway, interface, and metric."""
    discovery = WindowsGatewayDiscovery()
    candidates = discovery.parse_route_print(WINDOWS_ROUTE_PRINT_STANDARD)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.gateway_ip == "192.168.0.1"
    assert cand.interface_ip == "192.168.0.102"
    assert cand.metric == 50
    assert cand.ip_family == "IPv4"


@pytest.mark.unit
def test_windows_multiple_routes_selection_lowest_metric(monkeypatch):
    """Verify route selection chooses the lowest metric candidate when multiple exist."""
    discovery = WindowsGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, WINDOWS_ROUTE_PRINT_MULTIPLE_METRICS, "") if cmd[0] == "route" else (0, "", ""),
    )

    result = discovery.discover()
    assert result.status == Status.SUCCESS
    # 10.8.0.1 has metric 20, beating 50 and 100
    assert result.gateway_ip == "10.8.0.1"
    assert result.metric == 20
    assert result.local_ip == "10.8.0.42"
    assert result.ip_family == "IPv4"


@pytest.mark.unit
def test_windows_tied_lowest_metric_returns_incomplete(monkeypatch):
    """Verify that multiple distinct gateways tied at the lowest metric yields INCOMPLETE."""
    discovery = WindowsGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, WINDOWS_ROUTE_PRINT_TIED_METRICS, "") if cmd[0] == "route" else (0, "", ""),
    )

    result = discovery.discover()
    assert result.status == Status.INCOMPLETE
    assert "Ambiguous routing table" in (result.error_message or "")
    assert result.metric == 25


@pytest.mark.unit
def test_windows_ipv6_route_parsing():
    """Verify Windows IPv6 default route (::/0) is parsed when present."""
    discovery = WindowsGatewayDiscovery()
    candidates = discovery.parse_route_print(WINDOWS_ROUTE_PRINT_IPV6)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.gateway_ip == "fe80::1"
    assert cand.metric == 50
    assert cand.ip_family == "IPv6"


@pytest.mark.unit
def test_windows_interface_name_correlation():
    """Verify adapter name ('Wi-Fi') is correlated from ipconfig output using local IP."""
    discovery = WindowsGatewayDiscovery()
    adapter_name = discovery.correlate_interface_name(
        local_ip="192.168.0.102",
        gateway_ip="192.168.0.1",
        ipconfig_output=WINDOWS_IPCONFIG_STANDARD,
    )
    assert adapter_name == "Wi-Fi"


@pytest.mark.unit
def test_windows_no_default_route_returns_failed(monkeypatch):
    """Verify missing default route in Windows output returns FAILED without making up a gateway."""
    discovery = WindowsGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, WINDOWS_ROUTE_PRINT_NO_DEFAULT, ""),
    )

    result = discovery.discover()
    assert result.status == Status.FAILED
    assert result.gateway_ip is None
    assert "No valid default gateway route" in (result.error_message or "")


@pytest.mark.unit
def test_windows_malformed_command_output_returns_failed(monkeypatch):
    """Verify corrupt or unexpected command output is handled safely."""
    discovery = WindowsGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, "Garbled binary junk \x00\xff invalid header", ""),
    )

    result = discovery.discover()
    assert result.status == Status.FAILED
    assert result.gateway_ip is None


# ============================================================================
# 2. Linux Parsing Unit Tests
# ============================================================================

@pytest.mark.unit
def test_linux_parse_standard_default_route():
    """Verify standard Linux 'ip route show default' extracts gateway, device, and metric."""
    discovery = LinuxGatewayDiscovery()
    candidates = discovery.parse_ip_route(LINUX_IP_ROUTE_STANDARD)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.gateway_ip == "192.168.1.1"
    assert cand.interface_name == "eth0"
    assert cand.metric == 100
    assert cand.ip_family == "IPv4"


@pytest.mark.unit
def test_linux_multiple_routes_selection_lowest_metric(monkeypatch):
    """Verify Linux route selection selects the lowest metric candidate."""
    discovery = LinuxGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, LINUX_IP_ROUTE_MULTIPLE, ""),
    )

    result = discovery.discover()
    assert result.status == Status.SUCCESS
    assert result.gateway_ip == "10.0.0.1"
    assert result.metric == 50
    assert result.interface_name == "enp3s0"


@pytest.mark.unit
def test_linux_tied_metric_returns_incomplete(monkeypatch):
    """Verify multiple distinct Linux gateways tied at lowest metric returns INCOMPLETE."""
    discovery = LinuxGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, LINUX_IP_ROUTE_TIED, ""),
    )

    result = discovery.discover()
    assert result.status == Status.INCOMPLETE
    assert "Ambiguous routing table" in (result.error_message or "")


@pytest.mark.unit
def test_linux_ipv6_route_handling(monkeypatch):
    """Verify Linux IPv6 default route is parsed when IPv4 is absent."""
    discovery = LinuxGatewayDiscovery()

    def mock_cmd(cmd):
        if "-6" in cmd:
            return (0, LINUX_IP_ROUTE_IPV6, "")
        return (0, "", "")

    monkeypatch.setattr(discovery, "execute_command", mock_cmd)

    result = discovery.discover()
    assert result.status == Status.SUCCESS
    assert result.gateway_ip == "2001:db8::1"
    assert result.ip_family == "IPv6"
    assert result.metric == 1024


@pytest.mark.unit
def test_linux_route_without_gateway_returns_incomplete(monkeypatch):
    """Verify point-to-point route without gateway IP (default dev ppp0) returns INCOMPLETE."""
    discovery = LinuxGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, LINUX_IP_ROUTE_NO_GATEWAY, ""),
    )

    result = discovery.discover()
    assert result.status == Status.INCOMPLETE
    assert result.gateway_ip is None
    assert "specifies no next-hop gateway" in (result.error_message or "")


@pytest.mark.unit
def test_linux_fallback_to_proc_net_route(tmp_path: Path, monkeypatch):
    """Verify fallback to /proc/net/route when 'ip' executable is missing."""
    fake_proc = tmp_path / "route"
    fake_proc.write_text(PROC_NET_ROUTE_SYNTHETIC, encoding="utf-8")

    discovery = LinuxGatewayDiscovery(proc_route_path=fake_proc)

    def mock_execute(cmd):
        raise FileNotFoundError("ip executable not found")

    monkeypatch.setattr(discovery, "execute_command", mock_execute)

    result = discovery.discover()
    assert result.status == Status.SUCCESS
    # 0101A8C0 in little-endian hex is 192.168.1.1
    assert result.gateway_ip == "192.168.1.1"
    assert result.interface_name == "eth0"
    assert result.metric == 100


@pytest.mark.unit
def test_linux_missing_ip_and_missing_proc_route_fails(tmp_path: Path, monkeypatch):
    """Verify discovery fails cleanly when both 'ip' command and /proc/net/route are absent."""
    non_existent = tmp_path / "does_not_exist"
    discovery = LinuxGatewayDiscovery(proc_route_path=non_existent)

    def mock_execute(cmd):
        raise FileNotFoundError("ip not found")

    monkeypatch.setattr(discovery, "execute_command", mock_execute)

    result = discovery.discover()
    assert result.status == Status.FAILED
    assert "unavailable" in (result.error_message or "")


# ============================================================================
# 3. Subprocess Safety, Failures, and Error Handling
# ============================================================================

@pytest.mark.unit
def test_subprocess_timeout_returns_timeout_status(monkeypatch):
    """Verify subprocess timeout produces TIMEOUT status without hanging."""
    discovery = WindowsGatewayDiscovery(timeout_s=1.0)

    def mock_timeout(cmd):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)

    monkeypatch.setattr(discovery, "execute_command", mock_timeout)

    result = discovery.discover()
    assert result.status == Status.TIMEOUT
    assert "timed out" in (result.error_message or "")


@pytest.mark.unit
def test_subprocess_nonzero_exit_returns_failed(monkeypatch):
    """Verify nonzero command exit code is reported as FAILED with error context."""
    discovery = WindowsGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (1, "", "Access is denied"),
    )

    result = discovery.discover()
    assert result.status == Status.FAILED
    assert "failed with exit code 1" in (result.error_message or "")


@pytest.mark.unit
def test_subprocess_permission_error_returns_failed(monkeypatch):
    """Verify permission denial returns FAILED without crashing."""
    discovery = WindowsGatewayDiscovery()

    def mock_perm(cmd):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(discovery, "execute_command", mock_perm)

    result = discovery.discover()
    assert result.status == Status.FAILED
    assert "Permission denied" in (result.error_message or "")


@pytest.mark.unit
def test_unsupported_platform_returns_unsupported_status(monkeypatch):
    """Verify that execution on unsupported operating systems returns UNSUPPORTED."""
    adapter = GatewayDiscoveryAdapter()
    monkeypatch.setattr(sys, "platform", "darwin")

    result = adapter.execute(auto_discover=True)
    assert result.status == Status.UNSUPPORTED
    assert "unsupported on platform 'darwin'" in (result.error_message or "")


@pytest.mark.unit
def test_strictly_no_shell_true(monkeypatch):
    """Verify that MeasurementAdapter.run_command executes with shell=False unconditionally."""
    real_run = subprocess.run
    captured_kwargs = {}

    def spy_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        # Return dummy completed process
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"dummy", stderr=b"")

    monkeypatch.setattr(subprocess, "run", spy_run)

    class TestAdapter(MeasurementAdapter):
        @property
        def adapter_name(self) -> str:
            return "Test"
        @property
        def supported_platforms(self):
            return ("win32", "linux")
        def execute(self):
            return self.run_command(["route", "print"])

    adapter = TestAdapter()
    adapter.execute()

    assert captured_kwargs.get("shell") is False, "Command MUST be executed with shell=False"


@pytest.mark.unit
def test_zero_network_probes_dispatched_during_discovery(monkeypatch):
    """Verify gateway discovery does not open sockets or perform network probes."""
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Network traffic dispatched during gateway discovery!")

    monkeypatch.setattr(socket, "socket", forbidden_network)
    monkeypatch.setattr(socket, "gethostbyname", forbidden_network)

    # Execute discovery with mock Windows route print
    discovery = WindowsGatewayDiscovery()
    monkeypatch.setattr(
        discovery,
        "execute_command",
        lambda cmd: (0, WINDOWS_ROUTE_PRINT_STANDARD, "") if cmd[0] == "route" else (0, "", ""),
    )

    result = discovery.discover()
    assert result.status == Status.SUCCESS
    assert result.gateway_ip == "192.168.0.1"


# ============================================================================
# 4. Configuration Integration and Manual Override
# ============================================================================

@pytest.mark.unit
def test_valid_manual_override_takes_precedence():
    """Verify that a valid manual_override_ip bypasses OS discovery and succeeds."""
    adapter = GatewayDiscoveryAdapter()
    result = adapter.execute(manual_override_ip="192.168.1.254")

    assert result.status == Status.SUCCESS
    assert result.gateway_ip == "192.168.1.254"
    assert result.ip_family == "IPv4"
    assert result.discovery_source == "MANUAL_OVERRIDE"
    assert result.interface_name is None  # Not fabricated


@pytest.mark.unit
def test_valid_ipv6_manual_override():
    """Verify manual override works cleanly for valid IPv6 addresses."""
    adapter = GatewayDiscoveryAdapter()
    result = adapter.execute(manual_override_ip="2001:db8::1")

    assert result.status == Status.SUCCESS
    assert result.gateway_ip == "2001:db8::1"
    assert result.ip_family == "IPv6"
    assert result.discovery_source == "MANUAL_OVERRIDE"


@pytest.mark.unit
def test_invalid_manual_override_fails_safely():
    """Verify that a malformed manual override IP returns FAILED."""
    adapter = GatewayDiscoveryAdapter()
    result = adapter.execute(manual_override_ip="not_an_ip_address")

    assert result.status == Status.FAILED
    assert "invalid IP address" in (result.error_message or "")
    assert result.gateway_ip is None


@pytest.mark.unit
def test_auto_discover_disabled_without_override_returns_incomplete():
    """Verify that disabling auto-discovery without an override returns INCOMPLETE."""
    adapter = GatewayDiscoveryAdapter()
    result = adapter.execute(auto_discover=False)

    assert result.status == Status.INCOMPLETE
    assert "disabled in configuration" in (result.error_message or "")


@pytest.mark.unit
def test_gateway_discovery_result_to_target():
    """Verify GatewayDiscoveryResult cleanly converts to domain Target entity."""
    res = GatewayDiscoveryResult(
        status=Status.SUCCESS,
        gateway_ip="192.168.0.1",
        interface_name="Wi-Fi",
    )
    target = res.to_target()
    assert target is not None
    assert target.target_type == TargetType.GATEWAY
    assert target.ip_address == "192.168.0.1"
    assert target.provider_label == "Wi-Fi"

    # Failed result converts to None
    failed_res = GatewayDiscoveryResult(status=Status.FAILED)
    assert failed_res.to_target() is None


# ============================================================================
# 5. Optional Live Smoke Test (opt-in via @pytest.mark.live)
# ============================================================================

@pytest.mark.live
def test_live_gateway_discovery_smoke():
    """Opt-in live host inspection: verify GatewayDiscoveryAdapter runs on active machine.
    
    Guarantees:
    - Never pings the gateway.
    - Never probes external targets.
    - Does not require administrator privileges.
    - Excluded from ordinary unit test suite (run with: pytest -m live).
    """
    adapter = GatewayDiscoveryAdapter(timeout_s=3.0)
    result = adapter.execute(auto_discover=True)

    assert isinstance(result, GatewayDiscoveryResult)
    # The machine may or may not be connected to an active router;
    # verify the adapter reports status honestly without raising uncaught exceptions
    assert result.status in {Status.SUCCESS, Status.FAILED, Status.INCOMPLETE, Status.UNSUPPORTED}
    if result.status == Status.SUCCESS:
        assert result.gateway_ip is not None
        # Verify discovered IP is syntactically valid IPv4 or IPv6
        ip_obj = ipaddress.ip_address(result.gateway_ip)
        assert ip_obj.version in {4, 6}
