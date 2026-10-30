"""Recommendation Engine for explainable network diagnosis.

Translates diagnostic findings into actionable, prioritized, evidence-linked
recommendations for both non-technical home users and ISP technicians.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ticnet.models.entities import Finding
from ticnet.models.enums import FindingCategory, FindingPriority


@dataclass(frozen=True)
class ActionItem:
    """A single concrete diagnostic or remediation action item."""

    step_order: int
    title: str
    description: str
    target_audience: str  # "USER" or "TECHNICIAN"
    rationale: str


@dataclass(frozen=True)
class FindingRecommendation:
    """Structured recommendation bundle associated with a specific finding."""

    finding_id: str
    rule_id: str
    category: FindingCategory
    priority: FindingPriority
    summary: str
    limitations: str
    user_actions: List[ActionItem] = field(default_factory=list)
    technician_actions: List[ActionItem] = field(default_factory=list)


class RecommendationEngine:
    """Generates prioritized, evidence-linked action plans from diagnostic findings."""

    def generate_recommendations(self, findings: List[Finding]) -> List[FindingRecommendation]:
        """Generate structured recommendations for each diagnostic finding."""
        recommendations: List[FindingRecommendation] = []
        for finding in findings:
            rec = self._recommend_for_finding(finding)
            recommendations.append(rec)
        return recommendations

    def _recommend_for_finding(self, finding: Finding) -> FindingRecommendation:
        rule_id = finding.rule_id

        if rule_id == "R-GW-01":
            user_actions = [
                ActionItem(
                    step_order=1,
                    title="Check Physical or Wi-Fi Connection",
                    description="Ensure your Ethernet cable is firmly seated at both ends, or confirm you are connected to your intended home Wi-Fi SSID.",
                    target_audience="USER",
                    rationale="Physical link loss or disconnection prevents communication with the default gateway.",
                ),
                ActionItem(
                    step_order=2,
                    title="Restart Home Gateway / Router",
                    description="Power cycle your router by unplugging its power cord for 30 seconds and plugging it back in.",
                    target_audience="USER",
                    rationale="Clears local gateway software stalls or DHCP lease deadlocks.",
                ),
            ]
            technician_actions = [
                ActionItem(
                    step_order=1,
                    title="Verify Gateway ARP and Interface Link",
                    description="Inspect workstation ARP table and router LAN port link speed/duplex negotiation.",
                    target_audience="TECHNICIAN",
                    rationale="Isolates Layer 1 physical link vs Layer 2 ARP failure.",
                ),
            ]
        elif rule_id == "R-DNS-01":
            user_actions = [
                ActionItem(
                    step_order=1,
                    title="Configure Alternative Public DNS",
                    description="Set network adapter DNS to known reliable public resolvers (e.g. 1.1.1.1 or 8.8.8.8).",
                    target_audience="USER",
                    rationale="Bypasses broken local router DNS proxy or failing ISP resolver.",
                ),
                ActionItem(
                    step_order=2,
                    title="Flush Local DNS Cache",
                    description="Open command line and run 'ipconfig /flushdns' (Windows) or restart network service.",
                    target_audience="USER",
                    rationale="Purges stale or corrupted negative resolution entries from local cache.",
                ),
            ]
            technician_actions = [
                ActionItem(
                    step_order=1,
                    title="Audit Upstream Resolver UDP/53 Reachability",
                    description="Verify whether UDP/53 outbound traffic is blocked or intercepted by upstream firewall.",
                    target_audience="TECHNICIAN",
                    rationale="Detects ISP port filtering or transparent DNS interception proxies.",
                ),
            ]
        elif rule_id == "R-LOSS-01":
            user_actions = [
                ActionItem(
                    step_order=1,
                    title="Export TicNet Technician Report",
                    description="Generate and export the timestamped TicNet Markdown Technician Report.",
                    target_audience="USER",
                    rationale="Provides structured, objective evidence of external WAN degradation to show your ISP.",
                ),
                ActionItem(
                    step_order=2,
                    title="Contact ISP Support with Evidence",
                    description="Open an ISP support ticket citing multi-target external packet loss while local gateway is healthy.",
                    target_audience="USER",
                    rationale="Prevents Tier 1 ISP support from incorrectly blaming home Wi-Fi or devices.",
                ),
            ]
            technician_actions = [
                ActionItem(
                    step_order=1,
                    title="Check Access Line and Upstream Optical/RF Levels",
                    description="Inspect optical receive power (dBm) on GPON/FTTH ONT or SNR/power levels on DOCSIS modem.",
                    target_audience="TECHNICIAN",
                    rationale="Correlates measured external packet loss with physical access-line telemetry.",
                ),
            ]
        elif rule_id == "R-LOCAL-01":
            user_actions = [
                ActionItem(
                    step_order=1,
                    title="Move Closer to Wi-Fi Access Point",
                    description="Relocate closer to the router or remove line-of-sight obstacles to rule out radio signal attenuation.",
                    target_audience="USER",
                    rationale="Weak RF signal causes frame retries, leading to latency jitter and packet drops.",
                ),
                ActionItem(
                    step_order=2,
                    title="Switch to 5 GHz or 6 GHz Wi-Fi Band",
                    description="Connect to the 5 GHz or 6 GHz Wi-Fi band instead of crowded 2.4 GHz channels.",
                    target_audience="USER",
                    rationale="5 GHz and 6 GHz provide wider channels and much lower interference from neighboring networks.",
                ),
                ActionItem(
                    step_order=3,
                    title="Test with Wired Ethernet Cable",
                    description="Plug in a Cat5e or Cat6 Ethernet cable directly into the router LAN port.",
                    target_audience="USER",
                    rationale="Immediately isolates wireless radio issues from local router hardware defects.",
                ),
            ]
            technician_actions = [
                ActionItem(
                    step_order=1,
                    title="Perform 2.4/5 GHz RF Spectrum and Channel Survey",
                    description="Measure Wi-Fi RSSI (dBm), channel utilization (%), and adjacent-channel co-channel interference.",
                    target_audience="TECHNICIAN",
                    rationale="Quantifies environmental wireless interference and channel saturation.",
                ),
            ]
        elif rule_id == "R-LOAD-01":
            user_actions = [
                ActionItem(
                    step_order=1,
                    title="Enable Smart Queue Management (SQM)",
                    description="Access router administration settings and enable Smart Queue Management (CAKE or FQ-CoDel).",
                    target_audience="USER",
                    rationale="Eliminates bufferbloat by actively managing buffer queues during heavy downloads.",
                ),
                ActionItem(
                    step_order=2,
                    title="Set QoS Bandwidth Ceilings",
                    description="If SQM is unavailable, set router bandwidth limits to 90-95% of your measured speed.",
                    target_audience="USER",
                    rationale="Prevents modem buffers from filling up during peak upload/download activity.",
                ),
            ]
            technician_actions = [
                ActionItem(
                    step_order=1,
                    title="Evaluate CPE and CMTS/OLT Buffer Configurations",
                    description="Review upstream buffer configurations on CPE or customer-premises modem.",
                    target_audience="TECHNICIAN",
                    rationale="Determines whether buffer backlog occurs in customer router or ISP access equipment.",
                ),
            ]
        elif rule_id == "R-INCONCLUSIVE-01":
            user_actions = [
                ActionItem(
                    step_order=1,
                    title="Run an Extended Diagnostic Run",
                    description="Execute a second diagnostic run after verifying that other devices are not heavily utilizing bandwidth.",
                    target_audience="USER",
                    rationale="Additional measurement rounds help disambiguate transient anomalies from persistent faults.",
                ),
                ActionItem(
                    step_order=2,
                    title="Test via Direct Wired Ethernet",
                    description="Connect via Ethernet cable and run the diagnosis again to remove wireless variables.",
                    target_audience="USER",
                    rationale="Removes wireless medium instability to clarify ambiguous multi-target results.",
                ),
            ]
            technician_actions = [
                ActionItem(
                    step_order=1,
                    title="Perform Multi-Destination Traceroute and MTR",
                    description="Run bidirectional MTR probes toward divergent external transit providers.",
                    target_audience="TECHNICIAN",
                    rationale="Pinpoints specific AS path or transit peering points experiencing degradation.",
                ),
            ]
        else:
            user_actions = [
                ActionItem(
                    step_order=1,
                    title="Review Diagnostic Findings",
                    description=finding.recommended_action,
                    target_audience="USER",
                    rationale="Action derived from rule evaluation.",
                ),
            ]
            technician_actions = []

        return FindingRecommendation(
            finding_id=finding.finding_id,
            rule_id=finding.rule_id,
            category=finding.category,
            priority=finding.priority,
            summary=finding.plain_summary,
            limitations=finding.limitations,
            user_actions=user_actions,
            technician_actions=technician_actions,
        )
