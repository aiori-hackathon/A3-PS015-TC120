"""Unit tests for Phase 4: Ping Measurement Adapter and Parsers (TC-ADPT-02).

Tests Windows and Linux ICMP Echo parsing, subprocess security (strictly shell=False),
bounded probe limits, error handling, non-zero null invariants, persistence mapping,
and gateway discovery integration using synthetic output fixtures.
"""

import subprocess
import sys
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from ticnet.configuration.validator import GatewayConfig, TargetsConfig
from ticnet.measurements.base import GatewayDiscoveryResult
from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter
from ticnet.measurements.ping.adapter import (
    DEFAULT_PROBE_COUNT,
    DEFAULT_PROBE_TIMEOUT_MS,
    MAX_PROBE_COUNT,
    MAX_PROBE_TIMEOUT_MS,
    MIN_PROBE_COUNT,
    MIN_PROBE_TIMEOUT_MS,
    PingMeasurementAdapter,
    probe_gateway_reachability,
)
from ticnet.measurements.ping.linux import LinuxPingParser
from ticnet.measurements.ping.models import (
    PingMeasurementResult,
    PingProbeObservation,
)
from ticnet.measurements.ping.windows import WindowsPingParser
from ticnet.models.entities import Target, TestRun, now_utc_iso
from ticnet.models.enums import MeasurementStatus, RunStatus, Status, TargetType
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository


# ============================================================================
# Synthetic Mock Outputs: Windows ping.exe
# ============================================================================

WINDOWS_PING_ALL_SUCCESS = """
Pinging 192.168.0.1 with 32 bytes of data:
Reply from 192.168.0.1: bytes=32 time=2ms TTL=64
Reply from 192.168.0.1: bytes=32 time=4ms TTL=64
Reply from 192.168.0.1: bytes=32 time=3ms TTL=64
Reply from 192.168.0.1: bytes=32 time=5ms TTL=64

Ping statistics for 192.168.0.1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 2ms, Maximum = 5ms, Average = 3ms
"""

WINDOWS_PING_SUBMILLI_SUCCESS = """
Pinging 127.0.0.1 with 32 bytes of data:
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time=1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128

Ping statistics for 127.0.0.1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
"""

WINDOWS_PING_IPV6_SUCCESS = """
Pinging ::1 with 32 bytes of data:
Reply from ::1: time<1ms
Reply from ::1: time=1ms
Reply from ::1: time=2ms
Reply from ::1: time<1ms

Ping statistics for ::1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
"""

WINDOWS_PING_PARTIAL_LOSS = """
Pinging 192.168.0.1 with 32 bytes of data:
Reply from 192.168.0.1: bytes=32 time=10ms TTL=64
Request timed out.
Reply from 192.168.0.1: bytes=32 time=14ms TTL=64
Request timed out.

Ping statistics for 192.168.0.1:
    Packets: Sent = 4, Received = 2, Lost = 2 (50% loss),
"""

WINDOWS_PING_ALL_TIMEOUT = """
Pinging 192.168.1.99 with 32 bytes of data:
Request timed out.
Request timed out.
Request timed out.
Request timed out.

Ping statistics for 192.168.1.99:
    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),
"""

WINDOWS_PING_UNREACHABLE = """
Pinging 192.168.200.1 with 32 bytes of data:
Reply from 192.168.0.102: Destination host unreachable.
Reply from 192.168.0.102: Destination host unreachable.
Reply from 192.168.0.102: Destination host unreachable.
Reply from 192.168.0.102: Destination host unreachable.

Ping statistics for 192.168.200.1:
    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),
"""

WINDOWS_PING_GENERAL_FAILURE = """
Pinging 192.168.0.1 with 32 bytes of data:
General failure.
General failure.
General failure.
General failure.

Ping statistics for 192.168.0.1:
    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),
"""

WINDOWS_PING_TRUNCATED = """
Pinging 192.168.0.1 with 32 bytes of data:
Reply from 192.168.0.1: bytes=32 time=5ms TTL=64
"""


