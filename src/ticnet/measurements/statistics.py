"""Empirical network measurement statistical calculations for TicNet.

Implements mathematical definitions conforming strictly to 04_MEASUREMENT_METHODOLOGY.md:
- Packet Loss Percentage (Section 4)
- Delay Summaries: Minimum, Mean/Average, Maximum (Section 5)
- Median (p50): Requires minimum n >= 3 valid samples (Section 5)
- 95th Percentile (p95): Requires minimum n >= 10 valid samples (Section 5)
- Round-Trip Delay Variation / MACD Jitter: Requires minimum k >= 2 valid sample pairs (Section 6)

Core Invariants:
- Missingness is NOT zero: Failed/timed-out probes must NEVER be coerced to 0.0 ms.
- Sample sizes below statistical validity thresholds yield None rather than misleading claims.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Optional, Sequence, Union


def calculate_packet_loss_pct(
    attempted_probes: int,
    successful_responses: int,
) -> Optional[float]:
    """Calculate probe packet loss percentage over a bounded observation window.
    
    Formula:
        loss_pct = 100.0 * (attempted - successful) / attempted
        
    Args:
        attempted_probes: Total count of dispatched probe attempts.
        successful_responses: Count of valid received responses.
        
    Returns:
        Loss percentage as a float (0.0 to 100.0), or None if attempted_probes <= 0.
        
    Raises:
        ValueError: If sample counts are negative or successful > attempted.
    """
    if attempted_probes < 0 or successful_responses < 0:
        raise ValueError("Probe counts cannot be negative")
    if successful_responses > attempted_probes:
        raise ValueError("successful_responses cannot exceed attempted_probes")
    if attempted_probes == 0:
        return None

    failed = attempted_probes - successful_responses
    loss_pct = (failed / float(attempted_probes)) * 100.0
    return round(loss_pct, 2)


def calculate_median_rtt(
    rtts: Sequence[Optional[Union[float, int]]],
    min_samples: int = 3,
) -> Optional[float]:
    """Calculate median round-trip time across valid successful samples.
    
    Requires at least min_samples (default: 3, per Methodology Section 5).
    If valid sample count < min_samples, returns None to avoid statistical distortion.
    """
    valid: List[float] = [float(r) for r in rtts if r is not None and not isinstance(r, bool) and r >= 0.0]
    if len(valid) < min_samples:
        return None
    return round(float(statistics.median(valid)), 3)


def calculate_p95_rtt(
    rtts: Sequence[Optional[Union[float, int]]],
    min_samples: int = 10,
) -> Optional[float]:
    """Calculate 95th percentile RTT using the nearest-rank method.
    
    Requires at least min_samples (default: 10, per Methodology Section 5).
    If valid sample count < min_samples, returns None.
    """
    valid: List[float] = [float(r) for r in rtts if r is not None and not isinstance(r, bool) and r >= 0.0]
    if len(valid) < min_samples:
        return None

    sorted_rtts = sorted(valid)
    # Nearest-rank method: rank = ceil(P / 100 * N)
    rank = math.ceil(0.95 * len(sorted_rtts)) - 1
    rank = min(rank, len(sorted_rtts) - 1)
    return round(float(sorted_rtts[rank]), 3)


def calculate_macd_jitter(
    rtts: Sequence[Optional[Union[float, int]]],
) -> Optional[float]:
    """Calculate Round-Trip Delay Variation using Mean Absolute Consecutive Difference (MACD).
    
    Formula:
        Jitter_MACD = 1 / (k - 1) * sum(|RTT_{i+1} - RTT_i|)
        where k is the count of consecutive valid RTT samples.
        
    Disclosure:
        This metric represents round-trip delay variation as observed by the host.
        It must be labelled 'Round-Trip Delay Variation (MACD)' and NEVER described
        as 'RFC 3393 one-way jitter'.
        
    Returns:
        MACD jitter in milliseconds, or None if valid samples < 2.
    """
    valid: List[float] = [float(r) for r in rtts if r is not None and not isinstance(r, bool) and r >= 0.0]
    if len(valid) < 2:
        return None

    diffs = [abs(valid[i + 1] - valid[i]) for i in range(len(valid) - 1)]
    jitter = sum(diffs) / float(len(diffs))
    return round(jitter, 3)


def calculate_delay_summary_stats(
    rtts: Sequence[Optional[Union[float, int]]],
) -> Dict[str, Optional[float]]:
    """Compute complete delay distribution summaries for valid successful RTT samples.
    
    Returns:
        Dict with keys: min_rtt, avg_rtt, max_rtt, median_rtt, p95_rtt, jitter_macd.
    """
    valid: List[float] = [float(r) for r in rtts if r is not None and not isinstance(r, bool) and r >= 0.0]
    if not valid:
        return {
            "min_rtt": None,
            "avg_rtt": None,
            "max_rtt": None,
            "median_rtt": None,
            "p95_rtt": None,
            "jitter_macd": None,
        }

    return {
        "min_rtt": round(float(min(valid)), 3),
        "avg_rtt": round(float(sum(valid) / len(valid)), 3),
        "max_rtt": round(float(max(valid)), 3),
        "median_rtt": calculate_median_rtt(valid, min_samples=3),
        "p95_rtt": calculate_p95_rtt(valid, min_samples=10),
        "jitter_macd": calculate_macd_jitter(valid),
    }
