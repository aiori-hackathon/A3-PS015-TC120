"""Unit tests for Phase 4: Network Measurement Statistical Calculations (TC-UNIT-01, TC-UNIT-02).

Tests mathematical formulas conforming to 04_MEASUREMENT_METHODOLOGY.md:
- Packet loss percentage (Section 4)
- Delay distribution summaries: min, mean, max
- Sample median (p50, n >= 3)
- 95th percentile (p95, n >= 10, nearest-rank)
- Round-Trip Delay Variation / MACD Jitter (k >= 2)
- Strict non-zero null invariants and boundary conditions
"""

import pytest

from ticnet.measurements.statistics import (
    calculate_delay_summary_stats,
    calculate_macd_jitter,
    calculate_median_rtt,
    calculate_p95_rtt,
    calculate_packet_loss_pct,
)


# ============================================================================
# TC-UNIT-01: Packet Loss & Median Latency Calculations
# ============================================================================

@pytest.mark.unit
class TestPacketLossCalculation:
    """Verifies probe loss percentage calculation and boundary invariants."""

    def test_zero_packet_loss(self):
        """All attempted probes receive valid responses (0% loss)."""
        loss = calculate_packet_loss_pct(attempted_probes=4, successful_responses=4)
        assert loss == 0.0

    def test_partial_packet_loss(self):
        """Half of probes respond (50% loss)."""
        loss = calculate_packet_loss_pct(attempted_probes=4, successful_responses=2)
        assert loss == 50.0

    def test_complete_packet_loss(self):
        """Completed probe sequence with zero responses (100% loss)."""
        loss = calculate_packet_loss_pct(attempted_probes=4, successful_responses=0)
        assert loss == 100.0

    def test_fractional_loss_precision(self):
        """Verify 2-decimal rounded precision for fractional packet loss."""
        # 1 out of 3 failed = 33.33%
        loss = calculate_packet_loss_pct(attempted_probes=3, successful_responses=2)
        assert loss == 33.33

        # 1 out of 7 failed = 14.29%
        loss = calculate_packet_loss_pct(attempted_probes=7, successful_responses=6)
        assert loss == 14.29

    def test_zero_attempted_probes_returns_none(self):
        """Zero attempted probes must return None rather than division by zero."""
        loss = calculate_packet_loss_pct(attempted_probes=0, successful_responses=0)
        assert loss is None

    def test_negative_probe_counts_raise_value_error(self):
        """Negative counts must be strictly rejected."""
        with pytest.raises(ValueError, match="Probe counts cannot be negative"):
            calculate_packet_loss_pct(attempted_probes=-1, successful_responses=0)

        with pytest.raises(ValueError, match="Probe counts cannot be negative"):
            calculate_packet_loss_pct(attempted_probes=4, successful_responses=-1)

    def test_successful_exceeding_attempted_raises_value_error(self):
        """Successful responses cannot exceed attempted probes."""
        with pytest.raises(ValueError, match="successful_responses cannot exceed attempted_probes"):
            calculate_packet_loss_pct(attempted_probes=4, successful_responses=5)


@pytest.mark.unit
class TestMedianRTTCalculation:
    """Verifies median round-trip time calculation under sample size constraints (n >= 3)."""

    def test_median_with_sufficient_odd_samples(self):
        """Sufficient odd sample count (n=3, n=5) returns true middle sample."""
        assert calculate_median_rtt([10.0, 20.0, 30.0], min_samples=3) == 20.0
        assert calculate_median_rtt([5.0, 1.0, 8.0, 3.0, 12.0], min_samples=3) == 5.0

    def test_median_with_sufficient_even_samples(self):
        """Sufficient even sample count (n=4) returns arithmetic mean of middle two."""
        assert calculate_median_rtt([10.0, 20.0, 30.0, 40.0], min_samples=3) == 25.0

    def test_median_with_insufficient_samples_returns_none(self):
        """Sample sizes n < 3 must return None to prevent misleading statistical claims."""
        assert calculate_median_rtt([], min_samples=3) is None
        assert calculate_median_rtt([15.0], min_samples=3) is None
        assert calculate_median_rtt([10.0, 20.0], min_samples=3) is None

    def test_median_filters_none_and_invalids(self):
        """None, negative numbers, or invalid entries are excluded before evaluating n >= 3."""
        # 2 valid samples + 2 None -> effective n=2 < 3 -> returns None
        assert calculate_median_rtt([10.0, None, 20.0, None], min_samples=3) is None

        # 3 valid samples + 1 None -> effective n=3 -> returns median
        assert calculate_median_rtt([10.0, None, 20.0, 30.0], min_samples=3) == 20.0