# ============================================================================
# Synthetic Mock Outputs: Linux ping
# ============================================================================

LINUX_PING_ALL_SUCCESS = """
PING 192.168.0.1 (192.168.0.1) 56(84) bytes of data.
64 bytes from 192.168.0.1: icmp_seq=1 ttl=64 time=1.45 ms
64 bytes from 192.168.0.1: icmp_seq=2 ttl=64 time=2.10 ms
64 bytes from 192.168.0.1: icmp_seq=3 ttl=64 time=1.85 ms
64 bytes from 192.168.0.1: icmp_seq=4 ttl=64 time=2.50 ms

--- 192.168.0.1 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3004ms
rtt min/avg/max/mdev = 1.450/1.975/2.500/0.380 ms
"""

LINUX_PING_PARTIAL_LOSS = """
PING 192.168.0.1 (192.168.0.1) 56(84) bytes of data.
64 bytes from 192.168.0.1: icmp_seq=1 ttl=64 time=5.20 ms
64 bytes from 192.168.0.1: icmp_seq=3 ttl=64 time=6.10 ms

--- 192.168.0.1 ping statistics ---
4 packets transmitted, 2 received, 50% packet loss, time 3010ms
"""

LINUX_PING_ALL_LOST = """
PING 192.168.1.99 (192.168.1.99) 56(84) bytes of data.

--- 192.168.1.99 ping statistics ---
4 packets transmitted, 0 received, 100% packet loss, time 3055ms
"""

LINUX_PING_UNREACHABLE = """
PING 192.168.200.1 (192.168.200.1) 56(84) bytes of data.
From 192.168.0.102 icmp_seq=1 Destination Host Unreachable
From 192.168.0.102 icmp_seq=2 Destination Host Unreachable
From 192.168.0.102 icmp_seq=3 Destination Host Unreachable
From 192.168.0.102 icmp_seq=4 Destination Host Unreachable

--- 192.168.200.1 ping statistics ---
4 packets transmitted, 0 received, +4 errors, 100% packet loss, time 3006ms
"""


# ============================================================================
# Unit Tests: Windows Output Parser
# ============================================================================

@pytest.mark.unit
class TestWindowsPingParser:
    """Verifies deterministic parsing of native Windows ping stdout."""

    def test_parse_all_success_probes(self):
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_ALL_SUCCESS, expected_count=4)
        assert len(probes) == 4
        for p in probes:
            assert p.status == MeasurementStatus.SUCCESS
            assert p.rtt_ms is not None
        assert [p.rtt_ms for p in probes] == [2.0, 4.0, 3.0, 5.0]

    def test_parse_submillisecond_probes(self):
        """time<1ms is mapped to 0.5ms representation."""
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_SUBMILLI_SUCCESS, expected_count=4)
        assert len(probes) == 4
        assert probes[0].rtt_ms == 0.5
        assert probes[1].rtt_ms == 0.5
        assert probes[2].rtt_ms == 1.0
        assert probes[3].rtt_ms == 0.5

    def test_parse_ipv6_success_probes(self):
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_IPV6_SUCCESS, expected_count=4)
        assert len(probes) == 4
        assert [p.rtt_ms for p in probes] == [0.5, 1.0, 2.0, 0.5]

    def test_parse_partial_packet_loss(self):
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_PARTIAL_LOSS, expected_count=4)
        assert len(probes) == 4
        assert probes[0].status == MeasurementStatus.SUCCESS
        assert probes[0].rtt_ms == 10.0
        assert probes[1].status == MeasurementStatus.TIMEOUT
        assert probes[1].rtt_ms is None
        assert probes[2].status == MeasurementStatus.SUCCESS
        assert probes[2].rtt_ms == 14.0
        assert probes[3].status == MeasurementStatus.TIMEOUT
        assert probes[3].rtt_ms is None

    def test_parse_all_timeout_probes(self):
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_ALL_TIMEOUT, expected_count=4)
        assert len(probes) == 4
        for p in probes:
            assert p.status == MeasurementStatus.TIMEOUT
            assert p.rtt_ms is None

    def test_parse_unreachable_probes(self):
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_UNREACHABLE, expected_count=4)
        assert len(probes) == 4
        for p in probes:
            assert p.status == MeasurementStatus.FAILED
            assert p.rtt_ms is None
            assert p.error_message is not None and "unreachable" in p.error_message.lower()

    def test_parse_general_failure_probes(self):
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_GENERAL_FAILURE, expected_count=4)
        assert len(probes) == 4
        for p in probes:
            assert p.status == MeasurementStatus.FAILED
            assert p.rtt_ms is None
            assert p.error_message is not None and "General failure" in p.error_message

    def test_parse_truncated_pads_remaining_as_timeout(self):
        """Truncated output pads missing probes as TIMEOUT."""
        probes = WindowsPingParser.parse_probes(WINDOWS_PING_TRUNCATED, expected_count=4)
        assert len(probes) == 4
        assert probes[0].status == MeasurementStatus.SUCCESS
        assert probes[1].status == MeasurementStatus.TIMEOUT
        assert probes[2].status == MeasurementStatus.TIMEOUT
        assert probes[3].status == MeasurementStatus.TIMEOUT


