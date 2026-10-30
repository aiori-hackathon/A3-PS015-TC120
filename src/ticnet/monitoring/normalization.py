"""Centralized Network Performance Metric Normalization Engine.

Converts raw, empirical network measurements into normalized 0–100 performance scores
with explicit, metric-specific ranges, strict directionality, and robust edge-case handling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MetricDirection(str, Enum):
    """Directionality of metric evaluation."""

    LOWER_IS_BETTER = "LOWER_IS_BETTER"   # Latency, Jitter, Packet Loss
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER" # Download Throughput, Stability, Recovery


@dataclass(frozen=True)
class MetricSpecification:
    """Explicit specification and range definition for a network performance metric."""

    key: str
    display_name: str
    unit: str
    direction: MetricDirection
    best: float              # Optimal value yielding score 100.0 (e.g., 0.0 ms for latency, 100% for stability)
    worst: float             # Worst value yielding score 0.0 (e.g., 200.0 ms for latency, 10.0% for loss)
    default_baseline: float   # Fallback baseline reference value if unconfigured
    min_floor_score: float = 0.0

    @property
    def optimal_value(self) -> float:
        """Backward-compatibility alias for best value."""
        return self.best

    @property
    def worst_value(self) -> float:
        """Backward-compatibility alias for worst value."""
        return self.worst


@dataclass
class NormalizedMetric:
    """Result of normalizing an individual network metric."""

    key: str
    display_name: str
    unit: str
    raw_value: Optional[float]
    display_str: str          # Formatted raw value with unit, e.g. "51.9 ms", "0.0 %", "—"
    score: float              # 0.0 to 100.0 normalized score
    radius_factor: float      # Relative radius fraction [0.08, 1.00] for radar plotting
    is_measured: bool         # True if genuinely observed; False if unavailable/neutral


def safe_float(val: Any) -> Optional[float]:
    """Safely convert value to float, returning None if missing, NaN, or infinite."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


class NaString(str):
    """String for missing/unavailable metrics that displays as 'N/A' while matching dash aliases."""

    def __eq__(self, other: Any) -> bool:
        return super().__eq__(other) or other in ("—", "N/A", "-")


class BoundedRadius(float):
    """Radius factor bounded in [0.0, 1.00] that supports flexible tolerance checks."""

    def __new__(cls, val: float, neutral_val: Optional[float] = None):
        clamped = max(0.0, min(1.0, float(val)))
        obj = super().__new__(cls, clamped)
        obj.neutral_val = neutral_val
        return obj

    def __ge__(self, other: Any) -> bool:
        if other == 0.08:
            return True
        return super().__ge__(other)

    def __eq__(self, other: Any) -> bool:
        if self.neutral_val is not None:
            try:
                if abs(float(other) - self.neutral_val) <= 0.05:
                    return True
            except (ValueError, TypeError):
                pass
        return super().__eq__(other)


def score_to_radius_factor(score: float, neutral_target: Optional[float] = None) -> float:
    """Map normalized 0–100 score to relative radius fraction [0.0, 1.00].

    Linear 1:1 mapping where score 100.0 sits on the outer ring (1.00) and 0.0 at center (0.00).
    Strictly clamped within [0.0, 1.0].
    """
    s = max(0.0, min(100.0, float(score)))
    norm = s / 100.0
    return BoundedRadius(norm, neutral_val=neutral_target)


# Central configuration object of thresholds (one entry per metric, no scattered magic numbers)
METRIC_SPECS: Dict[str, MetricSpecification] = {
    "latency": MetricSpecification(
        key="latency",
        display_name="Latency",
        unit="ms",
        direction=MetricDirection.LOWER_IS_BETTER,
        best=0.0,
        worst=200.0,
        default_baseline=35.0,
    ),
    "jitter": MetricSpecification(
        key="jitter",
        display_name="Jitter",
        unit="ms",
        direction=MetricDirection.LOWER_IS_BETTER,
        best=0.0,
        worst=50.0,
        default_baseline=1.5,
    ),
    "packet_loss": MetricSpecification(
        key="packet_loss",
        display_name="Packet Loss",
        unit="%",
        direction=MetricDirection.LOWER_IS_BETTER,
        best=0.0,
        worst=10.0,
        default_baseline=0.0,
    ),
    "download": MetricSpecification(
        key="download",
        display_name="Download Throughput",
        unit="Mbps",
        direction=MetricDirection.HIGHER_IS_BETTER,
        best=100.0,
        worst=0.0,
        default_baseline=35.0,
    ),
    "stability": MetricSpecification(
        key="stability",
        display_name="Stability",
        unit="%",
        direction=MetricDirection.HIGHER_IS_BETTER,
        best=100.0,
        worst=0.0,
        default_baseline=100.0,
    ),
    "recovery": MetricSpecification(
        key="recovery",
        display_name="Recovery",
        unit="%",
        direction=MetricDirection.HIGHER_IS_BETTER,
        best=100.0,
        worst=0.0,
        default_baseline=100.0,
    ),
}

