"""R-LOAD-01: Latency Increase Under Load (Bufferbloat) Diagnostic Rule."""

from __future__ import annotations

import json
from typing import List, Optional

from ticnet.diagnosis.context import DiagnosisContext
from ticnet.diagnosis.rules.base import BaseDiagnosticRule, RuleEvaluationResult
from ticnet.models.entities import FindingEvidence
from ticnet.models.enums import (
    EvidenceRelationship,
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
)


class BufferbloatRule(BaseDiagnosticRule):
    """Rule R-LOAD-01: Detects significant latency degradation under active network load (bufferbloat)."""

    @property
    def rule_id(self) -> str:
        return "R-LOAD-01"

    @property
    def rule_version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> FindingCategory:
        return FindingCategory.BUFFERBLOAT

    @property
    def priority(self) -> FindingPriority:
        return FindingPriority.WARNING

    def evaluate(self, ctx: DiagnosisContext) -> Optional[RuleEvaluationResult]:
        delta_threshold_ms = 50.0
        ratio_threshold = 2.5
        min_samples = 5

        if ctx.thresholds:
            if ctx.thresholds.loaded_thresholds:
                delta_threshold_ms = ctx.thresholds.loaded_thresholds.bufferbloat_rtt_delta_ms
                ratio_threshold = ctx.thresholds.loaded_thresholds.bufferbloat_ratio
            if ctx.thresholds.sample_requirements:
                min_samples = ctx.thresholds.sample_requirements.min_loaded_samples

        delta_obs = ctx.loaded_latency_delta_observation
        meta = ctx.loaded_latency_metadata

        # If no loaded latency observation exists, check summaries
        delta_val: Optional[float] = None
        ratio_val: Optional[float] = None
        baseline_valid = 0
        loaded_valid = 0
        baseline_jitter: Optional[float] = None

        if delta_obs is not None and delta_obs.status == MeasurementStatus.SUCCESS and meta:
            baseline_valid = meta.get("baseline_valid_count", 0)
            loaded_valid = meta.get("loaded_valid_count", 0)
            delta_val = meta.get("delta_rtt_median_ms")
            ratio_val = meta.get("bufferbloat_ratio")
        else:
            # Check if summary records were created
            delta_sum = ctx.get_summary(None, "bufferbloat_delta_ms", "delta")
            ratio_sum = ctx.get_summary(None, "bufferbloat_ratio", "ratio")
            if delta_sum:
                delta_val = delta_sum.value_numeric
                baseline_valid = delta_sum.sample_count_valid
                loaded_valid = delta_sum.sample_count_valid
            if ratio_sum:
                ratio_val = ratio_sum.value_numeric

        # Trigger condition 1 & 2: Baseline valid >= 5, loaded valid >= 5
        if baseline_valid < min_samples or loaded_valid < min_samples:
            return None

        # Exclusion check: If baseline idle latency was already unstable (jitter > 40ms)
        gw_jitter_sum = ctx.gateway_jitter_summary
        if gw_jitter_sum and gw_jitter_sum.value_numeric is not None:
            baseline_jitter = gw_jitter_sum.value_numeric
            if baseline_jitter > 40.0:
                # Baseline was already too jittery to reliably identify bufferbloat
                return None

        # Trigger condition 3: (delta > 50ms) OR (ratio > 2.5)
        has_delta_trigger = delta_val is not None and delta_val > delta_threshold_ms
        has_ratio_trigger = ratio_val is not None and ratio_val > ratio_threshold

        if not (has_delta_trigger or has_ratio_trigger):
            return None

        plain_summary = (
            "Network latency increases significantly when the connection is busy downloading or "
            "uploading. This condition (bufferbloat) causes video calls to freeze, gaming to lag, "
            "and web pages to stall whenever another device or background application transfers data."
        )
        limitations = (
            "Does not distinguish whether unmanaged packet buffering occurs inside the home router "
            "or within the ISP modem/cable-headend."
        )
        recommended_action = (
            "Enable Smart Queue Management (SQM) such as CAKE or FQ-CoDel on your router if supported, "
            "or establish QoS bandwidth limits."
        )

        strength = EvidenceStrength.STRONG if (has_delta_trigger and has_ratio_trigger) else EvidenceStrength.MODERATE

        finding = self.create_finding(
            run_id=ctx.run_id,
            plain_summary=plain_summary,
            limitations=limitations,
            recommended_action=recommended_action,
            status=FindingStatus.SUPPORTED,
            evidence_strength=strength,
            priority=FindingPriority.WARNING,
            category=FindingCategory.BUFFERBLOAT,
        )

        evidence: List[FindingEvidence] = []
        if delta_obs:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    observation_id=delta_obs.observation_id,
                )
            )

        # Also link individual loaded observations
        for obs in ctx.get_observations_by_metric("loaded_rtt"):
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    observation_id=obs.observation_id,
                )
            )

        return RuleEvaluationResult(finding=finding, evidence=evidence)
