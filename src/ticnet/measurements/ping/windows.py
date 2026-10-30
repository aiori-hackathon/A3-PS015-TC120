"""Windows-specific ping output parser.

Parses stdout from native Windows `ping.exe` across IPv4 and IPv6, extracting
per-probe RTTs, timeouts, unreachability errors, and summary statistics.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ticnet.measurements.ping.models import PingProbeObservation
from ticnet.models.entities import now_utc_iso
from ticnet.models.enums import MeasurementStatus
from ticnet.utils.logging import get_logger

logger = get_logger("measurements.ping.windows")

# Regex patterns for Windows ping lines
# 1. Successful reply line:
# Reply from 192.168.0.1: bytes=32 time=2ms TTL=64
# Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
# Reply from ::1: time<1ms
REPLY_SUCCESS_PATTERN = re.compile(
    r"^Reply from (?P<from>\S+?): (?:bytes=\d+ )?time(?P<cmp>[<=])(?P<time>\d+(?:\.\d+)?)ms",
    re.IGNORECASE | re.MULTILINE,
)

# 2. Timeout line:
# Request timed out.
REPLY_TIMEOUT_PATTERN = re.compile(
    r"^Request timed out\.",
    re.IGNORECASE | re.MULTILINE,
)

# 3. Unreachable line:
# Reply from 192.168.0.102: Destination host unreachable.
# Destination host unreachable.
REPLY_UNREACHABLE_PATTERN = re.compile(
    r"^(?:Reply from \S+: )?Destination (?:host|network) unreachable\.",
    re.IGNORECASE | re.MULTILINE,
)

# 4. General failure line:
REPLY_FAILURE_PATTERN = re.compile(
    r"^(?:Transmit failed\.\s+)?General failure\.",
    re.IGNORECASE | re.MULTILINE,
)


class WindowsPingParser:
    """Parses output from Windows ping.exe."""

    @classmethod
    def parse_probes(
        cls,
        stdout: str,
        expected_count: int,
    ) -> List[PingProbeObservation]:
        """Parse individual probe lines from Windows ping stdout.
        
        Guarantees:
        - Accurately captures reply latency in milliseconds (handles time=12ms, time<1ms, time=1.5ms).
        - Correctly tags timeouts and unreachable responses with MeasurementStatus.
        - Enforces non-zero nulls: rtt_ms is None for timed out or failed probes.
        - Does not treat summary lines as probe replies.
        """
        probes: List[PingProbeObservation] = []
        sample_idx = 1

        for line in stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue

            # Check 1: Success reply
            success_match = REPLY_SUCCESS_PATTERN.match(line_str)
            if success_match:
                cmp_op = success_match.group("cmp")
                raw_time = float(success_match.group("time"))
                # If time<1ms, represent as 0.5ms (sub-millisecond approximation)
                rtt_val = 0.5 if cmp_op == "<" else raw_time
                probes.append(
                    PingProbeObservation(
                        sample_index=sample_idx,
                        timestamp_utc=now_utc_iso(),
                        status=MeasurementStatus.SUCCESS,
                        rtt_ms=rtt_val,
                    )
                )
                sample_idx += 1
                continue

            # Check 2: Timeout
            if REPLY_TIMEOUT_PATTERN.match(line_str):
                probes.append(
                    PingProbeObservation(
                        sample_index=sample_idx,
                        timestamp_utc=now_utc_iso(),
                        status=MeasurementStatus.TIMEOUT,
                        rtt_ms=None,
                        error_message="Request timed out",
                    )
                )
                sample_idx += 1
                continue

            # Check 3: Destination unreachable
            if REPLY_UNREACHABLE_PATTERN.match(line_str):
                probes.append(
                    PingProbeObservation(
                        sample_index=sample_idx,
                        timestamp_utc=now_utc_iso(),
                        status=MeasurementStatus.FAILED,
                        rtt_ms=None,
                        error_message="Destination host unreachable",
                    )
                )
                sample_idx += 1
                continue

            # Check 4: General failure
            if REPLY_FAILURE_PATTERN.match(line_str):
                probes.append(
                    PingProbeObservation(
                        sample_index=sample_idx,
                        timestamp_utc=now_utc_iso(),
                        status=MeasurementStatus.FAILED,
                        rtt_ms=None,
                        error_message="Transmit failed: General failure",
                    )
                )
                sample_idx += 1
                continue

        # If stdout was truncated or process exited prematurely, fill remaining expected probes as TIMEOUT
        while len(probes) < expected_count:
            probes.append(
                PingProbeObservation(
                    sample_index=sample_idx,
                    timestamp_utc=now_utc_iso(),
                    status=MeasurementStatus.TIMEOUT,
                    rtt_ms=None,
                    error_message="Probe not completed due to premature process termination",
                )
            )
            sample_idx += 1

        return probes
