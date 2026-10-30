"""Statistical Analytics and Explainable Network Slowdown Detection Engine.

Compliant with TicNet Stage 2 requirements:
- Deterministic, explainable baseline calculation from genuine historical measurements.
- Distinguishes transient isolated spikes from sustained multi-run deterioration.
- Evaluates metrics independently (Latency, Jitter, Packet Loss, Download Speed).
- Never penalizes missing measurements, unsupported upload tests, or insufficient data.
- Provides 24-hour, 7-day, and 30-day time window evaluations.
- Distinguishes NORMAL, SUSPECTED_SLOWDOWN, CONFIRMED_SLOWDOWN, CONFIRMED_RECOVERY, and INSUFFICIENT_DATA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple
import uuid

from ticnet.models.monitoring import MonitoringRun, now_utc_iso


def new_id() -> str:
    """Generate a random UUID4 string."""
    return str(uuid.uuid4())


class TimeWindow(str, Enum):
    """Supported historical analytics evaluation windows."""
    H24 = "24H"
    D7 = "7D"
    D30 = "30D"

    @property
    def timedelta(self) -> timedelta:
        if self == TimeWindow.H24:
            return timedelta(hours=24)
        elif self == TimeWindow.D7:
            return timedelta(days=7)
        elif self == TimeWindow.D30:
            return timedelta(days=30)
        return timedelta(hours=24)

    @property
    def display_label(self) -> str:
        if self == TimeWindow.H24:
            return "Last 24 Hours"
        elif self == TimeWindow.D7:
            return "Last 7 Days"
        elif self == TimeWindow.D30:
            return "Last 30 Days"
        return "Last 24 Hours"


class DetectionStatus(str, Enum):
    """Explainable classification of network performance relative to baseline."""
    NORMAL = "NORMAL"
    SUSPECTED_SLOWDOWN = "SUSPECTED_SLOWDOWN"
    CONFIRMED_SLOWDOWN = "CONFIRMED_SLOWDOWN"
    CONFIRMED_RECOVERY = "CONFIRMED_RECOVERY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class SlowdownEvent:
    """Persistent audit record of a detected slowdown or recovery event."""
    event_id: str = field(default_factory=new_id)
    monitoring_run_id: Optional[str] = None
    metric_name: str = "overall"
    detection_status: str = DetectionStatus.NORMAL.value
    baseline_value: Optional[float] = None
    observed_value: Optional[float] = None
    deviation_pct: Optional[float] = None
    threshold_applied: str = ""
    time_window: str = TimeWindow.H24.value
    consecutive_breaches: int = 0
    explanation: str = ""
    detected_at_utc: str = field(default_factory=now_utc_iso)


@dataclass
class MetricEvaluation:
    """Evaluation result for an individual network performance metric."""
    metric_name: str
    status: DetectionStatus
    baseline_value: Optional[float]
    observed_value: Optional[float]
    deviation_pct: Optional[float]
    threshold_applied: str
    consecutive_breaches: int
    explanation: str
    sample_count: int


@dataclass
class SlowdownReport:
    """Consolidated slowdown and performance evaluation report across all metrics."""
    overall_status: DetectionStatus
    time_window: TimeWindow
    total_runs_evaluated: int
    valid_measurements_count: int
    evaluations: Dict[str, MetricEvaluation]
    summary_headline: str
    summary_detail: str
    generated_at_utc: str = field(default_factory=now_utc_iso)


@dataclass
class AnalyticsTrendPoint:
    """Individual chronological data point for trend charting."""
    timestamp_utc: str
    monitoring_run_id: str
    run_id: Optional[str]
    trigger_type: str
    status: str
    latency_ms: Optional[float]
    jitter_ms: Optional[float]
    packet_loss_pct: Optional[float]
    download_mbps: Optional[float]


MIN_BASELINE_SAMPLES = 3


def calculate_median(values: List[float]) -> Optional[float]:
    """Calculate the median of a list of numeric values. Returns None if empty."""
    valid = [v for v in values if v is not None and not math.isnan(v)]
    if not valid:
        return None
    valid.sort()
    n = len(valid)
    mid = n // 2
    if n % 2 == 1:
        return float(valid[mid])
    else:
        return float((valid[mid - 1] + valid[mid]) / 2.0)


class AnalyticsEngine:
    """Deterministic analytics and slowdown detection engine."""

    @staticmethod
    def evaluate_metric(
        metric_name: str,
        series: List[Optional[float]],
        previous_status: Optional[DetectionStatus] = None,
    ) -> MetricEvaluation:
        """Evaluate a specific metric over a chronological series (earliest to latest).

        Args:
            metric_name: 'latency', 'jitter', 'packet_loss', or 'download_speed'.
            series: Chronological list of values (last element is the most recent reading).
            previous_status: Previous detection status to detect recoveries.

        Returns:
            MetricEvaluation with explainable status and baseline comparison.
        """
        valid_values = [v for v in series if v is not None and not math.isnan(v)]
        count = len(valid_values)

        if count < MIN_BASELINE_SAMPLES:
            return MetricEvaluation(
                metric_name=metric_name,
                status=DetectionStatus.INSUFFICIENT_DATA,
                baseline_value=None,
                observed_value=valid_values[-1] if valid_values else None,
                deviation_pct=None,
                threshold_applied="N/A",
                consecutive_breaches=0,
                explanation=f"Insufficient history ({count}/{MIN_BASELINE_SAMPLES} minimum samples required to establish baseline).",
                sample_count=count,
            )

        # Baseline is strictly computed over prior historical points, excluding the observation currently evaluated
        baseline_pool = valid_values[:-1]
        baseline = calculate_median(baseline_pool)
        observed = valid_values[-1]

        if baseline is None or observed is None:
            return MetricEvaluation(
                metric_name=metric_name,
                status=DetectionStatus.INSUFFICIENT_DATA,
                baseline_value=baseline,
                observed_value=observed,
                deviation_pct=None,
                threshold_applied="N/A",
                consecutive_breaches=0,
                explanation="Missing or invalid metric values.",
                sample_count=count,
            )

        # 1. Metric-specific breach evaluator
        is_breach, threshold_desc, dev_pct = AnalyticsEngine._check_metric_breach(
            metric_name=metric_name,
            observed=observed,
            baseline=baseline,
        )

        # 2. Check consecutive breaches in reverse chronological order
        consecutive_breaches = 0
        for val in reversed(valid_values):
            breach, _, _ = AnalyticsEngine._check_metric_breach(metric_name, val, baseline)
            if breach:
                consecutive_breaches += 1
            else:
                break

        # 3. Determine status and explainable diagnosis
        if consecutive_breaches >= 2:
            status = DetectionStatus.CONFIRMED_SLOWDOWN
            explanation = (
                f"Sustained degradation confirmed across {consecutive_breaches} consecutive measurements. "
                f"Observed {observed:.1f} vs baseline {baseline:.1f} ({threshold_desc})."
            )
        elif consecutive_breaches == 1:
            status = DetectionStatus.SUSPECTED_SLOWDOWN
            explanation = (
                f"Isolated deviation observed ({observed:.1f} vs baseline {baseline:.1f}). "
                f"Awaiting subsequent tests to verify if deterioration is sustained."
            )
        else:
            # 0 breaches on latest observation
            # Check if immediately preceding observation was in breach (transition to recovery)
            prior_breached = False
            if len(valid_values) >= 2:
                prior_breached, _, _ = AnalyticsEngine._check_metric_breach(
                    metric_name, valid_values[-2], baseline
                )

            if prior_breached or previous_status in (DetectionStatus.CONFIRMED_SLOWDOWN, DetectionStatus.SUSPECTED_SLOWDOWN):
                # Only report recovery if the immediate prior run was actually in breach
                # If prior was already nominal, this run is simply NORMAL, not another recovery event
                if prior_breached:
                    status = DetectionStatus.CONFIRMED_RECOVERY
                    explanation = (
                        f"Performance has recovered to normal baseline thresholds "
                        f"({observed:.1f} vs baseline {baseline:.1f})."
                    )
                else:
                    status = DetectionStatus.NORMAL
                    explanation = (
                        f"Performance is nominal ({observed:.1f} is consistent with baseline {baseline:.1f})."
                    )
            else:
                status = DetectionStatus.NORMAL
                explanation = (
                    f"Performance is nominal ({observed:.1f} is consistent with baseline {baseline:.1f})."
                )

        return MetricEvaluation(
            metric_name=metric_name,
            status=status,
            baseline_value=round(baseline, 2),
            observed_value=round(observed, 2),
            deviation_pct=round(dev_pct, 1) if dev_pct is not None else None,
            threshold_applied=threshold_desc,
            consecutive_breaches=consecutive_breaches,
            explanation=explanation,
            sample_count=count,
        )

    @staticmethod
    def _check_metric_breach(
        metric_name: str,
        observed: float,
        baseline: float,
    ) -> Tuple[bool, str, Optional[float]]:
        """Evaluate if an observed metric breaches baseline thresholds."""
        if metric_name == "latency":
            # Latency slowdown: >= 40% increase AND at least +15ms delta
            delta = observed - baseline
            dev_pct = ((delta / baseline) * 100.0) if baseline > 0 else (100.0 if delta >= 15.0 else 0.0)
            is_breach = (observed >= baseline * 1.40) and (delta >= 15.0)
            threshold_desc = f"+40% and +15ms (Δ: +{delta:.1f} ms)"
            return is_breach, threshold_desc, dev_pct

        elif metric_name == "jitter":
            # Jitter slowdown: >= 50% increase AND at least +3ms delta
            delta = observed - baseline
            dev_pct = ((delta / baseline) * 100.0) if baseline > 0 else (100.0 if delta >= 3.0 else 0.0)
            is_breach = (observed >= baseline * 1.50) and (delta >= 3.0)
            threshold_desc = f"+50% and +3ms (Δ: +{delta:.1f} ms)"
            return is_breach, threshold_desc, dev_pct

        elif metric_name == "packet_loss":
            # Packet loss: baseline is typically 0%, any sustained loss >= 3.0% is a degradation
            dev_pct = observed - baseline
            is_breach = observed >= 3.0
            threshold_desc = f"Loss >= 3.0% (Observed: {observed:.1f}%)"
            return is_breach, threshold_desc, dev_pct

        elif metric_name == "download_speed":
            # Download throughput slowdown: lower is worse.
            # Handles zero and near-zero baselines gracefully.
            if baseline <= 0.0:
                return False, "Baseline is zero (no prior throughput established)", 0.0

            delta = baseline - observed
            dev_pct = -((delta / baseline) * 100.0)

            # For low-bandwidth / near-zero baselines, absolute drop threshold is proportionally bounded
            # Rather than requiring >= 10 Mbps on e.g. a 5 Mbps link, require min(10.0, baseline * 0.35)
            min_drop = min(10.0, max(0.5, baseline * 0.35))
            is_breach = (observed <= baseline * 0.65) and (delta >= min_drop)
            threshold_desc = f"Drop >= 35% and >= {min_drop:.1f} Mbps (Δ: -{delta:.1f} Mbps)"
            return is_breach, threshold_desc, dev_pct

        return False, "Unknown metric", None

    @classmethod
    def evaluate_runs(
        cls,
        runs: List[MonitoringRun],
        window: TimeWindow = TimeWindow.H24,
        previous_status: Optional[DetectionStatus] = None,
    ) -> SlowdownReport:
        """Evaluate a set of historical monitoring runs within a specified time window.

        Args:
            runs: Chronological list of MonitoringRun records (earliest to newest).
            window: Evaluation time window (24H, 7D, 30D).
            previous_status: Previous system slowdown status.

        Returns:
            Consolidated SlowdownReport.
        """
        completed_runs = [
            r for r in runs
            if r.status in ("COMPLETED", "PARTIAL")
        ]

        latencies = [r.latency_ms for r in completed_runs]
        jitters = [r.jitter_ms for r in completed_runs]
        losses = [r.packet_loss_pct for r in completed_runs]
        downloads = [r.download_mbps for r in completed_runs]

        eval_lat = cls.evaluate_metric("latency", latencies, previous_status)
        eval_jit = cls.evaluate_metric("jitter", jitters, previous_status)
        eval_loss = cls.evaluate_metric("packet_loss", losses, previous_status)
        eval_dl = cls.evaluate_metric("download_speed", downloads, previous_status)

        evaluations: Dict[str, MetricEvaluation] = {
            "latency": eval_lat,
            "jitter": eval_jit,
            "packet_loss": eval_loss,
            "download_speed": eval_dl,
        }

        # Overall Status Resolution
        statuses = [e.status for e in evaluations.values()]
        active_evaluations = [e for e in evaluations.values() if e.sample_count >= MIN_BASELINE_SAMPLES]
        active_statuses = [e.status for e in active_evaluations]

        if DetectionStatus.CONFIRMED_SLOWDOWN in statuses:
            overall = DetectionStatus.CONFIRMED_SLOWDOWN
            breached_metrics = [e.metric_name for e in evaluations.values() if e.status == DetectionStatus.CONFIRMED_SLOWDOWN]
            headline = f"Confirmed Network Slowdown Detected ({', '.join(breached_metrics)})"
            detail = (
                f"Sustained performance deterioration was detected across consecutive tests in the {window.display_label.lower()}. "
                f"Degradation exceeds baseline thresholds."
            )
        elif DetectionStatus.SUSPECTED_SLOWDOWN in statuses:
            overall = DetectionStatus.SUSPECTED_SLOWDOWN
            suspected_metrics = [e.metric_name for e in evaluations.values() if e.status == DetectionStatus.SUSPECTED_SLOWDOWN]
            headline = f"Suspected Performance Variation ({', '.join(suspected_metrics)})"
            detail = (
                f"Transient deviation observed in {', '.join(suspected_metrics)}. "
                f"Monitoring subsequent test runs to confirm whether this represents a sustained slowdown."
            )
        elif DetectionStatus.CONFIRMED_RECOVERY in statuses:
            overall = DetectionStatus.CONFIRMED_RECOVERY
            headline = "Network Performance Recovered"
            detail = "Previously degraded performance metrics have returned to nominal baseline levels."
        elif active_statuses and all(s == DetectionStatus.NORMAL for s in active_statuses):
            overall = DetectionStatus.NORMAL
            headline = "Nominal Network Stability"
            detail = f"All evaluated performance metrics conform to historical baseline thresholds over the {window.display_label.lower()}."
        else:
            overall = DetectionStatus.INSUFFICIENT_DATA
            headline = "Insufficient Historical Data"
            detail = f"Fewer than {MIN_BASELINE_SAMPLES} completed measurements exist in the {window.display_label.lower()} to compute an empirical baseline."

        return SlowdownReport(
            overall_status=overall,
            time_window=window,
            total_runs_evaluated=len(runs),
            valid_measurements_count=len(completed_runs),
            evaluations=evaluations,
            summary_headline=headline,
            summary_detail=detail,
        )

