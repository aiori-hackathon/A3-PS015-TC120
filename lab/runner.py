"""Automated Laboratory Trial Runner and Multi-Scenario Orchestrator.

Orchestrates the complete laboratory trial lifecycle:
1. Safety Gate Verification (fail-closed).
2. Healthy baseline run.
3. Fault injection & independent injection verification.
4. Quarantined ground truth persistence.
5. Blind diagnostic workflow execution (WorkflowOrchestrator).
6. Blind deterministic rule evaluation (DiagnosisEngine).
7. Network restoration & independent cleanup verification.
8. Ground truth comparison and evaluation (LabEvaluator).

Reference: 09_LAB_SAFETY_RUNBOOK.md Section 4, SRS-F-015, SRS-F-016
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from ticnet.configuration.loader import load_app_config
from ticnet.configuration.validator import AppConfig
from ticnet.diagnosis.engine import DiagnosisEngine
from ticnet.models.enums import RunMode, TrialVerificationStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.workflow.orchestrator import WorkflowOrchestrator
from lab.evaluator import EvaluationResult, LabEvaluator
from lab.ground_truth import LabGroundTruthManager
from lab.safety_gate import LabSafetyGate
from lab.scenarios.base import LabScenarioBase
from lab.scenarios.scenario_a_gateway import ScenarioAGateway
from lab.scenarios.scenario_b_dns import ScenarioBDNS
from lab.scenarios.scenario_c_loss import ScenarioCLoss
from lab.scenarios.scenario_d_congestion import ScenarioDCongestion

logger = logging.getLogger("ticnet.lab.runner")


class LabTrialRunner:
    """Executes controlled laboratory trials safely and deterministically."""

    def __init__(
        self,
        db: DatabaseManager,
        config: Optional[AppConfig] = None,
        operator_confirmed: bool = True,
        target_interface: str = "veth-client",
        lab_gateway_ip: str = "127.0.0.1",
    ) -> None:
        self.db = db
        self.config = config or load_app_config()
        self.safety_gate = LabSafetyGate(
            target_interface=target_interface,
            lab_gateway_ip=lab_gateway_ip,
            operator_confirmed=operator_confirmed,
        )
        self.ground_truth_mgr = LabGroundTruthManager(db)
        self.evaluator = LabEvaluator(db)
        self.diagnosis_engine = DiagnosisEngine(
            thresholds=self.config.thresholds if hasattr(self.config, "thresholds") else None
        )

    def execute_scenario_trial(
        self,
        scenario: LabScenarioBase,
        trial_id: Optional[str] = None,
        single_target_mode: bool = False,
    ) -> EvaluationResult:
        """Execute a complete laboratory trial for the specified fault scenario."""
        trial_id = trial_id or f"trial-{scenario.scenario_id.value.lower()}-{uuid.uuid4().hex[:8]}"
        logger.info("Starting lab trial: %s (Scenario: %s)", trial_id, scenario.name)

        # 1. Pre-flight Safety Gate verification
        self.safety_gate.verify_all()

        # 2. Inject fault
        inject_kwargs = {}
        if hasattr(scenario, "inject"):
            if isinstance(scenario, ScenarioCLoss):
                inject_kwargs["single_target_mode"] = single_target_mode
            injection_params = scenario.inject(**inject_kwargs)
        else:
            injection_params = {}

        # 3. Independently verify injection
        if not scenario.verify_injection():
            scenario.restore()
            raise RuntimeError(f"Fault injection verification failed for {scenario.name}")

        run_id = f"run-lab-{uuid.uuid4().hex[:8]}"
        expected_rule_id = scenario.get_expected_rule_id()
        expected_category = scenario.get_expected_category()

        try:
            # 4. Execute diagnostic workflow (blind to ground truth)
            adapters = scenario.create_adapters() if hasattr(scenario, "create_adapters") else {}
            orchestrator = WorkflowOrchestrator(
                db=self.db,
                gateway_adapter=adapters.get("gateway_adapter"),
                ping_adapter=adapters.get("ping_adapter"),
                dns_adapter=adapters.get("dns_adapter"),
                throughput_adapter=adapters.get("throughput_adapter"),
                load_adapter=adapters.get("load_adapter"),
            )

            orchestrator.execute_run(
                config=self.config,
                run_id=run_id,
                run_mode=RunMode.LAB,
            )

            # 5. Quarantined Ground Truth Recording (linked to existing test_runs record via FK)
            self.ground_truth_mgr.record_trial(
                trial_id=trial_id,
                run_id=run_id,
                scenario_id=scenario.scenario_id,
                ground_truth_label=scenario.get_ground_truth_label(),
                injection_mechanism=injection_params.get("mechanism", "controlled_simulation"),
                injection_parameters=injection_params,
            )

            # 6. Blind Diagnosis Evaluation
            self.diagnosis_engine.evaluate_run(run_id=run_id, db=self.db)

        finally:
            # 7. Restore network to clean baseline
            scenario.restore()
            # 8. Verify clean restoration
            cleanup_ok = scenario.verify_cleanup()

        # 9. Evaluate outcome against quarantined ground truth
        eval_result = self.evaluator.evaluate_trial(
            trial_id=trial_id,
            expected_rule_id=expected_rule_id,
            expected_category=expected_category,
            cleanup_verified=cleanup_ok,
        )

        logger.info(
            "Lab trial %s completed: %s (Matched: %s)",
            trial_id,
            eval_result.verification_status.value,
            eval_result.matched,
        )
        return eval_result

    def run_full_matrix(self, repetitions_per_scenario: int = 3) -> Dict[str, Any]:
        """Execute full scenario evaluation matrix across all 4 scenarios with repetitions."""
        scenario_configs: List[tuple[LabScenarioBase, bool]] = [
            (ScenarioAGateway(), False),
            (ScenarioBDNS(), False),
            (ScenarioCLoss(), False),  # Multi-target loss
            (ScenarioCLoss(), True),   # Single-target guardrail
            (ScenarioDCongestion(), False),
        ]

        results: List[EvaluationResult] = []
        for scenario, is_single_target in scenario_configs:
            for rep in range(repetitions_per_scenario):
                res = self.execute_scenario_trial(
                    scenario=scenario,
                    single_target_mode=is_single_target,
                )
                results.append(res)

        total = len(results)
        matches = sum(1 for r in results if r.verification_status == TrialVerificationStatus.MATCH)
        mismatches = sum(1 for r in results if r.verification_status == TrialVerificationStatus.MISMATCH)
        inconclusives = sum(1 for r in results if r.verification_status == TrialVerificationStatus.INCONCLUSIVE)

        return {
            "total_trials": total,
            "matches": matches,
            "mismatches": mismatches,
            "inconclusives": inconclusives,
            "accuracy_pct": (matches / total) * 100.0 if total > 0 else 0.0,
            "trials": [r.to_dict() for r in results],
        }
