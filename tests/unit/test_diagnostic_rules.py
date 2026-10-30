"""Unit tests for Phase 8 Explainable Diagnostic Rule Engine and Recommendations.

Verifies:
- TC-UNIT-04: R-GW-01 (Local Gateway Unreachable)
- TC-UNIT-05: R-DNS-01 (DNS Resolution Subsystem Failure)
- TC-UNIT-06: R-LOSS-01 (Possible External Path Degradation & Single-Target Guardrail)
- TC-UNIT-07: R-LOCAL-01 (Local Wi-Fi or LAN Degradation)
- TC-UNIT-08: R-LOAD-01 (Bufferbloat / Latency Increase Under Load)
- TC-UNIT-09: R-INCONCLUSIVE-01 (Insufficient, Ambiguous, or Conflicting Evidence)
- Actionable Recommendations Generator (RecommendationEngine)
- Diagnosis Engine priority ordering and SQLite persistence
"""

from __future__ import annotations

import json
import pytest

from ticnet.configuration.validator import (
    DNSThresholdsConfig,
    ExternalThresholdsConfig,
    GatewayThresholdsConfig,
    LoadedThresholdsConfig,
    RetentionConfig,
    SampleRequirementsConfig,
    ThresholdsConfig,
)
from ticnet.diagnosis.context import DiagnosisContext
from ticnet.diagnosis.engine import DiagnosisEngine
from ticnet.diagnosis.recommender import RecommendationEngine
from ticnet.diagnosis.rules.bufferbloat import BufferbloatRule
from ticnet.diagnosis.rules.dns import DNSSubsystemFailureRule
from ticnet.diagnosis.rules.gateway import GatewayUnreachableRule
from ticnet.diagnosis.rules.inconclusive import InconclusiveRule
from ticnet.diagnosis.rules.local import LocalNetworkDegradationRule
from ticnet.diagnosis.rules.loss import ExternalPathDegradationRule
from ticnet.models.entities import Finding, FindingEvidence, Observation, Summary, Target, new_id, now_utc_iso
from ticnet.models.enums import (
    EvidenceRelationship,
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
    TargetType,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository


@pytest.fixture
def default_thresholds() -> ThresholdsConfig:
    """Return standard test thresholds matching configuration defaults."""
    return ThresholdsConfig(
        schema_version="1.0.0",
        sample_requirements=SampleRequirementsConfig(
            min_gateway_samples=5,
            min_external_samples=10,
            min_dns_samples=3,
            min_loaded_samples=5,
            min_samples_for_median=3,
            min_samples_for_p95=10,
        ),
        gateway_thresholds=GatewayThresholdsConfig(
            packet_loss_critical_pct=100.0,
            packet_loss_warning_pct=10.0,
            jitter_warning_ms=25.0,
            rtt_spike_warning_ms=100.0,
        ),
        external_thresholds=ExternalThresholdsConfig(
            packet_loss_warning_pct=15.0,
            jitter_warning_ms=30.0,
            min_degraded_targets=2,
        ),
        dns_thresholds=DNSThresholdsConfig(
            failure_rate_warning_pct=80.0,
            slow_lookup_ms=300.0,
        ),
        loaded_thresholds=LoadedThresholdsConfig(
            bufferbloat_rtt_delta_ms=50.0,
            bufferbloat_ratio=2.5,
        ),
        retention=RetentionConfig(
            max_historical_runs=100,
            max_retention_days=30,
            auto_purge_on_limit=True,
        ),
    )


@pytest.fixture
def unit_db(tmp_path):
    """In-memory SQLite database initialized with migrations."""
    db_file = tmp_path / "test_diag.db"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


def make_ping_obs(
    run_id: str,
    target_id: str,
    sample_index: int,
    status: MeasurementStatus,
    rtt_ms: float | None = None,
) -> Observation:
    """Helper to generate typed ping observations."""
    return Observation(
        observation_id=new_id(),
        run_id=run_id,
        target_id=target_id,
        sample_index=sample_index,
        timestamp_utc=now_utc_iso(),
        duration_ms=rtt_ms if rtt_ms is not None else 1000.0,
        metric_name="round_trip_time",
        value_numeric=rtt_ms if status == MeasurementStatus.SUCCESS else None,
        unit="ms",
        status=status,
    )


# ============================================================================
# TC-UNIT-04: R-GW-01 (Local Gateway Unreachable)
# ============================================================================
class TestTCUNIT04GatewayUnreachableRule:
    """Verifies TC-UNIT-04: Gateway unreachable rule trigger, exclusion, and evidence linking."""

    def test_tc_unit_04_trigger_on_100_percent_loss(self, default_thresholds):
        """Assert R-GW-01 triggers when gateway packet loss is 100% across >= 5 attempts."""
        run_id = new_id()
        gw_target = Target(
            target_id="gw-1",
            target_type=TargetType.GATEWAY,
            ip_address="192.168.1.1",
        )
        # 5 timed-out probes
        obs = [
            make_ping_obs(run_id, "gw-1", i + 1, MeasurementStatus.TIMEOUT, None)
            for i in range(5)
        ]
        loss_sum = Summary(
            summary_id=new_id(),
            run_id=run_id,
            target_id="gw-1",
            metric_name="packet_loss",
            statistic_name="loss_percentage",
            unit="%",
            sample_count_total=5,
            sample_count_valid=5,
            calculation_method="Standard Probe Ratio",
            calculation_version="1.0.0",
            value_numeric=100.0,
        )

        ctx = DiagnosisContext(
            run_id=run_id,
            observations=obs,
            summaries=[loss_sum],
            targets={"gw-1": gw_target},
            thresholds=default_thresholds,
        )

        rule = GatewayUnreachableRule()
        result = rule.evaluate(ctx)

        assert result is not None
        assert result.finding.rule_id == "R-GW-01"
        assert result.finding.priority == FindingPriority.CRITICAL
        assert result.finding.category == FindingCategory.LOCAL_NETWORK
        assert result.finding.status == FindingStatus.SUPPORTED
        assert result.finding.evidence_strength == EvidenceStrength.STRONG
        assert "cannot communicate with the local home router" in result.finding.plain_summary
        assert "ICMP" in result.finding.limitations
        assert "Restart the home router" in result.finding.recommended_action

        # Check evidence linking
        summary_links = [e for e in result.evidence if e.summary_id == loss_sum.summary_id]
        assert len(summary_links) == 1
        assert summary_links[0].relationship_type == EvidenceRelationship.SUPPORTING

        obs_links = [e for e in result.evidence if e.observation_id is not None]
        assert len(obs_links) == 5

    def test_tc_unit_04_non_trigger_on_clean_gateway(self, default_thresholds):
        """Assert R-GW-01 DOES NOT trigger when gateway loss is 0%."""
        run_id = new_id()
        gw_target = Target(target_id="gw-1", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        obs = [make_ping_obs(run_id, "gw-1", i + 1, MeasurementStatus.SUCCESS, 2.5) for i in range(5)]
        loss_sum = Summary(
            summary_id=new_id(),
            run_id=run_id,
            target_id="gw-1",
            metric_name="packet_loss",
            statistic_name="loss_percentage",
            unit="%",
            sample_count_total=5,
            sample_count_valid=5,
            calculation_method="Standard Probe Ratio",
            calculation_version="1.0.0",
            value_numeric=0.0,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=obs,
            summaries=[loss_sum],
            targets={"gw-1": gw_target},
            thresholds=default_thresholds,
        )
        assert GatewayUnreachableRule().evaluate(ctx) is None

    def test_tc_unit_04_exclusion_when_discovery_failed(self, default_thresholds):
        """Assert R-GW-01 DOES NOT trigger when gateway was not discovered (missing target)."""
        run_id = new_id()
        ctx = DiagnosisContext(run_id=run_id, observations=[], summaries=[], targets={}, thresholds=default_thresholds)
        assert GatewayUnreachableRule().evaluate(ctx) is None

    def test_tc_unit_04_exclusion_when_sample_count_insufficient(self, default_thresholds):
        """Assert R-GW-01 DOES NOT trigger when sample count < 5 even if all timed out."""
        run_id = new_id()
        gw_target = Target(target_id="gw-1", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        obs = [make_ping_obs(run_id, "gw-1", i + 1, MeasurementStatus.TIMEOUT, None) for i in range(3)]
        loss_sum = Summary(
            summary_id=new_id(),
            run_id=run_id,
            target_id="gw-1",
            metric_name="packet_loss",
            statistic_name="loss_percentage",
            unit="%",
            sample_count_total=3,
            sample_count_valid=3,
            calculation_method="Standard Probe Ratio",
            calculation_version="1.0.0",
            value_numeric=100.0,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=obs,
            summaries=[loss_sum],
            targets={"gw-1": gw_target},
            thresholds=default_thresholds,
        )
        assert GatewayUnreachableRule().evaluate(ctx) is None

    def test_tc_unit_04_non_trigger_on_partial_loss(self, default_thresholds):
        """Assert R-GW-01 DOES NOT trigger when gateway has partial loss (e.g. 20%)."""
        run_id = new_id()
        gw_target = Target(target_id="gw-1", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        obs = [
            make_ping_obs(run_id, "gw-1", 1, MeasurementStatus.TIMEOUT, None),
            make_ping_obs(run_id, "gw-1", 2, MeasurementStatus.SUCCESS, 3.0),
            make_ping_obs(run_id, "gw-1", 3, MeasurementStatus.SUCCESS, 2.8),
            make_ping_obs(run_id, "gw-1", 4, MeasurementStatus.SUCCESS, 3.1),
            make_ping_obs(run_id, "gw-1", 5, MeasurementStatus.SUCCESS, 2.9),
        ]
        loss_sum = Summary(
            summary_id=new_id(),
            run_id=run_id,
            target_id="gw-1",
            metric_name="packet_loss",
            statistic_name="loss_percentage",
            unit="%",
            sample_count_total=5,
            sample_count_valid=5,
            calculation_method="Standard Probe Ratio",
            calculation_version="1.0.0",
            value_numeric=20.0,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=obs,
            summaries=[loss_sum],
            targets={"gw-1": gw_target},
            thresholds=default_thresholds,
        )
        assert GatewayUnreachableRule().evaluate(ctx) is None


# ============================================================================
# TC-UNIT-05: R-DNS-01 (DNS Resolution Subsystem Failure)
# ============================================================================
class TestTCUNIT05DNSSubsystemFailureRule:
    """Verifies TC-UNIT-05: DNS failure rule trigger, exclusions, and multi-domain isolation."""

    def test_tc_unit_05_trigger_when_ip_healthy_but_dns_fails_ge_80_pct(self, default_thresholds):
        """Assert R-DNS-01 triggers when gateway and 2 external targets are reachable, but DNS fails >= 80%."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "ext1": Target(target_id="ext1", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "ext2": Target(target_id="ext2", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
            "dns1": Target(target_id="dns1", target_type=TargetType.DNS_RESOLVER, ip_address="192.168.1.1"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext1",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext2",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
        ]

        # 5 DNS queries: 4 fail (80%), 1 succeeds across 2 domains
        dns_obs = []
        for i, domain in enumerate(["cloudflare.com", "google.com", "apple.com", "wikipedia.org", "example.com"]):
            is_success = (i == 4)
            dns_obs.append(
                Observation(
                    observation_id=new_id(),
                    run_id=run_id,
                    target_id="dns1",
                    sample_index=i + 1,
                    timestamp_utc=now_utc_iso(),
                    duration_ms=25.0 if is_success else 2000.0,
                    metric_name="dns_resolution_time",
                    value_numeric=25.0 if is_success else None,
                    unit="ms",
                    status=MeasurementStatus.SUCCESS if is_success else MeasurementStatus.TIMEOUT,
                    method_metadata=json.dumps({
                        "resolver_ip": "192.168.1.1",
                        "domain": domain,
                        "resolution_status": "RESOLVED" if is_success else "TIMEOUT",
                    }),
                )
            )

        ctx = DiagnosisContext(
            run_id=run_id,
            observations=dns_obs,
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )

        rule = DNSSubsystemFailureRule()
        result = rule.evaluate(ctx)

        assert result is not None
        assert result.finding.rule_id == "R-DNS-01"
        assert result.finding.category == FindingCategory.DNS_SUBSYSTEM
        assert result.finding.priority == FindingPriority.CRITICAL
        assert "DNS server is failing to translate website names" in result.finding.plain_summary
        assert "alternative public DNS resolvers" in result.finding.recommended_action
        assert len(result.evidence) >= 4  # Contains failed observations

    def test_tc_unit_05_exclusion_when_entire_internet_is_down(self, default_thresholds):
        """Assert R-DNS-01 DOES NOT trigger when external IP reachability is 100% down (transit failure)."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "ext1": Target(target_id="ext1", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "ext2": Target(target_id="ext2", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
            "dns1": Target(target_id="dns1", target_type=TargetType.DNS_RESOLVER, ip_address="1.1.1.1"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            # External targets 100% down
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext1",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=100.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext2",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=100.0,
            ),
        ]
        dns_obs = [
            Observation(
                observation_id=new_id(), run_id=run_id, target_id="dns1", sample_index=1,
                timestamp_utc=now_utc_iso(), duration_ms=2000.0, metric_name="dns_resolution_time",
                value_numeric=None, unit="ms", status=MeasurementStatus.TIMEOUT,
            )
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=dns_obs,
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )
        assert DNSSubsystemFailureRule().evaluate(ctx) is None

    def test_tc_unit_05_exclusion_when_only_single_domain_fails(self, default_thresholds):
        """Assert R-DNS-01 DOES NOT trigger if only 1 domain fails (target domain misconfiguration)."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "ext1": Target(target_id="ext1", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "ext2": Target(target_id="ext2", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
            "dns1": Target(target_id="dns1", target_type=TargetType.DNS_RESOLVER, ip_address="192.168.1.1"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext1",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext2",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
        ]
        # Only bad-domain.com fails, good-domain.com succeeds
        dns_obs = [
            Observation(
                observation_id=new_id(), run_id=run_id, target_id="dns1", sample_index=1,
                timestamp_utc=now_utc_iso(), duration_ms=2000.0, metric_name="dns_resolution_time",
                value_numeric=None, unit="ms", status=MeasurementStatus.TIMEOUT,
                method_metadata=json.dumps({"domain": "bad-domain.com", "resolution_status": "TIMEOUT"}),
            ),
            Observation(
                observation_id=new_id(), run_id=run_id, target_id="dns1", sample_index=2,
                timestamp_utc=now_utc_iso(), duration_ms=20.0, metric_name="dns_resolution_time",
                value_numeric=20.0, unit="ms", status=MeasurementStatus.SUCCESS,
                method_metadata=json.dumps({"domain": "good-domain.com", "resolution_status": "RESOLVED"}),
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=dns_obs,
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )
        assert DNSSubsystemFailureRule().evaluate(ctx) is None


# ============================================================================
# TC-UNIT-06: R-LOSS-01 (External Path Degradation & Single-Target Guardrail)
# ============================================================================
class TestTCUNIT06ExternalPathDegradationRule:
    """Verifies TC-UNIT-06: External path degradation rule and single-target failure guardrail."""

    def test_tc_unit_06_single_target_failure_guardrail_never_fires_r_loss_01(self, default_thresholds):
        """CRITICAL GUARDRAIL: When Target A has 100% loss but Target B has 0% loss,

        R-LOSS-01 MUST NOT fire; R-INCONCLUSIVE-01 MUST fire.
        """
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "extA": Target(target_id="extA", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "extB": Target(target_id="extB", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            # Target A fails 100%
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extA",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=100.0,
            ),
            # Target B clean 0% loss
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="delay_variation", statistic_name="macd_jitter",
                unit="ms", sample_count_total=10, sample_count_valid=10,
                calculation_method="Mean Absolute Consecutive Difference (MACD)", calculation_version="1.0.0", value_numeric=2.5,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )

        loss_rule = ExternalPathDegradationRule()
        inconc_rule = InconclusiveRule()

        # 1. Assert R-LOSS-01 DOES NOT FIRE
        assert loss_rule.evaluate(ctx) is None, "R-LOSS-01 MUST NOT fire on a single-target external failure!"

        # 2. Assert R-INCONCLUSIVE-01 DOES FIRE
        inconc_result = inconc_rule.evaluate(ctx)
        assert inconc_result is not None, "R-INCONCLUSIVE-01 MUST fire when external targets disagree!"
        assert inconc_result.finding.category == FindingCategory.INCONCLUSIVE
        assert "Target disagreement" in inconc_result.finding.plain_summary

    def test_tc_unit_06_triggers_when_multiple_targets_exhibit_packet_loss(self, default_thresholds):
        """Assert R-LOSS-01 triggers when multiple external targets exhibit loss > 15%."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "extA": Target(target_id="extA", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "extB": Target(target_id="extB", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            # Both external targets exhibit packet loss > 15%
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extA",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=20.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=25.0,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )

        result = ExternalPathDegradationRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOSS-01"
        assert result.finding.category == FindingCategory.EXTERNAL_PATH
        assert result.finding.priority == FindingPriority.WARNING
        assert "multiple independent internet destinations" in result.finding.plain_summary
        assert "ISP technical support" in result.finding.recommended_action

    def test_tc_unit_06_triggers_when_multiple_targets_exhibit_high_jitter(self, default_thresholds):
        """Assert R-LOSS-01 triggers when multiple external targets exhibit jitter > 30ms."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "extA": Target(target_id="extA", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "extB": Target(target_id="extB", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extA",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extA",
                metric_name="delay_variation", statistic_name="macd_jitter",
                unit="ms", sample_count_total=10, sample_count_valid=10,
                calculation_method="MACD", calculation_version="1.0.0", value_numeric=38.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="delay_variation", statistic_name="macd_jitter",
                unit="ms", sample_count_total=10, sample_count_valid=10,
                calculation_method="MACD", calculation_version="1.0.0", value_numeric=42.0,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )

        result = ExternalPathDegradationRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOSS-01"

    def test_tc_unit_06_non_trigger_when_gateway_is_unhealthy(self, default_thresholds):
        """Assert R-LOSS-01 DOES NOT trigger if local gateway loss > 10% (local issue)."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "extA": Target(target_id="extA", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "extB": Target(target_id="extB", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=20.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extA",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=20.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=20.0,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )
        assert ExternalPathDegradationRule().evaluate(ctx) is None


# ============================================================================
# TC-UNIT-07: R-LOCAL-01 (Local Wi-Fi or LAN Degradation)
# ============================================================================
class TestTCUNIT07LocalNetworkDegradationRule:
    """Verifies TC-UNIT-07: Local LAN/Wi-Fi degradation (loss, jitter, RTT spikes)."""

    def test_tc_unit_07_triggers_on_gateway_partial_loss_gt_10_pct(self, default_thresholds):
        """Assert R-LOCAL-01 triggers when gateway loss > 10% and < 100%."""
        run_id = new_id()
        gw_target = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        loss_sum = Summary(
            summary_id=new_id(), run_id=run_id, target_id="gw",
            metric_name="packet_loss", statistic_name="loss_percentage",
            unit="%", sample_count_total=10, sample_count_valid=10,
            calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=20.0,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=[loss_sum],
            targets={"gw": gw_target},
            thresholds=default_thresholds,
        )

        result = LocalNetworkDegradationRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOCAL-01"
        assert result.finding.category == FindingCategory.LOCAL_NETWORK
        assert result.finding.priority == FindingPriority.WARNING
        assert "High latency spikes, packet loss, or delay variation" in result.finding.plain_summary
        assert "Move closer to the Wi-Fi access point" in result.finding.recommended_action

    def test_tc_unit_07_triggers_on_gateway_high_jitter_gt_25_ms(self, default_thresholds):
        """Assert R-LOCAL-01 triggers when gateway jitter > 25ms."""
        run_id = new_id()
        gw_target = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="delay_variation", statistic_name="macd_jitter",
                unit="ms", sample_count_total=10, sample_count_valid=10,
                calculation_method="MACD", calculation_version="1.0.0", value_numeric=32.0,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets={"gw": gw_target},
            thresholds=default_thresholds,
        )
        result = LocalNetworkDegradationRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOCAL-01"

    def test_tc_unit_07_triggers_on_gateway_rtt_spike_gt_100_ms(self, default_thresholds):
        """Assert R-LOCAL-01 triggers when a gateway probe exceeds 100ms."""
        run_id = new_id()
        gw_target = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        obs = [
            make_ping_obs(run_id, "gw", 1, MeasurementStatus.SUCCESS, 2.0),
            make_ping_obs(run_id, "gw", 2, MeasurementStatus.SUCCESS, 3.0),
            make_ping_obs(run_id, "gw", 3, MeasurementStatus.SUCCESS, 135.0),  # Spike > 100ms
            make_ping_obs(run_id, "gw", 4, MeasurementStatus.SUCCESS, 2.5),
            make_ping_obs(run_id, "gw", 5, MeasurementStatus.SUCCESS, 2.8),
        ]
        loss_sum = Summary(
            summary_id=new_id(), run_id=run_id, target_id="gw",
            metric_name="packet_loss", statistic_name="loss_percentage",
            unit="%", sample_count_total=5, sample_count_valid=5,
            calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=obs,
            summaries=[loss_sum],
            targets={"gw": gw_target},
            thresholds=default_thresholds,
        )
        result = LocalNetworkDegradationRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOCAL-01"

    def test_tc_unit_07_exclusion_when_100_percent_loss(self, default_thresholds):
        """Assert R-LOCAL-01 DOES NOT trigger on 100% loss (handled by R-GW-01)."""
        run_id = new_id()
        gw_target = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        loss_sum = Summary(
            summary_id=new_id(), run_id=run_id, target_id="gw",
            metric_name="packet_loss", statistic_name="loss_percentage",
            unit="%", sample_count_total=5, sample_count_valid=5,
            calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=100.0,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=[loss_sum],
            targets={"gw": gw_target},
            thresholds=default_thresholds,
        )
        assert LocalNetworkDegradationRule().evaluate(ctx) is None


# ============================================================================
# TC-UNIT-08: R-LOAD-01 (Bufferbloat / Latency Increase Under Load)
# ============================================================================
class TestTCUNIT08BufferbloatRule:
    """Verifies TC-UNIT-08: Bufferbloat rule triggers, delta/ratio calculation, and exclusions."""

    def test_tc_unit_08_triggers_when_rtt_delta_gt_50_ms(self, default_thresholds):
        """Assert R-LOAD-01 triggers when (Median_loaded - Median_idle) > 50ms."""
        run_id = new_id()
        meta = {
            "baseline_median_rtt_ms": 12.0,
            "loaded_median_rtt_ms": 75.0,
            "delta_rtt_median_ms": 63.0,
            "bufferbloat_ratio": 2.1,
            "baseline_valid_count": 5,
            "loaded_valid_count": 10,
        }
        delta_obs = Observation(
            observation_id=new_id(),
            run_id=run_id,
            target_id="tp",
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=0.0,
            metric_name="loaded_latency_delta",
            value_numeric=63.0,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            method_metadata=json.dumps(meta),
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[delta_obs],
            summaries=[],
            targets={},
            thresholds=default_thresholds,
        )

        result = BufferbloatRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOAD-01"
        assert result.finding.category == FindingCategory.BUFFERBLOAT
        assert result.finding.priority == FindingPriority.WARNING
        assert "Network latency increases significantly when the connection is busy" in result.finding.plain_summary
        assert "Enable Smart Queue Management" in result.finding.recommended_action

    def test_tc_unit_08_triggers_when_ratio_gt_2_5(self, default_thresholds):
        """Assert R-LOAD-01 triggers when bufferbloat ratio > 2.5."""
        run_id = new_id()
        meta = {
            "baseline_median_rtt_ms": 10.0,
            "loaded_median_rtt_ms": 25.0,
            "delta_rtt_median_ms": 15.0,  # Below 50ms
            "bufferbloat_ratio": 3.2,     # Above 2.5 ratio
            "baseline_valid_count": 5,
            "loaded_valid_count": 10,
        }
        delta_obs = Observation(
            observation_id=new_id(),
            run_id=run_id,
            target_id="tp",
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=0.0,
            metric_name="loaded_latency_delta",
            value_numeric=15.0,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            method_metadata=json.dumps(meta),
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[delta_obs],
            summaries=[],
            targets={},
            thresholds=default_thresholds,
        )
        result = BufferbloatRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOAD-01"

    def test_tc_unit_08_non_trigger_when_latency_under_load_is_stable(self, default_thresholds):
        """Assert R-LOAD-01 DOES NOT trigger when loaded latency is clean."""
        run_id = new_id()
        meta = {
            "baseline_median_rtt_ms": 12.0,
            "loaded_median_rtt_ms": 16.0,
            "delta_rtt_median_ms": 4.0,
            "bufferbloat_ratio": 1.4,
            "baseline_valid_count": 5,
            "loaded_valid_count": 10,
        }
        delta_obs = Observation(
            observation_id=new_id(),
            run_id=run_id,
            target_id="tp",
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=0.0,
            metric_name="loaded_latency_delta",
            value_numeric=4.0,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            method_metadata=json.dumps(meta),
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[delta_obs],
            summaries=[],
            targets={},
            thresholds=default_thresholds,
        )
        assert BufferbloatRule().evaluate(ctx) is None

    def test_tc_unit_08_exclusion_when_baseline_already_unstable(self, default_thresholds):
        """Assert R-LOAD-01 DOES NOT trigger when baseline idle latency was already jittery (> 40ms)."""
        run_id = new_id()
        gw_target = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        jitter_sum = Summary(
            summary_id=new_id(), run_id=run_id, target_id="gw",
            metric_name="delay_variation", statistic_name="macd_jitter",
            unit="ms", sample_count_total=5, sample_count_valid=5,
            calculation_method="MACD", calculation_version="1.0.0", value_numeric=52.0,
        )
        meta = {
            "baseline_median_rtt_ms": 30.0,
            "loaded_median_rtt_ms": 95.0,
            "delta_rtt_median_ms": 65.0,
            "bufferbloat_ratio": 3.2,
            "baseline_valid_count": 5,
            "loaded_valid_count": 10,
        }
        delta_obs = Observation(
            observation_id=new_id(),
            run_id=run_id,
            target_id="tp",
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=0.0,
            metric_name="loaded_latency_delta",
            value_numeric=65.0,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            method_metadata=json.dumps(meta),
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[delta_obs],
            summaries=[jitter_sum],
            targets={"gw": gw_target},
            thresholds=default_thresholds,
        )
        assert BufferbloatRule().evaluate(ctx) is None


# ============================================================================
# TC-UNIT-09: R-INCONCLUSIVE-01 (Insufficient, Ambiguous, Conflicting Evidence)
# ============================================================================
class TestTCUNIT09InconclusiveRule:
    """Verifies TC-UNIT-09: Inconclusive rule triggers on contradictory/incomplete measurements."""

    def test_tc_unit_09_fires_on_incomplete_probes_due_to_cancellation(self, default_thresholds):
        """Assert R-INCONCLUSIVE-01 triggers when probes < 5 due to cancellation."""
        run_id = new_id()
        gw = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        obs = [
            make_ping_obs(run_id, "gw", 1, MeasurementStatus.SUCCESS, 2.1),
            make_ping_obs(run_id, "gw", 2, MeasurementStatus.SUCCESS, 2.3),
        ]  # Only 2 probes collected
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=obs,
            summaries=[],
            targets={"gw": gw},
            thresholds=default_thresholds,
        )

        result = InconclusiveRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-INCONCLUSIVE-01"
        assert result.finding.category == FindingCategory.INCONCLUSIVE
        assert result.finding.priority == FindingPriority.INFO
        assert "Incomplete probe sequence" in result.finding.plain_summary

    def test_tc_unit_09_fires_on_high_icmp_loss_with_successful_tcp_throughput(self, default_thresholds):
        """Assert R-INCONCLUSIVE-01 triggers when ICMP loss is high but TCP throughput succeeded."""
        run_id = new_id()
        gw = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        tp = Target(target_id="tp", target_type=TargetType.THROUGHPUT_ENDPOINT, hostname="https://example.com/test")

        loss_sum = Summary(
            summary_id=new_id(), run_id=run_id, target_id="gw",
            metric_name="packet_loss", statistic_name="loss_percentage",
            unit="%", sample_count_total=5, sample_count_valid=5,
            calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=60.0,
        )
        tp_obs = Observation(
            observation_id=new_id(),
            run_id=run_id,
            target_id="tp",
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=4500.0,
            metric_name="throughput_mbps",
            value_numeric=52.4,  # Normal 52.4 Mbps throughput
            unit="Mbps",
            status=MeasurementStatus.SUCCESS,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[tp_obs],
            summaries=[loss_sum],
            targets={"gw": gw, "tp": tp},
            thresholds=default_thresholds,
        )

        result = InconclusiveRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-INCONCLUSIVE-01"
        assert "Contradictory evidence" in result.finding.plain_summary