# ============================================================================
# Unit Tests: Linux Output Parser
# ============================================================================

@pytest.mark.unit
class TestLinuxPingParser:
    """Verifies deterministic parsing of native Linux ping stdout."""

    def test_parse_all_success_probes(self):
        probes = LinuxPingParser.parse_probes(LINUX_PING_ALL_SUCCESS, expected_count=4)
        assert len(probes) == 4
        assert [p.rtt_ms for p in probes] == [1.45, 2.10, 1.85, 2.50]
        for p in probes:
            assert p.status == MeasurementStatus.SUCCESS

    def test_parse_partial_packet_loss_with_seq_gap(self):
        """Missing sequence numbers (2 and 4) are mapped to TIMEOUT."""
        probes = LinuxPingParser.parse_probes(LINUX_PING_PARTIAL_LOSS, expected_count=4)
        assert len(probes) == 4
        assert probes[0].sample_index == 1
        assert probes[0].status == MeasurementStatus.SUCCESS
        assert probes[0].rtt_ms == 5.20

        assert probes[1].sample_index == 2
        assert probes[1].status == MeasurementStatus.TIMEOUT
        assert probes[1].rtt_ms is None

        assert probes[2].sample_index == 3
        assert probes[2].status == MeasurementStatus.SUCCESS
        assert probes[2].rtt_ms == 6.10

        assert probes[3].sample_index == 4
        assert probes[3].status == MeasurementStatus.TIMEOUT
        assert probes[3].rtt_ms is None

    def test_parse_all_lost_probes(self):
        probes = LinuxPingParser.parse_probes(LINUX_PING_ALL_LOST, expected_count=4)
        assert len(probes) == 4
        for p in probes:
            assert p.status == MeasurementStatus.TIMEOUT
            assert p.rtt_ms is None

    def test_parse_destination_unreachable_probes(self):
        probes = LinuxPingParser.parse_probes(LINUX_PING_UNREACHABLE, expected_count=4)
        assert len(probes) == 4
        for p in probes:
            assert p.status == MeasurementStatus.FAILED
            assert p.rtt_ms is None
            assert p.error_message is not None and "Unreachable" in p.error_message


# ============================================================================
# Unit Tests: PingMeasurementAdapter Execution & Safety
# ============================================================================