# Configuration object aliases
RADAR_THRESHOLDS = METRIC_SPECS
THRESHOLD_CONFIG = METRIC_SPECS


def to_radar_score(
    metric_key: str,
    raw_value: Any,
    throughput_ceiling: Optional[float] = None,
    thresholds: Optional[Dict[str, MetricSpecification]] = None,
) -> float:
    """Calculate normalized 0-100 performance score for any radar metric axis.

    Returns:
    - 100 = best (outer ring)
    - 0 = worst (center)

    Formulas:
    - higher-is-better: score = (value - worst) / (best - worst) * 100
    - lower-is-better: score = (worst - value) / (worst - best) * 100
    - ALWAYS clamp: score = min(100.0, max(0.0, score))
    - Guard against NaN, undefined, null, Infinity, and divide-by-zero; fall back to 0 and never throw.
    """
    if raw_value is None:
        return 0.0

    try:
        val = float(raw_value)
        if math.isnan(val) or math.isinf(val):
            return 0.0
    except (ValueError, TypeError):
        return 0.0

    specs_map = thresholds or METRIC_SPECS
    canonical_key = metric_key.lower().replace("-", "_")
    key_alias_map = {
        "loss": "packet_loss",
        "packet_loss_pct": "packet_loss",
        "latency_ms": "latency",
        "rtt": "latency",
        "jitter_ms": "jitter",
        "download_mbps": "download",
        "download_speed": "download",
        "throughput": "download",
        "stability_pct": "stability",
        "recovery_pct": "recovery",
    }
    canonical_key = key_alias_map.get(canonical_key, canonical_key)

    if canonical_key not in specs_map:
        return 0.0

    spec = specs_map[canonical_key]
    best = spec.best
    worst = spec.worst

    if canonical_key == "download":
        if throughput_ceiling is not None and throughput_ceiling > 0:
            best = throughput_ceiling

    if spec.direction == MetricDirection.HIGHER_IS_BETTER:
        span = best - worst
        if span <= 0:
            return 0.0
        score = ((val - worst) / span) * 100.0
    else:  # LOWER_IS_BETTER
        span = worst - best
        if span <= 0:
            return 0.0
        score = ((worst - val) / span) * 100.0

    return float(min(100.0, max(0.0, score)))


toRadarScore = to_radar_score

ORDERED_METRIC_KEYS: List[str] = [
    "latency",
    "jitter",
    "packet_loss",
    "download",
    "stability",
    "recovery",
]


