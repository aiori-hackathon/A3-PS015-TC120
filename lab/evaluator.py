"""Automated Laboratory Diagnostic Evaluator.

Compares operational findings emitted by DiagnosisEngine against quarantined ground truth
in the lab_trials table, scoring outcomes deterministically without circular reasoning.

Outcome Classifications:
- MATCH: The engine correctly identified the injected fault condition with matching category & rule.
- MISMATCH: The engine fired an incorrect or conflicting rule.
- INCONCLUSIVE: The engine emitted inconclusive findings when evidence was ambiguous, or failed to detect the fault.
- INVALID_TRIAL: The test run failed or aborted before diagnostic evaluation.

Reference: 09_LAB_SAFETY_RUNBOOK.md, SRS-F-016, TC-LAB-01 through TC-LAB-06
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ticnet.models.entities import Finding, LabTrial
from ticnet.models.enums import FindingCategory, LabScenario, TrialCleanupStatus, TrialVerificationStatus
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.lab_repo import LabTrialRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from lab.ground_truth import LabGroundTruthManager


@dataclass
class EvaluationResult:
    """Detailed evaluation result comparing findings against lab ground truth."""

    trial_id: str
    run_id: str
    scenario_id: LabScenario
    ground_truth_label: str
    verification_status: TrialVerificationStatus
    cleanup_status: TrialCleanupStatus
    expected_rule_id: str
    expected_category: FindingCategory
    actual_findings: List[Finding]
    matched: bool
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id.value,
            "ground_truth_label": self.ground_truth_label,
            "verification_status": self.verification_status.value,
            "cleanup_status": self.cleanup_status.value,
            "expected_rule_id": self.expected_rule_id,
            "expected_category": self.expected_category.value,
            "actual_findings_count": len(self.actual_findings),
            "actual_rules_emitted": [f.rule_id for f in self.actual_findings],
            "actual_categories": [f.category.value for f in self.actual_findings],
            "matched": self.matched,
            "explanation": self.explanation,
        }


class LabEvaluator:
    """Evaluates completed diagnostic runs against independent ground truth."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.lab_gt = LabGroundTruthManager(db)
        self.finding_repo = FindingRepository(db)
        self.run_repo = RunRepository(db)

    def evaluate_trial(
        self,
        trial_id: str,
        expected_rule_id: str,
        expected_category: FindingCategory,
        cleanup_verified: bool = True,
    ) -> EvaluationResult:
        """Evaluate a specific laboratory trial against expected ground truth."""
        trial = self.lab_gt.get_trial(trial_id)
        if not trial:
            raise ValueError(f"Lab trial not found: {trial_id}")

        findings = self.finding_repo.get_findings_for_run(trial.run_id)
        cleanup_status = TrialCleanupStatus.CLEANED_VERIFIED if cleanup_verified else TrialCleanupStatus.CLEANUP_FAILED

        # Rule matching check
        emitted_rule_ids = {f.rule_id for f in findings}
        emitted_categories = {f.category for f in findings}

        matched = False
        status = TrialVerificationStatus.MISMATCH
        explanation = ""

        if expected_rule_id in emitted_rule_ids and expected_category in emitted_categories:
            matched = True
            status = TrialVerificationStatus.MATCH
            explanation = (
                f"Ground truth match: Rule '{expected_rule_id}' fired with category '{expected_category.value}', "
                f"satisfying ground truth expectation for {trial.scenario_id.value}."
            )
        elif FindingCategory.INCONCLUSIVE in emitted_categories and expected_category != FindingCategory.INCONCLUSIVE:
            # Engine emitted inconclusive instead of detecting the specific fault
            matched = False
            status = TrialVerificationStatus.INCONCLUSIVE
            explanation = (
                f"Inconclusive outcome: Expected rule '{expected_rule_id}' ({expected_category.value}), "
                f"but engine emitted INCONCLUSIVE."
            )
        else:
            # Wrong fault diagnosed or zero findings
            matched = False
            status = TrialVerificationStatus.MISMATCH
            explanation = (
                f"Diagnostic mismatch: Expected '{expected_rule_id}' ({expected_category.value}), "
                f"but emitted: {list(emitted_rule_ids)}."
            )

        # Update persisted trial record
        self.lab_gt.update_trial_status(
            trial_id=trial_id,
            verification_status=status,
            cleanup_status=cleanup_status,
            operator_notes=explanation,
        )

        return EvaluationResult(
            trial_id=trial_id,
            run_id=trial.run_id,
            scenario_id=trial.scenario_id,
            ground_truth_label=trial.ground_truth_label,
            verification_status=status,
            cleanup_status=cleanup_status,
            expected_rule_id=expected_rule_id,
            expected_category=expected_category,
            actual_findings=findings,
            matched=matched,
            explanation=explanation,
        )
