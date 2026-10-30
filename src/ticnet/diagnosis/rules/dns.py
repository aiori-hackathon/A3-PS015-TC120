"""R-DNS-01: DNS Resolution Subsystem Failure Diagnostic Rule."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from ticnet.diagnosis.context import DiagnosisContext
from ticnet.diagnosis.rules.base import BaseDiagnosticRule, RuleEvaluationResult
from ticnet.models.entities import FindingEvidence, Observation, Summary
from ticnet.models.enums import (
    EvidenceRelationship,
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
)


class DNSSubsystemFailureRule(BaseDiagnosticRule):
    """Rule R-DNS-01: Detects failure in the DNS resolution subsystem while IP reachability is intact."""

    @property
    def rule_id(self) -> str:
        return "R-DNS-01"

    @property
    def rule_version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> FindingCategory:
        return FindingCategory.DNS_SUBSYSTEM

    @property
    def priority(self) -> FindingPriority:
        return FindingPriority.CRITICAL

    def evaluate(self, ctx: DiagnosisContext) -> Optional[RuleEvaluationResult]:
        gw_threshold = 10.0
        dns_threshold = 80.0
        if ctx.thresholds:
            if ctx.thresholds.gateway_thresholds:
                gw_threshold = ctx.thresholds.gateway_thresholds.packet_loss_warning_pct
            if ctx.thresholds.dns_thresholds:
                dns_threshold = ctx.thresholds.dns_thresholds.failure_rate_warning_pct

        # 1. Condition 1: Gateway reachability is healthy (loss <= 10%)
        gw_target = ctx.gateway_target
        if gw_target is None:
            return None

        gw_loss_sum = ctx.gateway_loss_summary
        gw_obs = ctx.gateway_observations
        if gw_loss_sum is not None:
            if gw_loss_sum.value_numeric is None or gw_loss_sum.value_numeric > gw_threshold:
                return None
        elif gw_obs:
            succ = sum(1 for o in gw_obs if o.status == MeasurementStatus.SUCCESS)
            total = len(gw_obs)
            if total == 0 or ((total - succ) / total * 100.0) > gw_threshold:
                return None
        else:
            return None

        # 2. Condition 2: At least two independent external targets are reachable via IP probing
        ext_targets = ctx.external_targets
        reachable_ext_count = 0
        ext_summaries: List[Summary] = []
        ext_all_down = True if ext_targets else False

        for ext in ext_targets:
            ext_loss = ctx.get_summary(ext.target_id, "packet_loss", "loss_percentage")
            if ext_loss is not None:
                ext_summaries.append(ext_loss)
                if ext_loss.value_numeric is not None and ext_loss.value_numeric < 100.0:
                    reachable_ext_count += 1
                    ext_all_down = False
            else:
                obs = ctx.get_observations_for_target(ext.target_id)
                succ = sum(1 for o in obs if o.status == MeasurementStatus.SUCCESS)
                if succ > 0:
                    reachable_ext_count += 1
                    ext_all_down = False

        # Exclusion 1: If external IP reachability is 100% down, this is external transit failure, not DNS
        if ext_targets and ext_all_down:
            return None

        if reachable_ext_count < 2:
            return None

        # 3. Condition 3: Configured DNS queries to primary resolver time out or return SERVFAIL >= 80%
        dns_obs = ctx.dns_observations
        if not dns_obs:
            return None

        total_dns = len(dns_obs)
        failed_dns: List[Observation] = []
        domain_failures: Dict[str, int] = {}
        domain_totals: Dict[str, int] = {}
        resolvers_tested: Set[str] = set()

        for obs in dns_obs:
            is_failure = False
            domain_name = "unknown"
            if obs.target_id:
                resolvers_tested.add(obs.target_id)

            if obs.method_metadata:
                try:
                    meta = json.loads(obs.method_metadata)
                    domain_name = meta.get("domain", "unknown")
                    rcode = meta.get("rcode")
                    rcode_name = meta.get("rcode_name")
                    res_status = meta.get("resolution_status")

                    if obs.status in (MeasurementStatus.TIMEOUT, MeasurementStatus.FAILED):
                        is_failure = True
                    elif rcode == 2 or rcode_name == "SERVFAIL" or res_status == "SERVFAIL":
                        is_failure = True
                    elif obs.status != MeasurementStatus.SUCCESS:
                        is_failure = True
                except Exception:
                    if obs.status != MeasurementStatus.SUCCESS:
                        is_failure = True
            else:
                if obs.status != MeasurementStatus.SUCCESS:
                    is_failure = True

            domain_totals[domain_name] = domain_totals.get(domain_name, 0) + 1
            if is_failure:
                failed_dns.append(obs)
                domain_failures[domain_name] = domain_failures.get(domain_name, 0) + 1

        failure_rate = (len(failed_dns) / total_dns) * 100.0
        if failure_rate < dns_threshold:
            return None

        # Exclusion 2: If only a single domain name fails while other domains resolve,
        # it is a target domain misconfiguration, not a DNS subsystem failure.
        if len(domain_totals) > 1:
            failed_domains = [d for d, count in domain_failures.items() if count == domain_totals.get(d, 0)]
            if len(failed_domains) == 1 and len(domain_totals) > 1:
                # Only 1 domain failed out of multiple domains tested
                return None

        plain_summary = (
            "The internet connection appears capable of transmitting data, but your configured "
            "DNS server is failing to translate website names into IP addresses. Browsers will "
            "report 'Server IP address could not be found'."
        )
        limitations = (
            "Does not distinguish between local router DNS proxy failure and upstream ISP "
            "resolver outage unless secondary public resolvers were tested."
        )
        recommended_action = (
            "Configure your workstation or router to use alternative public DNS resolvers "
            "(such as 1.1.1.1 or 8.8.8.8) to verify if name resolution recovers."
        )

        strength = EvidenceStrength.STRONG if len(resolvers_tested) > 1 and len(domain_totals) > 1 else EvidenceStrength.MODERATE

        finding = self.create_finding(
            run_id=ctx.run_id,
            plain_summary=plain_summary,
            limitations=limitations,
            recommended_action=recommended_action,
            status=FindingStatus.SUPPORTED,
            evidence_strength=strength,
            priority=FindingPriority.CRITICAL,
            category=FindingCategory.DNS_SUBSYSTEM,
        )

        evidence: List[FindingEvidence] = []
        for f_obs in failed_dns:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    observation_id=f_obs.observation_id,
                )
            )

        if gw_loss_sum:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    summary_id=gw_loss_sum.summary_id,
                )
            )

        for s in ext_summaries:
            evidence.append(
                self.create_evidence_link(
                    finding_id=finding.finding_id,
                    relationship_type=EvidenceRelationship.SUPPORTING,
                    summary_id=s.summary_id,
                )
            )

        return RuleEvaluationResult(finding=finding, evidence=evidence)
