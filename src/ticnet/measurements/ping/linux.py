"""Linux-specific ping output parser.

Parses stdout from native Linux `ping` utility across IPv4 and IPv6, extracting
per-probe RTTs via icmp_seq, timeouts, unreachability errors, and summary metrics.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from ticnet.measurements.ping.models import PingProbeObservation
from ticnet.models.entities import now_utc_iso
from ticnet.models.enums import MeasurementStatus
from ticnet.utils.logging import get_logger

logger = get_logger("measurements.ping.linux")

# Matches: 64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=0.450 ms
LINUX_REPLY_PATTERN = re.compile(
    r"^\d+\s+bytes\s+from\s+\S+:\s+(?:icmp_seq|seq)=(?P<seq>\d+)\s+.*?\s+time=(?P<time>\d+(?:\.\d+)?)\s*ms",
    re.IGNORECASE | re.MULTILINE,
)

# Matches: From 192.168.1.1 icmp_seq=1 Destination Host Unreachable
LINUX_UNREACHABLE_PATTERN = re.compile(
    r"^From\s+\S+\s+(?:icmp_seq|seq)=(?P<seq>\d+)\s+(?P<error>Destination\s+Host\s+Unreachable|Destination\s+Port\s+Unreachable|Time\s+to\s+live\s+exceeded)",
    re.IGNORECASE | re.MULTILINE,
)

# Matches summary: 4 packets transmitted, 2 received, 50% packet loss, time 3004ms
LINUX_SUMMARY_PATTERN = re.compile(
    r"^(?P<tx>\d+)\s+packets\s+transmitted,\s+(?P<rx>\d+)\s+(?:packets\s+)?received",
    re.IGNORECASE | re.MULTILINE,
)


class LinuxPingParser:
    """Parses output from Linux ping utility."""

    @classmethod
    def parse_probes(
        cls,
        stdout: str,
        expected_count: int,
    ) -> List[PingProbeObservation]:
        """Parse individual probe lines from Linux ping stdout.
        
        Uses icmp_seq to correctly position successful replies and identify missing/timed-out probes.
        """
        probes_by_seq: Dict[int, PingProbeObservation] = {}

        for line in stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue

            # 1. Success reply line
            match_success = LINUX_REPLY_PATTERN.match(line_str)
            if match_success:
                seq = int(match_success.group("seq"))
                time_val = float(match_success.group("time"))
                probes_by_seq[seq] = PingProbeObservation(
                    sample_index=seq,
                    timestamp_utc=now_utc_iso(),
                    status=MeasurementStatus.SUCCESS,
                    rtt_ms=time_val,
                )
                continue

            # 2. Unreachable / ICMP error line
            match_unreach = LINUX_UNREACHABLE_PATTERN.match(line_str)
            if match_unreach:
                seq = int(match_unreach.group("seq"))
                err_msg = match_unreach.group("error")
                probes_by_seq[seq] = PingProbeObservation(
                    sample_index=seq,
                    timestamp_utc=now_utc_iso(),
                    status=MeasurementStatus.FAILED,
                    rtt_ms=None,
                    error_message=err_msg,
                )
                continue

        # Reconstruct all expected probe sequences 1..expected_count
        # Unobserved sequences correspond to lost / timed-out packets
        final_probes: List[PingProbeObservation] = []
        for seq in range(1, expected_count + 1):
            if seq in probes_by_seq:
                final_probes.append(probes_by_seq[seq])
            else:
                final_probes.append(
                    PingProbeObservation(
                        sample_index=seq,
                        timestamp_utc=now_utc_iso(),
                        status=MeasurementStatus.TIMEOUT,
                        rtt_ms=None,
                        error_message="Request timed out",
                    )
                )

        return final_probes