@pytest.mark.unit
class TestP95RTTCalculation:
    """Verifies 95th percentile RTT calculation under sample size constraints (n >= 10)."""

    def test_p95_with_insufficient_samples_returns_none(self):
        """Sample count n < 10 must return None."""
        samples = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
        assert calculate_p95_rtt(samples, min_samples=10) is None

    def test_p95_with_sufficient_samples_nearest_rank(self):
        """Sample count n=10, 20 returns accurate 95th percentile using nearest-rank."""
        samples_10 = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0]
        # rank = ceil(0.95 * 10) - 1 = 10 - 1 = 9 -> index 9 is 28.0
        assert calculate_p95_rtt(samples_10, min_samples=10) == 28.0

        samples_20 = [float(i) for i in range(1, 21)]  # 1.0 to 20.0
        # rank = ceil(0.95 * 20) - 1 = 19 - 1 = 18 -> index 18 is 19.0
        assert calculate_p95_rtt(samples_20, min_samples=10) == 19.0


# ============================================================================
# TC-UNIT-02: Round-Trip Delay Variation (MACD Jitter)
# ============================================================================

@pytest.mark.unit
class TestMACDJitterCalculation:
    """Verifies Mean Absolute Consecutive Difference (MACD) jitter calculation."""

    def test_macd_jitter_known_sequence(self):
        """Known sequence: [10.0, 15.0, 12.0] -> diffs: |15-10|=5, |12-15|=3 -> avg = 8/2 = 4.0."""
        jitter = calculate_macd_jitter([10.0, 15.0, 12.0])
        assert jitter == 4.0

    def test_macd_jitter_constant_rtt(self):
        """Constant RTT results in exactly 0.0 ms jitter."""
        jitter = calculate_macd_jitter([15.0, 15.0, 15.0, 15.0])
        assert jitter == 0.0

    def test_macd_jitter_insufficient_samples(self):
        """Samples k < 2 cannot produce consecutive differences and must return None."""
        assert calculate_macd_jitter([]) is None
        assert calculate_macd_jitter([12.5]) is None
        assert calculate_macd_jitter([None, 12.5, None]) is None

    def test_macd_jitter_filters_none(self):
        """Missing or timed-out probes (None) are excluded from the sequence."""
        # [10.0, None, 20.0] -> valid: [10.0, 20.0] -> k=2 -> |20-10| = 10.0
        assert calculate_macd_jitter([10.0, None, 20.0]) == 10.0


# ============================================================================
# Summary Distribution Statistics Aggregator
# ============================================================================

@pytest.mark.unit
class TestDelaySummaryAggregator:
    """Verifies complete delay summary dictionary computation and non-zero null invariants."""

    def test_empty_samples_returns_all_nones(self):
        """Empty or fully timed-out probe sequences yield None for all delay metrics."""
        stats = calculate_delay_summary_stats([])
        assert stats["min_rtt"] is None
        assert stats["avg_rtt"] is None
        assert stats["max_rtt"] is None
        assert stats["median_rtt"] is None
        assert stats["p95_rtt"] is None
        assert stats["jitter_macd"] is None

    def test_single_sample_summary(self):
        """Single sample satisfies min/avg/max, but median, p95, and jitter are None."""
        stats = calculate_delay_summary_stats([12.5])
        assert stats["min_rtt"] == 12.5
        assert stats["avg_rtt"] == 12.5
        assert stats["max_rtt"] == 12.5
        assert stats["median_rtt"] is None  # Requires n >= 3
        assert stats["p95_rtt"] is None     # Requires n >= 10
        assert stats["jitter_macd"] is None # Requires k >= 2

    def test_complete_four_probe_summary(self):
        """Typical 4-probe successful sequence computes min, avg, max, median, jitter."""
        rtts = [10.0, 12.0, 14.0, 16.0]
        stats = calculate_delay_summary_stats(rtts)
        assert stats["min_rtt"] == 10.0
        assert stats["avg_rtt"] == 13.0
        assert stats["max_rtt"] == 16.0
        assert stats["median_rtt"] == 13.0
        assert stats["p95_rtt"] is None  # n=4 < 10
        assert stats["jitter_macd"] == 2.0

    def test_non_zero_null_invariant(self):
        """Ensures that failed or timed-out probes are never represented as 0.0 ms."""
        # If all probes timed out, stats must NOT show min_rtt=0.0 or avg_rtt=0.0
        stats = calculate_delay_summary_stats([])
        for metric, val in stats.items():
            assert val is None, f"Expected None for {metric}, got {val}"