@pytest.mark.unit
class TestPingMeasurementAdapter:
    """Verifies adapter execution safety, boundary validation, and subprocess mocking."""

    def test_adapter_properties(self):
        adapter = PingMeasurementAdapter()
        assert adapter.adapter_name == "PingMeasurementAdapter"
        assert "win32" in adapter.supported_platforms
        assert "linux" in adapter.supported_platforms

    def test_reject_invalid_target_ip(self):
        adapter = PingMeasurementAdapter()
        # Non-IP strings must be rejected without invoking subprocess
        with patch.object(adapter, "run_command") as mock_cmd:
            res1 = adapter.execute(target_ip="")
            assert res1.status == Status.FAILED
            assert res1.packet_loss_pct is None

            res2 = adapter.execute(target_ip="not_an_ip")
            assert res2.status == Status.FAILED
            assert res2.packet_loss_pct is None

            res3 = adapter.execute(target_ip="999.999.999.999")
            assert res3.status == Status.FAILED
            assert res3.packet_loss_pct is None

            mock_cmd.assert_not_called()

    def test_reject_invalid_probe_count(self):
        adapter = PingMeasurementAdapter()
        with patch.object(adapter, "run_command") as mock_cmd:
            res_zero = adapter.execute(target_ip="192.168.0.1", count=0)
            assert res_zero.status == Status.FAILED
            assert res_zero.error_message is not None and "safety bounds" in res_zero.error_message

            res_neg = adapter.execute(target_ip="192.168.0.1", count=-5)
            assert res_neg.status == Status.FAILED

            res_exceed = adapter.execute(target_ip="192.168.0.1", count=51)
            assert res_exceed.status == Status.FAILED

            res_bool = adapter.execute(target_ip="192.168.0.1", count=True)  # type: ignore
            assert res_bool.status == Status.FAILED

            mock_cmd.assert_not_called()

    def test_reject_invalid_timeout(self):
        adapter = PingMeasurementAdapter()
        with patch.object(adapter, "run_command") as mock_cmd:
            res_low = adapter.execute(target_ip="192.168.0.1", per_probe_timeout_ms=50)
            assert res_low.status == Status.FAILED

            res_high = adapter.execute(target_ip="192.168.0.1", per_probe_timeout_ms=6000)
            assert res_high.status == Status.FAILED

            mock_cmd.assert_not_called()

    def test_unsupported_platform(self):
        adapter = PingMeasurementAdapter(platform_name="darwin")
        res = adapter.execute(target_ip="192.168.0.1")
        assert res.status == Status.UNSUPPORTED
        assert res.error_message is not None and "unsupported on platform 'darwin'" in res.error_message

    def test_subprocess_security_shell_false(self):
        """Verifies that the executed command strictly enforces shell=False and tokenized args."""
        adapter = PingMeasurementAdapter(platform_name="win32")
        with patch("subprocess.run") as mock_subproc:
            mock_subproc.return_value = subprocess.CompletedProcess(
                args=["ping", "-n", "4", "-w", "1000", "192.168.0.1"],
                returncode=0,
                stdout=WINDOWS_PING_ALL_SUCCESS.encode("utf-8"),
                stderr=b"",
            )
            res = adapter.execute(target_ip="192.168.0.1", count=4, per_probe_timeout_ms=1000)
            assert res.status == Status.SUCCESS

            mock_subproc.assert_called_once()
            call_kwargs = mock_subproc.call_args.kwargs
            assert call_kwargs["shell"] is False
            cmd_args = mock_subproc.call_args.args[0]
            assert isinstance(cmd_args, list)
            assert cmd_args[0] == "ping"
            assert cmd_args[-1] == "192.168.0.1"

    def test_windows_mock_execution_all_success(self):
        adapter = PingMeasurementAdapter(platform_name="win32")
        with patch.object(adapter, "run_command", return_value=(0, WINDOWS_PING_ALL_SUCCESS, "")):
            res = adapter.execute(target_ip="192.168.0.1", count=4)
            assert res.status == Status.SUCCESS
            assert res.target_ip == "192.168.0.1"
            assert res.ip_family == "IPv4"
            assert res.probe_count_configured == 4
            assert res.probe_count_attempted == 4
            assert res.probe_count_successful == 4
            assert res.probe_count_failed == 0
            assert res.packet_loss_pct == 0.0
            assert res.individual_rtts == [2.0, 4.0, 3.0, 5.0]
            assert res.min_rtt_ms == 2.0
            assert res.avg_rtt_ms == 3.5
            assert res.max_rtt_ms == 5.0
            assert res.median_rtt_ms == 3.5
            assert res.p95_rtt_ms is None  # n=4 < 10
            assert res.jitter_macd_ms is not None

    def test_windows_mock_execution_partial_loss(self):
        adapter = PingMeasurementAdapter(platform_name="win32")
        with patch.object(adapter, "run_command", return_value=(0, WINDOWS_PING_PARTIAL_LOSS, "")):
            res = adapter.execute(target_ip="192.168.0.1", count=4)
            assert res.status == Status.SUCCESS
            assert res.probe_count_attempted == 4
            assert res.probe_count_successful == 2
            assert res.probe_count_failed == 2
            assert res.packet_loss_pct == 50.0
            assert res.individual_rtts == [10.0, 14.0]
            assert res.min_rtt_ms == 10.0
            assert res.avg_rtt_ms == 12.0
            assert res.max_rtt_ms == 14.0
            assert res.median_rtt_ms is None  # n=2 < 3
            assert res.jitter_macd_ms == 4.0  # |14-10| = 4.0

    def test_windows_mock_execution_all_unanswered_100_percent_loss(self):
        adapter = PingMeasurementAdapter(platform_name="win32")
        with patch.object(adapter, "run_command", return_value=(1, WINDOWS_PING_ALL_TIMEOUT, "")):
            res = adapter.execute(target_ip="192.168.1.99", count=4)
            assert res.status == Status.SUCCESS
            assert res.probe_count_attempted == 4
            assert res.probe_count_successful == 0
            assert res.probe_count_failed == 4
            assert res.packet_loss_pct == 100.0
            assert res.individual_rtts == []
            assert res.min_rtt_ms is None
            assert res.avg_rtt_ms is None
            assert res.max_rtt_ms is None
            assert res.median_rtt_ms is None
            assert res.p95_rtt_ms is None
            assert res.jitter_macd_ms is None

    def test_linux_mock_execution_all_success(self):
        adapter = PingMeasurementAdapter(platform_name="linux")
        with patch.object(adapter, "run_command", return_value=(0, LINUX_PING_ALL_SUCCESS, "")):
            res = adapter.execute(target_ip="192.168.0.1", count=4)
            assert res.status == Status.SUCCESS
            assert res.probe_count_successful == 4
            assert res.packet_loss_pct == 0.0
            assert res.min_rtt_ms == 1.45
            assert res.max_rtt_ms == 2.50
            assert res.median_rtt_ms is not None

    def test_subprocess_timeout_handling(self):
        adapter = PingMeasurementAdapter(platform_name="win32")
        with patch.object(adapter, "run_command", side_effect=subprocess.TimeoutExpired(cmd="ping", timeout=7.0)):
            res = adapter.execute(target_ip="192.168.0.1", count=4)
            assert res.status == Status.TIMEOUT
            assert res.packet_loss_pct is None  # Timed-out command is NOT 100% loss
            assert res.error_message is not None and "timed out" in res.error_message.lower()

    def test_executable_not_found_handling(self):
        adapter = PingMeasurementAdapter(platform_name="win32")
        with patch.object(adapter, "run_command", side_effect=FileNotFoundError("ping executable not found")):
            res = adapter.execute(target_ip="192.168.0.1", count=4)
            assert res.status == Status.FAILED
            assert res.packet_loss_pct is None
            assert res.error_message is not None and "not found" in res.error_message.lower()

    def test_permission_error_handling(self):
        adapter = PingMeasurementAdapter(platform_name="win32")
        with patch.object(adapter, "run_command", side_effect=PermissionError("Permission denied")):
            res = adapter.execute(target_ip="192.168.0.1", count=4)
            assert res.status == Status.FAILED
            assert res.packet_loss_pct is None
            assert res.error_message is not None and "permission denied" in res.error_message.lower()


