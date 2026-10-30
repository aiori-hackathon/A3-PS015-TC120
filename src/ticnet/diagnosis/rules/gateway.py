"""R-GW-01: Local Gateway Unreachable Diagnostic Rule."""

from __future__ import annotations

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


class GatewayUnreachableRule(BaseDiagnosticRule):
    """Rule R-GW-01: Detects when the local default gateway is completely unreachable."""

    @property
    def rule_id(self) -> str:
        return "R-GW-01"

    @property
    def rule_version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> FindingCategory:
        return FindingCategory.LOCAL_NETWORK

    @property
    def priority(self) -> FindingPriority:
        return FindingPriority.CRITICAL

    def evaluate(self, ctx: DiagnosisContext) -> Optional[RuleEvaluationResult]:
        gw_target = ctx.gateway_target
        # Exclusion 1: Gateway discovery failed, blocked, or missing
        if gw_target is None or not gw_target.ip_address:
            return None

        # Check gateway observations and summary
        gw_obs = ctx.gateway_observations
        loss_summary = ctx.gateway_loss_summary

        min_samples = 5
        if ctx.thresholds and ctx.thresholds.sample_requirements:
            min_samples = ctx.thresholds.sample_requirements.min_gateway_samples

        total_attempts = 0
        loss_pct: Optional[float] = None
        successful_count = 0

        gw_probe_obs = [o for o in gw_obs if o.metric_name in ("round_trip_time", "rtt")]
        if loss_summary is not None:
            total_attempts = loss_summary.sample_count_total
            loss_pct = loss_summary.value_numeric
            successful_count = sum(1 for o in gw_probe_obs if o.status == MeasurementStatus.SUCCESS)
        elif gw_probe_obs:
            total_attempts = len(gw_probe_obs)
            successful_count = sum(1 for o in gw_probe_obs if o.status == MeasurementStatus.SUCCESS)
            loss_pct = 100.0 if successful_count == 0 else ((total_attempts - successful_count) / total_attempts) * 100.0

        # Trigger condition check:
        # 1. Attempts >= min_samples
        # 2. Loss is 100% (successful == 0)
        if total_attempts < min_samples:
            return None

        if loss_pct is None or loss_pct < 100.0 or successful_count > 0:
            return None

        plain_summary = (
            "The computer cannot communicate with the local home router or default gateway. "
            "Because the first network hop is unreachable, all external internet connectivity is blocked."
        )
        limitations = (
            "A failure to respond to ICMP Echo requests can occasionally occur if the router has "
            "ICMP ping responses disabled in its security settings. However, local ARP or TCP communication "
            "may still be affected."
        )
        recommended_action = (
            "1. Check physical Ethernet cable or Wi-Fi connection. "
            "2. Verify Wi-Fi network selection. "
            "3. Restart the home router and verify power LEDs."
        )

        finding = self.create_finding(
            run_id=ctx.run_id,
            plain_summary=plain_summary,
            limitations=limitations,
            recommended_action=recommended_action,
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            priority=FindingPriority.CRITICAL,
            category=FindingCategory.LOCAL_NETWORK,
        )

        evidence: List[FindingEvidence] = []
        if loss_summary is not None:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    summary_id=loss_summary.summary_id,
                )
            )

        for obs in gw_obs:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    observation_id=obs.observation_id,
                )
            )

        return RuleEvaluationResult(finding=finding, evidence=evidence)