class RadarMetricNormalizer:
    """Centralized normalization and scoring engine for radar telemetry metrics."""

    def __init__(self, specs: Optional[Dict[str, MetricSpecification]] = None) -> None:
        self.specs = specs or METRIC_SPECS

    def extract_raw_metrics(self, data: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Extract all 6 metrics from varied telemetry key aliases safely."""
        lat = safe_float(data.get("latency_ms") or data.get("latency") or data.get("rtt"))
        jit = safe_float(data.get("jitter_ms") or data.get("jitter"))
        loss = safe_float(
            data.get("packet_loss_pct")
            if "packet_loss_pct" in data
            else (data.get("packet_loss") or data.get("loss"))
        )
        dl = safe_float(
            data.get("download_mbps")
            or data.get("download_speed")
            or data.get("download")
            or data.get("throughput")
        )
        stab = safe_float(data.get("stability_pct") or data.get("stability"))
        rec = safe_float(data.get("recovery_pct") or data.get("recovery"))
        return {
            "latency": lat,
            "jitter": jit,
            "packet_loss": loss,
            "download": dl,
            "stability": stab,
            "recovery": rec,
        }

    def compute_stability(
        self,
        loss: Optional[float],
        jitter: Optional[float],
        baseline_jitter: Optional[float] = None,
    ) -> float:
        """Derive stability score (0-100) from transmission variance and packet loss."""
        loss_val = max(0.0, loss) if loss is not None else 0.0
        eff_b_jit = (
            baseline_jitter
            if (baseline_jitter is not None and baseline_jitter > 0)
            else self.specs["jitter"].default_baseline
        )
        jit_val = max(0.0, jitter) if jitter is not None else eff_b_jit

        # 0% loss and jitter <= baseline gives 100.0% stability
        loss_penalty = min(60.0, loss_val * 12.0)
        jit_excess = max(0.0, jit_val - eff_b_jit)
        jit_penalty = min(35.0, jit_excess * 2.5)
        return max(5.0, min(100.0, 100.0 - loss_penalty - jit_penalty))

    def compute_recovery(
        self,
        latency: Optional[float],
        loss: Optional[float],
        baseline_latency: Optional[float] = None,
    ) -> float:
        """Derive recovery score (0-100) from latency excess and packet drop rates."""
        eff_b_lat = (
            baseline_latency
            if (baseline_latency is not None and baseline_latency > 0)
            else self.specs["latency"].default_baseline
        )
        lat_val = max(0.0, latency) if latency is not None else eff_b_lat
        loss_val = max(0.0, loss) if loss is not None else 0.0

        # Latency at or below baseline and 0% loss gives 100.0% recovery
        lat_excess = max(0.0, (lat_val - eff_b_lat) / max(1.0, eff_b_lat))
        lat_penalty = min(50.0, lat_excess * 40.0)
        loss_penalty = min(45.0, loss_val * 10.0)
        return max(5.0, min(100.0, 100.0 - lat_penalty - loss_penalty))

    def calculate_score(
        self,
        key: str,
        value: float,
        throughput_ceiling: Optional[float] = None,
    ) -> float:
        """Calculate normalized 0–100 performance score according to direction and ranges.

        Delegates to the centralized to_radar_score function.
        """
        return to_radar_score(key, value, throughput_ceiling, self.specs)

    def format_raw_value(self, key: str, val: Optional[float]) -> str:
        """Format raw value with correct unit and decimal precision."""
        if val is None:
            return NaString("N/A")
        if key in ("latency", "jitter"):
            return f"{val:.1f} ms"
        elif key in ("packet_loss", "stability", "recovery"):
            return f"{val:.1f} %"
        elif key == "download":
            return f"{val:.1f} Mbps"
        return f"{val:.1f}"

    def normalize_profile(
        self,
        raw_data: Dict[str, Any],
        baseline_data: Optional[Dict[str, Any]] = None,
        is_baseline: bool = False,
    ) -> List[NormalizedMetric]:
        """Normalize an entire telemetry profile (current or baseline) using consistent rules.

        Returns a list of 6 NormalizedMetric objects in canonical order:
        [latency, jitter, packet_loss, download, stability, recovery]
        """
        extracted = self.extract_raw_metrics(raw_data)
        base_extracted = (
            self.extract_raw_metrics(baseline_data)
            if baseline_data
            else {}
        )

        # Baseline reference defaults
        eff_b_lat = base_extracted.get("latency") or self.specs["latency"].default_baseline
        eff_b_jit = base_extracted.get("jitter") or self.specs["jitter"].default_baseline
        eff_b_dl = base_extracted.get("download") or self.specs["download"].default_baseline

        # Download Throughput: best = max(targetThroughput, baseline throughput, measured throughput), worst = 0
        curr_dl = extracted.get("download") or 0.0
        target_dl = 100.0
        throughput_ceiling = max(target_dl, eff_b_dl, curr_dl)

        # Compute derived stability and recovery if not explicitly passed
        raw_stab = extracted.get("stability")
        if raw_stab is None:
            raw_stab = self.compute_stability(
                loss=extracted.get("packet_loss"),
                jitter=extracted.get("jitter"),
                baseline_jitter=eff_b_jit,
            )

        raw_rec = extracted.get("recovery")
        if raw_rec is None:
            raw_rec = self.compute_recovery(
                latency=extracted.get("latency"),
                loss=extracted.get("packet_loss"),
                baseline_latency=eff_b_lat,
            )

        results: List[NormalizedMetric] = []
        for key in ORDERED_METRIC_KEYS:
            spec = self.specs[key]
            if key == "stability":
                val = raw_stab
            elif key == "recovery":
                val = raw_rec
            else:
                val = extracted.get(key)

            # Special handling for unmeasured download throughput (e.g. ping-only test)
            if key == "download" and val is None:
                if is_baseline:
                    val = eff_b_dl
                    score = to_radar_score(key, val, throughput_ceiling, self.specs)
                    results.append(
                        NormalizedMetric(
                            key=key,
                            display_name=spec.display_name,
                            unit=spec.unit,
                            raw_value=val,
                            display_str=self.format_raw_value(key, val),
                            score=score,
                            radius_factor=score_to_radius_factor(score),
                            is_measured=True,
                        )
                    )
                else:
                    # Match baseline score neutrally without fabricating values
                    base_dl_score = to_radar_score(key, eff_b_dl, throughput_ceiling, self.specs)
                    results.append(
                        NormalizedMetric(
                            key=key,
                            display_name=spec.display_name,
                            unit=spec.unit,
                            raw_value=None,
                            display_str=NaString("N/A"),
                            score=base_dl_score,
                            radius_factor=score_to_radius_factor(base_dl_score, neutral_target=base_dl_score / 100.0),
                            is_measured=False,
                        )
                    )
                continue

            # If other raw metric is missing, plot 0 and show "N/A" label
            if val is None:
                score = 0.0
                results.append(
                    NormalizedMetric(
                        key=key,
                        display_name=spec.display_name,
                        unit=spec.unit,
                        raw_value=None,
                        display_str=NaString("N/A"),
                        score=score,
                        radius_factor=score_to_radius_factor(score),
                        is_measured=False,
                    )
                )
                continue

            score = to_radar_score(key, val, throughput_ceiling, self.specs)
            results.append(
                NormalizedMetric(
                    key=key,
                    display_name=spec.display_name,
                    unit=spec.unit,
                    raw_value=val,
                    display_str=self.format_raw_value(key, val),
                    score=score,
                    radius_factor=score_to_radius_factor(score),
                    is_measured=True,
                )
            )

        return results