# ============================================================================
# Unit Tests: Gateway Reachability Orchestration & Discovery Integration
# ============================================================================

@pytest.mark.unit
class TestGatewayReachabilityOrchestrator:
    """Verifies coupling between gateway discovery, manual overrides, and reachability probing."""

    def test_probe_gateway_successful_discovery(self):
        mock_disc = MagicMock(spec=GatewayDiscoveryAdapter)
        mock_disc.execute.return_value = GatewayDiscoveryResult(
            status=Status.SUCCESS,
            gateway_ip="192.168.0.1",
            ip_family="IPv4",
            interface_name="Wi-Fi",
        )

        mock_ping = MagicMock(spec=PingMeasurementAdapter)
        mock_ping.execute.return_value = PingMeasurementResult(
            target_ip="192.168.0.1",
            ip_family="IPv4",
            probe_count_attempted=4,
            probe_count_successful=4,
            packet_loss_pct=0.0,
            status=Status.SUCCESS,
        )

        res = probe_gateway_reachability(discovery_adapter=mock_disc, ping_adapter=mock_ping)
        assert res.status == Status.SUCCESS
        assert res.target_ip == "192.168.0.1"
        mock_ping.execute.assert_called_once_with(
            target_ip="192.168.0.1",
            count=DEFAULT_PROBE_COUNT,
            per_probe_timeout_ms=DEFAULT_PROBE_TIMEOUT_MS,
            target_type=TargetType.GATEWAY,
        )

    def test_probe_gateway_discovery_failed_stops_safely(self):
        """When gateway discovery fails, reachability probing MUST stop without probing an arbitrary target."""
        mock_disc = MagicMock(spec=GatewayDiscoveryAdapter)
        mock_disc.execute.return_value = GatewayDiscoveryResult(
            status=Status.FAILED,
            error_message="Routing table empty",
        )

        mock_ping = MagicMock(spec=PingMeasurementAdapter)

        res = probe_gateway_reachability(discovery_adapter=mock_disc, ping_adapter=mock_ping)
        assert res.status == Status.FAILED
        assert res.error_message is not None and "Gateway discovery did not succeed" in res.error_message
        assert res.packet_loss_pct is None
        mock_ping.execute.assert_not_called()

    def test_probe_gateway_discovery_incomplete_stops_safely(self):
        mock_disc = MagicMock(spec=GatewayDiscoveryAdapter)
        mock_disc.execute.return_value = GatewayDiscoveryResult(
            status=Status.INCOMPLETE,
            error_message="Auto-discovery disabled",
        )

        mock_ping = MagicMock(spec=PingMeasurementAdapter)
        res = probe_gateway_reachability(discovery_adapter=mock_disc, ping_adapter=mock_ping)
        assert res.status == Status.INCOMPLETE
        mock_ping.execute.assert_not_called()

    def test_probe_gateway_explicit_manual_target(self):
        """Explicit target_ip bypasses gateway discovery entirely."""
        mock_disc = MagicMock(spec=GatewayDiscoveryAdapter)
        mock_ping = MagicMock(spec=PingMeasurementAdapter)
        mock_ping.execute.return_value = PingMeasurementResult(
            target_ip="10.0.0.1",
            ip_family="IPv4",
            status=Status.SUCCESS,
        )

        res = probe_gateway_reachability(
            target_ip="10.0.0.1",
            discovery_adapter=mock_disc,
            ping_adapter=mock_ping,
        )
        assert res.status == Status.SUCCESS
        mock_disc.execute.assert_not_called()
        mock_ping.execute.assert_called_once()


