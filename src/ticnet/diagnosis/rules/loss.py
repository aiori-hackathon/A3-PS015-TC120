"""R-LOSS-01: Possible External Path Degradation (Last-Mile / ISP) Diagnostic Rule."""

from __future__ import annotations

from typing import List, Optional

from ticnet.diagnosis.context import DiagnosisContext
from ticnet.diagnosis.rules.base import BaseDiagnosticRule, RuleEvaluationResult
from ticnet.models.entities import FindingEvidence, Summary
from ticnet.models.enums import (
    EvidenceRelationship,
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
)


class ExternalPathDegradationRule(BaseDiagnosticRule):
    """Rule R-LOSS-01: Detects external path or last-mile degradation across multiple destinations."""

    @property
    def rule_id(self) -> str:
        return "R-LOSS-01"

    @property
    def rule_version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> FindingCategory:
        return FindingCategory.EXTERNAL_PATH

    @property
    def priority(self) -> FindingPriority:
        return FindingPriority.WARNING

    def evaluate(self, ctx: DiagnosisContext) -> Optional[RuleEvaluationResult]:
        gw_loss_limit = 5.0
        ext_loss_threshold = 15.0
        ext_jitter_threshold = 30.0
        min_samples = 10
        min_degraded = 2

        if ctx.thresholds:
            if ctx.thresholds.gateway_thresholds:
                # Gateway loss should be low (clean local connection)
                gw_loss_limit = ctx.thresholds.gateway_thresholds.packet_loss_warning_pct
            if ctx.thresholds.external_thresholds:
                ext_loss_threshold = ctx.thresholds.external_thresholds.packet_loss_warning_pct
                ext_jitter_threshold = ctx.thresholds.external_thresholds.jitter_warning_ms
                min_degraded = ctx.thresholds.external_thresholds.min_degraded_targets
            if ctx.thresholds.sample_requirements:
                min_samples = ctx.thresholds.sample_requirements.min_external_samples

        # 1. Condition 1: Gateway probe loss is low and stable
        gw_target = ctx.gateway_target
        if gw_target is None:
            return None

        gw_loss_sum = ctx.gateway_loss_summary
        gw_obs = ctx.gateway_observations
        if gw_loss_sum is not None:
            if gw_loss_sum.value_numeric is None or gw_loss_sum.value_numeric > gw_loss_limit:
                return None
        elif gw_obs:
            succ = sum(1 for o in gw_obs if o.status == MeasurementStatus.SUCCESS)
            total = len(gw_obs)
            if total == 0 or ((total - succ) / total * 100.0) > gw_loss_limit:
                return None
        else:
            return None

        # 2. Condition 2: Minimum of TWO independent external targets probed with N >= min_samples each
        ext_targets = ctx.external_targets
        if len(ext_targets) < 2:
            return None

        degraded_targets: List[str] = []
        healthy_targets: List[str] = []
        supporting_summaries: List[Summary] = []

        for ext in ext_targets:
            loss_sum = ctx.get_summary(ext.target_id, "packet_loss", "loss_percentage")
            jitter_sum = ctx.get_summary(ext.target_id, "delay_variation", "macd_jitter")
            ext_obs = ctx.get_observations_for_target(ext.target_id)

            attempts = 0
            loss_val: Optional[float] = None
            jitter_val: Optional[float] = None

            if loss_sum is not None:
                attempts = loss_sum.sample_count_total
                loss_val = loss_sum.value_numeric
            elif ext_obs:
                attempts = len(ext_obs)
                succ = sum(1 for o in ext_obs if o.status == MeasurementStatus.SUCCESS)
                loss_val = ((attempts - succ) / attempts) * 100.0 if attempts > 0 else None

            if jitter_sum is not None:
                jitter_val = jitter_sum.value_numeric

            if attempts < min_samples:
                # Target does not meet minimum sample requirements
                continue

            # Check if degraded: loss > 15% OR jitter > 30ms
            is_degraded = False
            if loss_val is not None and loss_val > ext_loss_threshold:
                is_degraded = True
            if jitter_val is not None and jitter_val > ext_jitter_threshold:
                is_degraded = True

            if is_degraded:
                degraded_targets.append(ext.target_id)
                if loss_sum:
                    supporting_summaries.append(loss_sum)
                if jitter_sum:
                    supporting_summaries.append(jitter_sum)
            else:
                healthy_targets.append(ext.target_id)

        # CRITICAL GUARDRAIL:
        # A failure on a single external target must NEVER trigger R-LOSS-01.
        # Single target degradation must be handled as Inconclusive / Target Disagreement.
        if len(degraded_targets) < min_degraded:
            return None

        plain_summary = (
            "The local connection to your home router is clean, but multiple independent internet "
            "destinations are experiencing repeated packet loss or severe delay variation. "
            "This indicates degradation beyond the local gateway, likely in the access line, "
            "fiber/cable modem, or ISP transit network."
        )
        limitations = (
            "Laptop-only measurements cannot view ISP internal routing tables, fiber optical "
            "power levels, or radio signal-to-noise ratios. A degraded external path does not "
            "definitively prove ISP infrastructure failure without technician line diagnostics."
        )
        recommended_action = (
            "Generate a TicNet Technician Report and provide the timestamped evidence log "
            "to your ISP technical support."
        )

        strength = EvidenceStrength.STRONG if len(degraded_targets) >= 3 else EvidenceStrength.MODERATE

        finding = self.create_finding(
            run_id=ctx.run_id,
            plain_summary=plain_summary,
            limitations=limitations,
            recommended_action=recommended_action,
            status=FindingStatus.SUPPORTED,
            evidence_strength=strength,
            priority=FindingPriority.WARNING,
            category=FindingCategory.EXTERNAL_PATH,
        )

        evidence: List[FindingEvidence] = []
        if gw_loss_sum:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    summary_id=gw_loss_sum.summary_id,
                )
            )

        for s in supporting_summaries:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    summary_id=s.summary_id,
                )
            )

        # Link supporting observations for degraded targets
        for deg_id in degraded_targets:
            for obs in ctx.get_observations_for_target(deg_id):
                evidence.append(
                    self.create_evidence_link(
                        finding_id=finding.finding_id,
                        relationship_type=EvidenceRelationship.SUPPORTING,
                        observation_id=obs.observation_id,
                    )
                )

        return RuleEvaluationResult(finding=finding, evidence=evidence)
