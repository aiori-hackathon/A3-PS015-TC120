"""R-LOCAL-01: Local Wi-Fi or LAN Degradation Diagnostic Rule."""

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


class LocalNetworkDegradationRule(BaseDiagnosticRule):
    """Rule R-LOCAL-01: Detects local Wi-Fi or LAN degradation (loss, jitter, RTT spikes)."""

    @property
    def rule_id(self) -> str:
        return "R-LOCAL-01"

    @property
    def rule_version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> FindingCategory:
        return FindingCategory.LOCAL_NETWORK

    @property
    def priority(self) -> FindingPriority:
        return FindingPriority.WARNING

    def evaluate(self, ctx: DiagnosisContext) -> Optional[RuleEvaluationResult]:
        gw_target = ctx.gateway_target
        if gw_target is None or not gw_target.ip_address:
            return None

        loss_threshold = 10.0
        jitter_threshold = 25.0
        rtt_spike_threshold = 100.0
        min_samples = 5

        if ctx.thresholds:
            if ctx.thresholds.gateway_thresholds:
                loss_threshold = ctx.thresholds.gateway_thresholds.packet_loss_warning_pct
                jitter_threshold = ctx.thresholds.gateway_thresholds.jitter_warning_ms
                rtt_spike_threshold = ctx.thresholds.gateway_thresholds.rtt_spike_warning_ms
            if ctx.thresholds.sample_requirements:
                min_samples = ctx.thresholds.sample_requirements.min_gateway_samples

        gw_obs = ctx.gateway_observations
        loss_summary = ctx.gateway_loss_summary
        jitter_summary = ctx.gateway_jitter_summary
        median_summary = ctx.gateway_median_summary

        total_attempts = 0
        loss_val: Optional[float] = None
        jitter_val: Optional[float] = None
        median_rtt_val: Optional[float] = None

        if loss_summary is not None:
            total_attempts = loss_summary.sample_count_total
            loss_val = loss_summary.value_numeric
        elif gw_obs:
            total_attempts = len(gw_obs)
            succ = sum(1 for o in gw_obs if o.status == MeasurementStatus.SUCCESS)
            loss_val = ((total_attempts - succ) / total_attempts) * 100.0 if total_attempts > 0 else None

        if jitter_summary is not None:
            jitter_val = jitter_summary.value_numeric

        if median_summary is not None:
            median_rtt_val = median_summary.value_numeric

        if total_attempts < min_samples:
            return None

        # Exclusion: 100% loss is handled by R-GW-01
        if loss_val is not None and loss_val >= 100.0:
            return None

        # Check max RTT spike from observations
        max_rtt = 0.0
        for obs in gw_obs:
            if obs.status == MeasurementStatus.SUCCESS and obs.value_numeric is not None:
                if obs.value_numeric > max_rtt:
                    max_rtt = obs.value_numeric

        # Trigger conditions:
        # 1. Gateway loss > loss_threshold (10%) and < 100%
        # 2. OR Gateway jitter > jitter_threshold (25ms)
        # 3. OR Gateway RTT spikes > rtt_spike_threshold (100ms) or median > 100ms
        has_loss_degradation = loss_val is not None and loss_val > loss_threshold
        has_jitter_degradation = jitter_val is not None and jitter_val > jitter_threshold
        has_rtt_spike = max_rtt > rtt_spike_threshold or (median_rtt_val is not None and median_rtt_val > rtt_spike_threshold)

        if not (has_loss_degradation or has_jitter_degradation or has_rtt_spike):
            return None

        plain_summary = (
            "High latency spikes, packet loss, or delay variation were detected between your "
            "computer and the local home router. This typically indicates Wi-Fi radio interference, "
            "weak wireless signal, channel congestion, or a faulty local Ethernet patch cable."
        )
        limitations = (
            "Cannot isolate whether interference is caused by physical obstacles, "
            "microwave/Bluetooth devices, or neighboring Wi-Fi access points without RF spectrum telemetry."
        )
        recommended_action = (
            "1. Move closer to the Wi-Fi access point. "
            "2. Switch from 2.4 GHz to 5 GHz / 6 GHz Wi-Fi. "
            "3. Connect via a known-good Cat5e/Cat6 Ethernet cable to test if degradation disappears."
        )

        strength = EvidenceStrength.STRONG if (has_loss_degradation and has_jitter_degradation) else EvidenceStrength.MODERATE

        finding = self.create_finding(
            run_id=ctx.run_id,
            plain_summary=plain_summary,
            limitations=limitations,
            recommended_action=recommended_action,
            status=FindingStatus.SUPPORTED,
            evidence_strength=strength,
            priority=FindingPriority.WARNING,
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
        if jitter_summary is not None:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    summary_id=jitter_summary.summary_id,
                )
            )
        if median_summary is not None:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    summary_id=median_summary.summary_id,
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