# ============================================================================
# Unit Tests: Persistence Mapping (Observation & Summary Repositories)
# ============================================================================

@pytest.mark.unit
class TestPersistenceIntegration:
    """Verifies conversion and database persistence of PingMeasurementResult."""

    def test_persist_observations_and_summaries(self, tmp_path):
        db_path = tmp_path / "test_phase4.db"
        db = DatabaseManager(str(db_path))
        db.run_migrations()

        run_repo = RunRepository(db)
        obs_repo = ObservationRepository(db)
        sum_repo = SummaryRepository(db)

        # 1. Setup prerequisite Run and Target
        run = TestRun(
            run_id="run-phase4-test",
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="1.0.0",
            config_version="1.0.0",
        )
        run_repo.create_run(run)
        target = Target(
            target_id="tgt-gateway-test",
            target_type=TargetType.GATEWAY,
            ip_address="192.168.0.1",
            provider_label="Test Gateway",
            is_active=True,
        )
        obs_repo.create_target(target)

        # 2. Build PingMeasurementResult
        probes = [
            PingProbeObservation(sample_index=1, timestamp_utc=now_utc_iso(), status=MeasurementStatus.SUCCESS, rtt_ms=2.5),
            PingProbeObservation(sample_index=2, timestamp_utc=now_utc_iso(), status=MeasurementStatus.SUCCESS, rtt_ms=3.5),
            PingProbeObservation(sample_index=3, timestamp_utc=now_utc_iso(), status=MeasurementStatus.SUCCESS, rtt_ms=4.5),
            PingProbeObservation(sample_index=4, timestamp_utc=now_utc_iso(), status=MeasurementStatus.TIMEOUT, rtt_ms=None),
        ]
        result = PingMeasurementResult(
            target_ip="192.168.0.1",
            ip_family="IPv4",
            probe_count_configured=4,
            probe_count_attempted=4,
            probe_count_successful=3,
            probe_count_failed=1,
            packet_loss_pct=25.0,
            individual_rtts=[2.5, 3.5, 4.5],
            min_rtt_ms=2.5,
            avg_rtt_ms=3.5,
            max_rtt_ms=4.5,
            median_rtt_ms=3.5,
            p95_rtt_ms=None,
            jitter_macd_ms=1.0,
            status=Status.SUCCESS,
            probes=probes,
        )

        # 3. Convert to domain entities
        domain_observations = result.to_observations(run_id=run.run_id, target_id=target.target_id)
        domain_summaries = result.to_summaries(run_id=run.run_id, target_id=target.target_id)

        assert len(domain_observations) == 4
        # Verify non-zero null invariant in observation
        assert domain_observations[3].status == MeasurementStatus.TIMEOUT
        assert domain_observations[3].value_numeric is None

        # 4. Save to SQLite via repositories
        saved_obs = obs_repo.create_observations_batch(domain_observations)
        assert len(saved_obs) == 4

        saved_sums = sum_repo.create_summaries_batch(domain_summaries)
        assert len(saved_sums) == 3  # packet_loss, median (n=3), jitter (k=3)

        # 5. Read back from DB to confirm integrity
        loaded_obs = obs_repo.get_observations_for_run(run.run_id)
        assert len(loaded_obs) == 4
        # Assert timed out probe persisted value_numeric as None
        timed_out_obs = [o for o in loaded_obs if o.sample_index == 4][0]
        assert timed_out_obs.value_numeric is None
        assert timed_out_obs.status == MeasurementStatus.TIMEOUT

        loaded_sums = sum_repo.get_summaries_for_run(run.run_id)
        assert len(loaded_sums) == 3


# ============================================================================
# Opt-In Live Smoke Test (disabled by default in offline runs)
# ============================================================================

@pytest.mark.live
def test_live_gateway_ping_smoke():
    """Opt-in live ping smoke test targeting 127.0.0.1 with 1 probe."""
    adapter = PingMeasurementAdapter()
    result = adapter.execute(target_ip="127.0.0.1", count=1, per_probe_timeout_ms=1000)
    assert result.status == Status.SUCCESS
    assert result.probe_count_attempted == 1
    assert result.packet_loss_pct == 0.0
