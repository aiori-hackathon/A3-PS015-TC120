"""R-INCONCLUSIVE-01: Insufficient, Ambiguous, or Conflicting Evidence Diagnostic Rule."""

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


class InconclusiveRule(BaseDiagnosticRule):
    """Rule R-INCONCLUSIVE-01: Detects ambiguous, conflicting, or insufficient measurement evidence."""

    @property
    def rule_id(self) -> str:
        return "R-INCONCLUSIVE-01"

    @property
    def rule_version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> FindingCategory:
        return FindingCategory.INCONCLUSIVE

    @property
    def priority(self) -> FindingPriority:
        return FindingPriority.INFO

    def evaluate(self, ctx: DiagnosisContext) -> Optional[RuleEvaluationResult]:
        conflicting_reasons: List[str] = []
        conflicting_summaries: List[Summary] = []
        conflicting_obs_ids: List[str] = []

        ext_loss_threshold = 15.0
        ext_jitter_threshold = 30.0
        min_samples = 5

        if ctx.thresholds:
            if ctx.thresholds.external_thresholds:
                ext_loss_threshold = ctx.thresholds.external_thresholds.packet_loss_warning_pct
                ext_jitter_threshold = ctx.thresholds.external_thresholds.jitter_warning_ms
            if ctx.thresholds.sample_requirements:
                min_samples = ctx.thresholds.sample_requirements.min_gateway_samples

        # Check 1: Single-target external failure guardrail (Target Disagreement)
        # If external targets were probed, and one degrades while another succeeds cleanly:
        ext_targets = ctx.external_targets
        if len(ext_targets) >= 2:
            degraded_targets: List[str] = []
            healthy_targets: List[str] = []

            for ext in ext_targets:
                loss_sum = ctx.get_summary(ext.target_id, "packet_loss", "loss_percentage")
                jitter_sum = ctx.get_summary(ext.target_id, "delay_variation", "macd_jitter")
                ext_obs = ctx.get_observations_for_target(ext.target_id)

                loss_val: Optional[float] = None
                jitter_val: Optional[float] = None

                if loss_sum is not None:
                    loss_val = loss_sum.value_numeric
                elif ext_obs:
                    total = len(ext_obs)
                    succ = sum(1 for o in ext_obs if o.status == MeasurementStatus.SUCCESS)
                    loss_val = ((total - succ) / total) * 100.0 if total > 0 else None

                if jitter_sum is not None:
                    jitter_val = jitter_sum.value_numeric

                is_degraded = False
                if loss_val is not None and loss_val > ext_loss_threshold:
                    is_degraded = True
                if jitter_val is not None and jitter_val > ext_jitter_threshold:
                    is_degraded = True

                if is_degraded:
                    degraded_targets.append(ext.target_id)
                    if loss_sum:
                        conflicting_summaries.append(loss_sum)
                else:
                    healthy_targets.append(ext.target_id)
                    if loss_sum:
                        conflicting_summaries.append(loss_sum)

            # Target Disagreement: exactly 1 degraded target and at least 1 healthy target
            if len(degraded_targets) == 1 and len(healthy_targets) >= 1:
                conflicting_reasons.append(
                    f"Target disagreement: Target {degraded_targets[0]} exhibited packet loss or latency degradation, "
                    f"while Target {healthy_targets[0]} remained healthy. A single remote server issue must not be "
                    "attributed to general internet or ISP failure."
                )

        # Check 2: Incomplete probe sequence (< 5 attempts due to early cancellation or interruption)
        gw_obs = ctx.gateway_observations
        gw_target = ctx.gateway_target
        if gw_target and gw_obs and len(gw_obs) < min_samples:
            conflicting_reasons.append(
                f"Incomplete probe sequence: Only {len(gw_obs)} gateway probes were recorded "
                f"(minimum required: {min_samples}). Cannot reach a definitive conclusion."
            )
            for o in gw_obs:
                conflicting_obs_ids.append(o.observation_id)

        # Check 3: High probe loss but successful TCP throughput
        gw_loss = ctx.gateway_loss_summary
        tp_target = ctx.throughput_target
        if gw_loss and gw_loss.value_numeric is not None and gw_loss.value_numeric >= 50.0:
            if tp_target:
                tp_obs = ctx.get_observations_for_target(tp_target.target_id)
                succ_tp = [o for o in tp_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric and o.value_numeric > 5.0]
                if succ_tp:
                    conflicting_reasons.append(
                        "Contradictory evidence: ICMP probe loss was high, but TCP throughput completed successfully "
                        "at normal speeds, indicating potential ICMP rate-limiting or firewall policy rather than network outage."
                    )
                    conflicting_summaries.append(gw_loss)
                    conflicting_obs_ids.extend([o.observation_id for o in succ_tp])

        # Check 4: Blocked probe methods (ICMP blocked at both gateway and external destinations)
        all_obs = ctx.observations
        if all_obs and all(o.status == MeasurementStatus.BLOCKED for o in all_obs):
            conflicting_reasons.append(
                "Blocked probing: Network measurements were actively blocked by local firewall or permissions."
            )
            conflicting_obs_ids.extend([o.observation_id for o in all_obs[:5]])

        if not conflicting_reasons:
            return None

        reason_str = " ".join(conflicting_reasons)
        plain_summary = (
            f"The diagnostic results are inconclusive: {reason_str} "
            "The available measurement evidence is either conflicting, incomplete, or insufficient "
            "to isolate a specific network failure domain safely."
        )
        limitations = (
            "Laptop-perspective measurements could not establish a consistent pattern across multiple "
            "test destinations or probe methods."
        )
        recommended_action = (
            "Run an extended diagnostic session with additional external targets or execute a direct "
            "Ethernet-connected diagnostic run."
        )

        finding = self.create_finding(
            run_id=ctx.run_id,
            plain_summary=plain_summary,
            limitations=limitations,
            recommended_action=recommended_action,
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.INCONCLUSIVE,
            priority=FindingPriority.INFO,
            category=FindingCategory.INCONCLUSIVE,
        )

        evidence: List[FindingEvidence] = []
        for s in conflicting_summaries:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.CONFLICTING,
                    summary_id=s.summary_id,
                )
            )

        for obs_id in set(conflicting_obs_ids):
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.CONFLICTING,
                    observation_id=obs_id,
                )
            )

        return RuleEvaluationResult(finding=finding, evidence=evidence)
