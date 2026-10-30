"""Tests for Four Reproducible Network Fault Scenarios (TC-LAB-01 through TC-LAB-04).

Covers:
- TC-LAB-01 / AC-LAB-02: Scenario A — Default Gateway Unreachable (R-GW-01, Stage 5 skipped, no ISP blame)
- TC-LAB-02 / AC-LAB-03: Scenario B — DNS Subsystem Failure (R-DNS-01, IP routing intact)
- TC-LAB-03 / AC-LAB-04: Scenario C — Induced Packet Loss (R-LOSS-01 multi-target, R-INCONCLUSIVE-01 single-target guardrail)
- TC-LAB-04 / AC-LAB-05: Scenario D — Network Congestion & Bufferbloat (R-LOAD-01, loaded latency spike)
"""

from __future__ import annotations

import pytest

from ticnet.configuration.loader import load_app_config
from ticnet.models.enums import (
    FindingCategory,
    FindingPriority,
    LabScenario,
    StageStatus,
    TrialCleanupStatus,
    TrialVerificationStatus,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from lab.runner import LabTrialRunner
from lab.scenarios.scenario_a_gateway import ScenarioAGateway
from lab.scenarios.scenario_b_dns import ScenarioBDNS
from lab.scenarios.scenario_c_loss import ScenarioCLoss
from lab.scenarios.scenario_d_congestion import ScenarioDCongestion


@pytest.fixture
def temp_db(tmp_path):
    """Provide temporary test SQLite database with initial schema applied."""
    db_file = tmp_path / "test_lab_scenarios.db"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def app_config():
    """Load default validated application configuration."""
    return load_app_config()


class TestLabScenarios:
    """Verifies TC-LAB-01 through TC-LAB-04."""

    def test_tc_lab_01_scenario_a_gateway_unreachable(self, temp_db: DatabaseManager, app_config) -> None:
        """TC-LAB-01 / AC-LAB-02: Scenario A — Gateway Unreachable.

        Verifies:
        1. Gateway reachability failure (100% loss) is injected and verified independently.
        2. DiagnosisEngine triggers R-GW-01 with CRITICAL priority and LOCAL_NETWORK category.
        3. Throughput Stage 5 is skipped per SRS-F-008.
        4. No unsupported external ISP transit or damaged cable claims are made.
        5. Restoration and cleanup verification succeed.
        """
        runner = LabTrialRunner(
            db=temp_db,
            config=app_config,
            operator_confirmed=True,
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
        )
        scenario = ScenarioAGateway(gateway_ip="192.168.100.1")

        result = runner.execute_scenario_trial(scenario=scenario)

        # 1. Verification status must be MATCH
        assert result.matched is True
        assert result.verification_status == TrialVerificationStatus.MATCH
        assert result.expected_rule_id == "R-GW-01"
        assert result.expected_category == FindingCategory.LOCAL_NETWORK
        assert result.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED

        # 2. Verify stage 5 was SKIPPED
        run_repo = RunRepository(temp_db)
        stages = run_repo.get_stages_for_run(result.run_id)
        stage5 = next((s for s in stages if s.stage_order == 5), None)
        assert stage5 is not None
        assert stage5.status == StageStatus.SKIPPED
        assert "Gateway unreachable" in (stage5.skip_reason or "")

        # 3. Verify no unsupported claims in findings
        finding_repo = FindingRepository(temp_db)
        findings = finding_repo.get_findings_for_run(result.run_id)
        gw_finding = next((f for f in findings if f.rule_id == "R-GW-01"), None)
        assert gw_finding is not None
        assert gw_finding.priority == FindingPriority.CRITICAL
        text_corpus = (
            gw_finding.plain_summary
            + (gw_finding.limitations or "")
            + (gw_finding.recommended_action or "")
        ).lower()
        assert "damaged cable" not in text_corpus
        assert "isp outage" not in text_corpus

    def test_tc_lab_02_scenario_b_dns_failure(self, temp_db: DatabaseManager, app_config) -> None:
        """TC-LAB-02 / AC-LAB-03: Scenario B — DNS Subsystem Failure.

        Verifies:
        1. DNS failure is injected (UDP 53 timeout to resolver).
        2. Gateway connectivity and external IP routing remain healthy.
        3. DiagnosisEngine triggers R-DNS-01 with CRITICAL priority and DNS_SUBSYSTEM category.
        4. Distinguishes DNS failure from general connectivity or total transit failure.
        5. Restoration and cleanup verification succeed.
        """
        runner = LabTrialRunner(
            db=temp_db,
            config=app_config,
            operator_confirmed=True,
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
        )
        scenario = ScenarioBDNS(gateway_ip="192.168.100.1", resolver_ip="192.168.100.2")

        result = runner.execute_scenario_trial(scenario=scenario)

        assert result.matched is True
        assert result.verification_status == TrialVerificationStatus.MATCH
        assert result.expected_rule_id == "R-DNS-01"
        assert result.expected_category == FindingCategory.DNS_SUBSYSTEM
        assert result.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED

        # Verify findings
        finding_repo = FindingRepository(temp_db)
        findings = finding_repo.get_findings_for_run(result.run_id)
        dns_finding = next((f for f in findings if f.rule_id == "R-DNS-01"), None)
        assert dns_finding is not None
        assert dns_finding.category == FindingCategory.DNS_SUBSYSTEM
        assert dns_finding.priority == FindingPriority.CRITICAL

    def test_tc_lab_03_scenario_c_multi_target_packet_loss(self, temp_db: DatabaseManager, app_config) -> None:
        """TC-LAB-03 / AC-LAB-04: Scenario C — Induced Multi-Target Packet Loss.

        Verifies:
        1. Controlled packet loss (20%) is induced on external targets while gateway is clean.
        2. DiagnosisEngine triggers R-LOSS-01 with WARNING priority and EXTERNAL_PATH category.
        3. End-to-end evidence is verified across >=2 diverse destinations.
        4. Restoration and cleanup verification succeed.
        """
        runner = LabTrialRunner(
            db=temp_db,
            config=app_config,
            operator_confirmed=True,
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
        )
        scenario = ScenarioCLoss(gateway_ip="192.168.100.1")

        result = runner.execute_scenario_trial(scenario=scenario, single_target_mode=False)

        assert result.matched is True
        assert result.verification_status == TrialVerificationStatus.MATCH
        assert result.expected_rule_id == "R-LOSS-01"
        assert result.expected_category == FindingCategory.EXTERNAL_PATH
        assert result.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED

        finding_repo = FindingRepository(temp_db)
        findings = finding_repo.get_findings_for_run(result.run_id)
        loss_finding = next((f for f in findings if f.rule_id == "R-LOSS-01"), None)
        assert loss_finding is not None
        assert loss_finding.category == FindingCategory.EXTERNAL_PATH
        assert loss_finding.priority == FindingPriority.WARNING

    def test_tc_lab_03_scenario_c_single_target_guardrail(self, temp_db: DatabaseManager, app_config) -> None:
        """TC-LAB-03 / AC-LAB-04: Scenario C — Single-Target Loss Guardrail.

        Verifies:
        1. Single-target degradation (only 1 target degraded, others healthy).
        2. Guardrail prevents false ISP blame: R-LOSS-01 does NOT fire.
        3. R-INCONCLUSIVE-01 fires with INCONCLUSIVE category.
        """
        runner = LabTrialRunner(
            db=temp_db,
            config=app_config,
            operator_confirmed=True,
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
        )
        scenario = ScenarioCLoss(gateway_ip="192.168.100.1")

        result = runner.execute_scenario_trial(scenario=scenario, single_target_mode=True)

        assert result.matched is True
        assert result.verification_status == TrialVerificationStatus.MATCH
        assert result.expected_rule_id == "R-INCONCLUSIVE-01"
        assert result.expected_category == FindingCategory.INCONCLUSIVE
        assert result.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED

        finding_repo = FindingRepository(temp_db)
        findings = finding_repo.get_findings_for_run(result.run_id)
        emitted_rule_ids = {f.rule_id for f in findings}
        assert "R-LOSS-01" not in emitted_rule_ids
        assert "R-INCONCLUSIVE-01" in emitted_rule_ids

    def test_tc_lab_04_scenario_d_network_congestion(self, temp_db: DatabaseManager, app_config) -> None:
        """TC-LAB-04 / AC-LAB-05: Scenario D — Network Congestion & Bufferbloat.

        Verifies:
        1. Baseline latency is healthy (12ms, jitter <= 40ms).
        2. Loaded latency spikes to 140ms (delta > 50ms, ratio > 2.5).
        3. DiagnosisEngine triggers R-LOAD-01 with WARNING priority and BUFFERBLOAT category.
        4. Loaded latency degradation is distinguished from ordinary latency variation.
        5. Restoration and cleanup verification succeed.
        """
        runner = LabTrialRunner(
            db=temp_db,
            config=app_config,
            operator_confirmed=True,
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
        )
        scenario = ScenarioDCongestion(
            gateway_ip="192.168.100.1",
            baseline_rtt_ms=12.0,
            loaded_rtt_ms=140.0,
        )

        result = runner.execute_scenario_trial(scenario=scenario)

        assert result.matched is True
        assert result.verification_status == TrialVerificationStatus.MATCH
        assert result.expected_rule_id == "R-LOAD-01"
        assert result.expected_category == FindingCategory.BUFFERBLOAT
        assert result.cleanup_status == TrialCleanupStatus.CLEANED_VERIFIED

        finding_repo = FindingRepository(temp_db)
        findings = finding_repo.get_findings_for_run(result.run_id)
        load_finding = next((f for f in findings if f.rule_id == "R-LOAD-01"), None)
        assert load_finding is not None
        assert load_finding.category == FindingCategory.BUFFERBLOAT
        assert load_finding.priority == FindingPriority.WARNING
