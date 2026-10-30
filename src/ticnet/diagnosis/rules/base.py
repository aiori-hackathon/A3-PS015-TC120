"""Base abstractions for deterministic diagnostic rules.

Every diagnostic rule evaluates empirical measurements in a DiagnosisContext and
returns a structured RuleEvaluationResult with evidence links, limitations, and
recommended actions, or None if the trigger condition is not met.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from ticnet.diagnosis.context import DiagnosisContext
from ticnet.models.entities import Finding, FindingEvidence, new_id
from ticnet.models.enums import (
    EvidenceRelationship,
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
)


@dataclass
class RuleEvaluationResult:
    """Result emitted when a diagnostic rule condition is triggered."""

    finding: Finding
    evidence: List[FindingEvidence] = field(default_factory=list)


class BaseDiagnosticRule(ABC):
    """Abstract base class for all deterministic diagnostic rules."""

    @property
    @abstractmethod
    def rule_id(self) -> str:
        """Canonical rule identifier (e.g. 'R-GW-01')."""
        pass

    @property
    @abstractmethod
    def rule_version(self) -> str:
        """Version string of the rule logic and thresholds (e.g. '1.0.0')."""
        pass

    @property
    @abstractmethod
    def category(self) -> FindingCategory:
        """Failure domain category for the emitted finding."""
        pass

    @property
    @abstractmethod
    def priority(self) -> FindingPriority:
        """Triage priority for the emitted finding."""
        pass

    @abstractmethod
    def evaluate(self, ctx: DiagnosisContext) -> Optional[RuleEvaluationResult]:
        """Evaluate the rule against the diagnosis context.

        Returns:
            RuleEvaluationResult if the trigger condition is satisfied,
            or None if conditions are not met or exclusion conditions apply.
        """
        pass

    def create_finding(
        self,
        run_id: str,
        plain_summary: str,
        limitations: str,
        recommended_action: str,
        status: FindingStatus = FindingStatus.SUPPORTED,
        evidence_strength: EvidenceStrength = EvidenceStrength.STRONG,
        priority: Optional[FindingPriority] = None,
        category: Optional[FindingCategory] = None,
    ) -> Finding:
        """Factory helper to construct a typed Finding object."""
        return Finding(
            finding_id=new_id(),
            run_id=run_id,
            category=category or self.category,
            priority=priority or self.priority,
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            status=status,
            evidence_strength=evidence_strength,
            plain_summary=plain_summary,
            limitations=limitations,
            recommended_action=recommended_action,
        )

    @staticmethod
    def create_evidence_link(
        finding_id: str,
        relationship_type: EvidenceRelationship,
        observation_id: Optional[str] = None,
        summary_id: Optional[str] = None,
    ) -> FindingEvidence:
        """Factory helper to construct a typed FindingEvidence link."""
        return FindingEvidence(
            evidence_id=new_id(),
            finding_id=finding_id,
            relationship_type=relationship_type,
            observation_id=observation_id,
            summary_id=summary_id,
        )