# ============================================================================
# Actionable Recommendations & Limitations (SRS-F-013, AC-DIAG-05)
# ============================================================================
class TestRecommendationEngine:
    """Verifies that evidence-linked recommendations are produced for all rule findings."""

    def test_recommendations_generated_for_all_rule_types(self):
        """Verify user and technician recommendations across all six rule types."""
        engine = RecommendationEngine()
        rule_ids = ["R-GW-01", "R-DNS-01", "R-LOSS-01", "R-LOCAL-01", "R-LOAD-01", "R-INCONCLUSIVE-01"]
        findings = [
            Finding(
                finding_id=new_id(),
                run_id="run-1",
                category=FindingCategory.LOCAL_NETWORK if rid in ("R-GW-01", "R-LOCAL-01") else (
                    FindingCategory.DNS_SUBSYSTEM if rid == "R-DNS-01" else (
                        FindingCategory.EXTERNAL_PATH if rid == "R-LOSS-01" else (
                            FindingCategory.BUFFERBLOAT if rid == "R-LOAD-01" else FindingCategory.INCONCLUSIVE
                        )
                    )
                ),
                priority=FindingPriority.CRITICAL if rid in ("R-GW-01", "R-DNS-01") else FindingPriority.WARNING,
                rule_id=rid,
                rule_version="1.0.0",
                status=FindingStatus.SUPPORTED,
                evidence_strength=EvidenceStrength.STRONG,
                plain_summary=f"Summary for {rid}",
                limitations=f"Limitations for {rid}",
                recommended_action=f"Action for {rid}",
            )
            for rid in rule_ids
        ]

        recommendations = engine.generate_recommendations(findings)
        assert len(recommendations) == 6

        for rec in recommendations:
            assert rec.rule_id in rule_ids
            assert len(rec.user_actions) >= 1
            for act in rec.user_actions:
                assert act.target_audience == "USER"
                assert act.title != ""
                assert act.description != ""
                assert act.rationale != ""

            if rec.rule_id != "UNKNOWN":
                assert len(rec.technician_actions) >= 1
                for tech_act in rec.technician_actions:
                    assert tech_act.target_audience == "TECHNICIAN"
                    assert tech_act.title != ""
                    assert tech_act.description != ""
                    assert tech_act.rationale != ""


