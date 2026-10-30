"""Explainable Diagnosis Engine for TicNet.

Pure functional rule evaluation pipeline that takes empirical observations,
statistical summaries, targets, and thresholds, and produces deterministic,
evidence-linked diagnostic findings without black-box ML or LLMs.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ticnet.configuration.loader import load_thresholds_config
from ticnet.configuration.validator import ThresholdsConfig
from ticnet.diagnosis.context import DiagnosisContext
from ticnet.diagnosis.rules.base import BaseDiagnosticRule, RuleEvaluationResult
from ticnet.diagnosis.rules.bufferbloat import BufferbloatRule
from ticnet.diagnosis.rules.dns import DNSSubsystemFailureRule
from ticnet.diagnosis.rules.gateway import GatewayUnreachableRule
from ticnet.diagnosis.rules.inconclusive import InconclusiveRule
from ticnet.diagnosis.rules.local import LocalNetworkDegradationRule
from ticnet.diagnosis.rules.loss import ExternalPathDegradationRule
from ticnet.models.entities import Finding, FindingEvidence, Observation, Summary, Target
from ticnet.models.enums import FindingPriority
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository
from ticnet.utils.logging import get_logger

logger = get_logger("diagnosis.engine")


class DiagnosisEngine:
    """Deterministic, explainable diagnostic rule evaluation engine.
    
    Invariants:
    1. Zero black-box ML / LLMs: All findings are produced by transparent rule logic.
    2. Bidirectional Evidence Linking: Every emitted finding links to explicit observation
       or summary identifiers.
    3. Single-Target Failure Guardrail: Single external destination failure never triggers
       R-LOSS-01; it triggers R-INCONCLUSIVE-01.
    4. Deterministic Priority Triage: Rules are evaluated in deterministic priority order.
    """

    def __init__(
        self,
        rules: Optional[List[BaseDiagnosticRule]] = None,
        thresholds: Optional[ThresholdsConfig] = None,
    ) -> None:
        """Initialize the diagnosis engine with default or custom diagnostic rules."""
        if rules is not None:
            self._rules = list(rules)
        else:
            self._rules = [
                GatewayUnreachableRule(),
                DNSSubsystemFailureRule(),
                ExternalPathDegradationRule(),
                LocalNetworkDegradationRule(),
                BufferbloatRule(),
                InconclusiveRule(),
            ]

        self._thresholds = thresholds

    @property
    def rules(self) -> List[BaseDiagnosticRule]:
        """Return registered diagnostic rules in evaluation order."""
        return list(self._rules)

    def evaluate(self, ctx: DiagnosisContext) -> List[RuleEvaluationResult]:
        """Pure functional evaluation of diagnostic rules against evidence context.

        Args:
            ctx: DiagnosisContext containing observations, summaries, targets, and thresholds.

        Returns:
            List of triggered RuleEvaluationResult objects containing Findings and Evidence links.
        """
        # Ensure thresholds are attached to context
        if ctx.thresholds is None and self._thresholds is not None:
            ctx.thresholds = self._thresholds
        elif ctx.thresholds is None:
            try:
                ctx.thresholds = load_thresholds_config()
            except Exception as e:
                logger.warning("Could not load default thresholds for diagnosis: %s", e)

        # Priority order: CRITICAL -> WARNING -> INFO
        priority_order = {
            FindingPriority.CRITICAL: 1,
            FindingPriority.WARNING: 2,
            FindingPriority.INFO: 3,
        }

        sorted_rules = sorted(
            self._rules,
            key=lambda r: (priority_order.get(r.priority, 99), r.rule_id),
        )

        results: List[RuleEvaluationResult] = []
        rule_ids_triggered: set[str] = set()

        for rule in sorted_rules:
            # If gateway is completely unreachable (R-GW-01), skip local degradation or bufferbloat
            if "R-GW-01" in rule_ids_triggered and rule.rule_id in ("R-LOCAL-01", "R-LOAD-01", "R-LOSS-01"):
                logger.debug("Skipping %s because Gateway Unreachable (R-GW-01) is active", rule.rule_id)
                continue

            try:
                res = rule.evaluate(ctx)
                if res is not None:
                    results.append(res)
                    rule_ids_triggered.add(rule.rule_id)
                    logger.info("Rule triggered: %s (%s)", rule.rule_id, res.finding.category.value)
            except Exception as rule_err:
                logger.error("Error evaluating rule %s: %s", rule.rule_id, rule_err, exc_info=True)

        return results

    def evaluate_run(
        self,
        run_id: str,
        db: DatabaseManager,
        thresholds: Optional[ThresholdsConfig] = None,
    ) -> List[Finding]:
        """Evaluate stored evidence for a run and persist findings and evidence to SQLite.

        Args:
            run_id: Unique test run identifier.
            db: DatabaseManager instance.
            thresholds: Optional override thresholds configuration.

        Returns:
            List of persisted Finding entities.
        """
        obs_repo = ObservationRepository(db)
        summary_repo = SummaryRepository(db)
        finding_repo = FindingRepository(db)

        observations: List[Observation] = obs_repo.get_observations_for_run(run_id)
        summaries: List[Summary] = summary_repo.get_summaries_for_run(run_id)
        target_list: List[Target] = obs_repo.list_targets(active_only=False)
        run_target_ids = {o.target_id for o in observations if o.target_id} | {s.target_id for s in summaries if s.target_id}
        targets_by_id: Dict[str, Target] = {
            t.target_id: t for t in target_list if not run_target_ids or t.target_id in run_target_ids
        }

        active_thresholds = thresholds or self._thresholds
        if active_thresholds is None:
            try:
                active_thresholds = load_thresholds_config()
            except Exception as e:
                logger.warning("Could not load default thresholds: %s", e)

        ctx = DiagnosisContext(
            run_id=run_id,
            observations=observations,
            summaries=summaries,
            targets=targets_by_id,
            thresholds=active_thresholds,
        )

        evaluation_results = self.evaluate(ctx)
        persisted_findings: List[Finding] = []

        for eval_res in evaluation_results:
            finding = finding_repo.create_finding(eval_res.finding)
            persisted_findings.append(finding)
            if eval_res.evidence:
                finding_repo.link_evidence_batch(eval_res.evidence)

        logger.info(
            "Diagnosis evaluation complete for run %s: %d findings persisted",
            run_id,
            len(persisted_findings),
        )
        return persisted_findings