# ============================================================================
# Diagnosis Engine Pipeline & Persistence Integration
# ============================================================================
class TestDiagnosisEngineIntegration:
    """Verifies DiagnosisEngine priority sorting, execution flow, and database persistence."""

    def test_engine_priority_execution_and_clean_run(self, default_thresholds):
        """Assert engine runs clean baseline without emitting false positives."""
        run_id = new_id()
        gw = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        ext1 = Target(target_id="ext1", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1")
        ext2 = Target(target_id="ext2", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8")

        # Perfectly healthy measurements
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext1",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="ext2",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets={"gw": gw, "ext1": ext1, "ext2": ext2},
            thresholds=default_thresholds,
        )

        engine = DiagnosisEngine(thresholds=default_thresholds)
        results = engine.evaluate(ctx)

        # Clean baseline: zero failure rules should trigger
        assert len(results) == 0

    def test_engine_evaluate_run_persists_findings_and_evidence(self, unit_db, default_thresholds):
        """Assert evaluate_run queries SQLite, evaluates rules, and persists findings and evidence."""
        run_id = new_id()
        # Seed test run in database
        from ticnet.persistence.repositories.run_repo import RunRepository
        from ticnet.models.entities import TestRun
        from ticnet.models.enums import RunStatus, RunMode

        run_repo = RunRepository(unit_db)
        run_repo.create_run(
            TestRun(
                run_id=run_id,
                start_time_utc=now_utc_iso(),
                status=RunStatus.RUNNING,
                app_version="1.0.0",
                config_version="1.0.0",
                run_mode=RunMode.OPERATIONAL,
            )
        )

        obs_repo = ObservationRepository(unit_db)
        summary_repo = SummaryRepository(unit_db)
        finding_repo = FindingRepository(unit_db)

        # Create gateway target with 100% loss (triggers R-GW-01)
        gw = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        obs_repo.create_target(gw)

        obs_list = [
            make_ping_obs(run_id, "gw", i + 1, MeasurementStatus.TIMEOUT, None)
            for i in range(5)
        ]
        obs_repo.create_observations_batch(obs_list)

        loss_sum = Summary(
            summary_id=new_id(), run_id=run_id, target_id="gw",
            metric_name="packet_loss", statistic_name="loss_percentage",
            unit="%", sample_count_total=5, sample_count_valid=5,
            calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=100.0,
        )
        summary_repo.create_summary(loss_sum)

        engine = DiagnosisEngine(thresholds=default_thresholds)
        findings = engine.evaluate_run(run_id=run_id, db=unit_db, thresholds=default_thresholds)

        assert len(findings) == 1
        assert findings[0].rule_id == "R-GW-01"

        # Verify findings persisted in SQLite
        db_findings = finding_repo.get_findings_for_run(run_id)
        assert len(db_findings) == 1
        assert db_findings[0].rule_id == "R-GW-01"
        assert db_findings[0].category == FindingCategory.LOCAL_NETWORK

        # Verify evidence persisted in SQLite
        db_evidence = finding_repo.get_evidence_for_finding(findings[0].finding_id)
        assert len(db_evidence) == 6  # 1 summary + 5 observations
        assert any(e.summary_id == loss_sum.summary_id for e in db_evidence)

    def test_engine_suppresses_downstream_rules_when_gateway_unreachable(self, default_thresholds):
        """Assert engine suppresses R-LOCAL-01, R-LOAD-01, R-LOSS-01 when R-GW-01 triggers."""
        run_id = new_id()
        gw = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        loss_sum = Summary(
            summary_id=new_id(), run_id=run_id, target_id="gw",
            metric_name="packet_loss", statistic_name="loss_percentage",
            unit="%", sample_count_total=5, sample_count_valid=5,
            calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=100.0,
        )
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=[loss_sum],
            targets={"gw": gw},
            thresholds=default_thresholds,
        )

        engine = DiagnosisEngine(thresholds=default_thresholds)
        results = engine.evaluate(ctx)

        assert len(results) == 1
        assert results[0].finding.rule_id == "R-GW-01"

    def test_loss_rule_three_targets_yields_strong_evidence(self, default_thresholds):
        """Assert R-LOSS-01 reports STRONG evidence when >= 3 targets degrade."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "extA": Target(target_id="extA", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "extB": Target(target_id="extB", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
            "extC": Target(target_id="extC", target_type=TargetType.EXTERNAL_IP, ip_address="9.9.9.9"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extA",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=20.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=22.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extC",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=25.0,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )

        result = ExternalPathDegradationRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOSS-01"
        assert result.finding.evidence_strength == EvidenceStrength.STRONG

    def test_loss_rule_mixed_degradation_metrics(self, default_thresholds):
        """Assert R-LOSS-01 triggers when Target A degrades by loss and Target B degrades by jitter."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "extA": Target(target_id="extA", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "extB": Target(target_id="extB", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
        }
        summaries = [
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="gw",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=5, sample_count_valid=5,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            # Target A has high loss (25%), normal jitter
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extA",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=25.0,
            ),
            # Target B has low loss (0%), but high jitter (45ms > 30ms)
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="packet_loss", statistic_name="loss_percentage",
                unit="%", sample_count_total=10, sample_count_valid=10,
                calculation_method="Standard Probe Ratio", calculation_version="1.0.0", value_numeric=0.0,
            ),
            Summary(
                summary_id=new_id(), run_id=run_id, target_id="extB",
                metric_name="delay_variation", statistic_name="macd_jitter",
                unit="ms", sample_count_total=10, sample_count_valid=10,
                calculation_method="MACD", calculation_version="1.0.0", value_numeric=45.0,
            ),
        ]
        ctx = DiagnosisContext(
            run_id=run_id,
            observations=[],
            summaries=summaries,
            targets=targets,
            thresholds=default_thresholds,
        )

        result = ExternalPathDegradationRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-LOSS-01"

    def test_inconclusive_rule_fires_when_all_probes_blocked(self, default_thresholds):
        """Assert R-INCONCLUSIVE-01 triggers when firewall actively blocks all probing."""
        run_id = new_id()
        obs = [
            Observation(
                observation_id=new_id(), run_id=run_id, sample_index=i + 1,
                timestamp_utc=now_utc_iso(), duration_ms=0.0, metric_name="round_trip_time",
                value_numeric=None, unit="ms", status=MeasurementStatus.BLOCKED,
            )
            for i in range(5)
        ]
        ctx = DiagnosisContext(run_id=run_id, observations=obs, summaries=[], targets={}, thresholds=default_thresholds)
        result = InconclusiveRule().evaluate(ctx)
        assert result is not None
        assert result.finding.rule_id == "R-INCONCLUSIVE-01"
        assert "Blocked probing" in result.finding.plain_summary

    def test_engine_custom_rules_and_property(self):
        """Assert DiagnosisEngine accepts custom rule list and exposes rules property."""
        gw_rule = GatewayUnreachableRule()
        engine = DiagnosisEngine(rules=[gw_rule])
        assert len(engine.rules) == 1
        assert engine.rules[0] is gw_rule

    def test_engine_handles_exception_in_rule_gracefully(self, default_thresholds):
        """Assert DiagnosisEngine logs and continues if an individual rule raises an exception."""
        class BuggyRule(GatewayUnreachableRule):
            @property
            def rule_id(self) -> str:
                return "R-BUGGY-01"

            def evaluate(self, ctx: DiagnosisContext):
                raise RuntimeError("Simulated rule bug")

        engine = DiagnosisEngine(rules=[BuggyRule(), GatewayUnreachableRule()], thresholds=default_thresholds)
        ctx = DiagnosisContext(run_id=new_id(), observations=[], summaries=[], targets={}, thresholds=default_thresholds)
        # Should not raise; BuggyRule error is logged and returns remaining results
        results = engine.evaluate(ctx)
        assert isinstance(results, list)

    def test_engine_evaluate_run_uses_default_thresholds_if_unspecified(self, unit_db):
        """Assert evaluate_run loads default thresholds when thresholds is None."""
        run_id = new_id()
        from ticnet.persistence.repositories.run_repo import RunRepository
        from ticnet.models.entities import TestRun
        from ticnet.models.enums import RunStatus, RunMode

        run_repo = RunRepository(unit_db)
        run_repo.create_run(
            TestRun(
                run_id=run_id,
                start_time_utc=now_utc_iso(),
                status=RunStatus.RUNNING,
                app_version="1.0.0",
                config_version="1.0.0",
                run_mode=RunMode.OPERATIONAL,
            )
        )
        engine = DiagnosisEngine()  # thresholds=None
        findings = engine.evaluate_run(run_id=run_id, db=unit_db)
        assert isinstance(findings, list)

    def test_gateway_rule_evaluates_from_raw_observations_when_summary_absent(self, default_thresholds):
        """Assert R-GW-01 calculates loss percentage from raw observations if summary is missing."""
        run_id = new_id()
        gw = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        obs = [
            make_ping_obs(run_id, "gw", i + 1, MeasurementStatus.TIMEOUT, None)
            for i in range(5)
        ]
        ctx = DiagnosisContext(run_id=run_id, observations=obs, summaries=[], targets={"gw": gw}, thresholds=default_thresholds)
        res = GatewayUnreachableRule().evaluate(ctx)
        assert res is not None
        assert res.finding.rule_id == "R-GW-01"

    def test_local_rule_evaluates_from_raw_observations_when_summary_absent(self, default_thresholds):
        """Assert R-LOCAL-01 calculates loss from raw observations if summary is missing."""
        run_id = new_id()
        gw = Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1")
        # 1 timeout out of 5 = 20% loss
        obs = [
            make_ping_obs(run_id, "gw", 1, MeasurementStatus.TIMEOUT, None),
            make_ping_obs(run_id, "gw", 2, MeasurementStatus.SUCCESS, 4.0),
            make_ping_obs(run_id, "gw", 3, MeasurementStatus.SUCCESS, 4.2),
            make_ping_obs(run_id, "gw", 4, MeasurementStatus.SUCCESS, 4.1),
            make_ping_obs(run_id, "gw", 5, MeasurementStatus.SUCCESS, 4.0),
        ]
        ctx = DiagnosisContext(run_id=run_id, observations=obs, summaries=[], targets={"gw": gw}, thresholds=default_thresholds)
        res = LocalNetworkDegradationRule().evaluate(ctx)
        assert res is not None
        assert res.finding.rule_id == "R-LOCAL-01"

    def test_loss_rule_evaluates_from_raw_observations_when_summary_absent(self, default_thresholds):
        """Assert R-LOSS-01 evaluates gateway and external targets from observations if summaries absent."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "extA": Target(target_id="extA", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "extB": Target(target_id="extB", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
        }
        gw_obs = [make_ping_obs(run_id, "gw", i + 1, MeasurementStatus.SUCCESS, 2.0) for i in range(5)]
        # extA has 3 timeouts out of 10 = 30% loss
        extA_obs = [make_ping_obs(run_id, "extA", i + 1, MeasurementStatus.TIMEOUT if i < 3 else MeasurementStatus.SUCCESS, None if i < 3 else 15.0) for i in range(10)]
        # extB has 2 timeouts out of 10 = 20% loss
        extB_obs = [make_ping_obs(run_id, "extB", i + 1, MeasurementStatus.TIMEOUT if i < 2 else MeasurementStatus.SUCCESS, None if i < 2 else 18.0) for i in range(10)]

        ctx = DiagnosisContext(
            run_id=run_id,
            observations=gw_obs + extA_obs + extB_obs,
            summaries=[],
            targets=targets,
            thresholds=default_thresholds,
        )
        res = ExternalPathDegradationRule().evaluate(ctx)
        assert res is not None
        assert res.finding.rule_id == "R-LOSS-01"

    def test_dns_rule_handles_servfail_rcode_name(self, default_thresholds):
        """Assert R-DNS-01 recognizes SERVFAIL response from rcode_name or status."""
        run_id = new_id()
        targets = {
            "gw": Target(target_id="gw", target_type=TargetType.GATEWAY, ip_address="192.168.1.1"),
            "ext1": Target(target_id="ext1", target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1"),
            "ext2": Target(target_id="ext2", target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8"),
            "dns1": Target(target_id="dns1", target_type=TargetType.DNS_RESOLVER, ip_address="192.168.1.1"),
        }
        gw_obs = [make_ping_obs(run_id, "gw", i + 1, MeasurementStatus.SUCCESS, 2.0) for i in range(5)]
        ext1_obs = [make_ping_obs(run_id, "ext1", i + 1, MeasurementStatus.SUCCESS, 15.0) for i in range(10)]
        ext2_obs = [make_ping_obs(run_id, "ext2", i + 1, MeasurementStatus.SUCCESS, 16.0) for i in range(10)]

        dns_obs = [
            Observation(
                observation_id=new_id(),
                run_id=run_id,
                target_id="dns1",
                sample_index=i + 1,
                timestamp_utc=now_utc_iso(),
                duration_ms=45.0,
                metric_name="dns_resolution_time",
                value_numeric=None,
                unit="ms",
                status=MeasurementStatus.FAILED,
                method_metadata=json.dumps({"domain": f"domain{i}.com", "rcode": 2, "rcode_name": "SERVFAIL"}),
            )
            for i in range(5)
        ]

        ctx = DiagnosisContext(
            run_id=run_id,
            observations=gw_obs + ext1_obs + ext2_obs + dns_obs,
            summaries=[],
            targets=targets,
            thresholds=default_thresholds,
        )
        res = DNSSubsystemFailureRule().evaluate(ctx)
        assert res is not None
        assert res.finding.rule_id == "R-DNS-01"

    def test_recommender_unknown_rule_fallback(self):
        """Assert RecommendationEngine handles unknown rule ID gracefully."""
        finding = Finding(
            finding_id=new_id(),
            run_id="run-1",
            category=FindingCategory.INCONCLUSIVE,
            priority=FindingPriority.INFO,
            rule_id="R-UNKNOWN-99",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.LIMITED,
            plain_summary="Unknown issue",
            limitations="None",
            recommended_action="Inspect logs",
        )
        engine = RecommendationEngine()
        recs = engine.generate_recommendations([finding])
        assert len(recs) == 1
        assert len(recs[0].user_actions) == 1
        assert recs[0].user_actions[0].description == "Inspect logs"
        assert len(recs[0].technician_actions) == 0



