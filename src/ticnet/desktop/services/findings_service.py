"""Findings service adapter for TicNet desktop presentation layer.

Provides read access to explainable diagnostic conclusions, evidence links,
and action recommendations produced by DiagnosisEngine and RecommendationEngine.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ticnet.diagnosis.recommender import FindingRecommendation, RecommendationEngine
from ticnet.models.entities import Finding, FindingEvidence, Observation, Summary
from ticnet.models.enums import FindingPriority
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository
from ticnet.utils.logging import get_logger

logger = get_logger("desktop.service.findings")


class FindingsService:
    """Manages retrieval and formatting of explainable diagnostic findings and evidence."""

    def __init__(
        self,
        db: DatabaseManager,
        recommender: Optional[RecommendationEngine] = None,
    ) -> None:
        """Initialize the findings service with database manager.

        Args:
            db: Configured DatabaseManager instance.
            recommender: Optional RecommendationEngine override.
        """
        self.db = db
        self.finding_repo = FindingRepository(db)
        self.obs_repo = ObservationRepository(db)
        self.summary_repo = SummaryRepository(db)
        self.recommender = recommender or RecommendationEngine()

    def get_findings_for_run(self, run_id: str) -> List[Finding]:
        """Retrieve all diagnostic findings for a specific run.

        Findings are ordered by priority (CRITICAL -> WARNING -> INFO).

        Args:
            run_id: Unique test run identifier.

        Returns:
            List of Finding entities.
        """
        try:
            return self.finding_repo.get_findings_for_run(run_id)
        except Exception as exc:
            logger.error("Failed to query findings for run %s: %s", run_id, exc, exc_info=True)
            return []

    def get_evidence_for_finding(self, finding_id: str) -> List[FindingEvidence]:
        """Retrieve evidence link records for a specific diagnostic finding.

        Args:
            finding_id: Unique finding identifier.

        Returns:
            List of FindingEvidence junction entities.
        """
        try:
            return self.finding_repo.get_evidence_for_finding(finding_id)
        except Exception as exc:
            logger.error("Failed to query evidence for finding %s: %s", finding_id, exc, exc_info=True)
            return []

    def get_recommendations_for_run(self, run_id: str) -> List[FindingRecommendation]:
        """Generate prioritized action recommendations for all findings in a run.

        Args:
            run_id: Unique test run identifier.

        Returns:
            List of FindingRecommendation objects partitioned into user and technician actions.
        """
        findings = self.get_findings_for_run(run_id)
        if not findings:
            return []

        try:
            return self.recommender.generate_recommendations(findings)
        except Exception as exc:
            logger.error("Failed to generate recommendations for run %s: %s", run_id, exc, exc_info=True)
            return []

    def get_evidence_details(self, finding_id: str) -> List[Dict[str, Any]]:
        """Resolve evidence IDs to full observation or summary data.

        Args:
            finding_id: Unique finding identifier.

        Returns:
            List of dictionaries containing resolved observation or summary metrics.
        """
        evidence_links = self.get_evidence_for_finding(finding_id)
        results: List[Dict[str, Any]] = []

        for ev in evidence_links:
            item: Dict[str, Any] = {
                "evidence_id": ev.evidence_id,
                "relationship": ev.relationship_type.value,
            }

            if ev.observation_id:
                obs: Optional[Observation] = self.obs_repo.get_observation(ev.observation_id)
                if obs:
                    item.update({
                        "type": "OBSERVATION",
                        "metric_name": obs.metric_name,
                        "value_numeric": obs.value_numeric,
                        "unit": obs.unit,
                        "status": obs.status.value,
                        "sample_index": obs.sample_index,
                        "timestamp_utc": obs.timestamp_utc,
                    })
            elif ev.summary_id:
                sum_rec: Optional[Summary] = self.summary_repo.get_summary(ev.summary_id)
                if sum_rec:
                    item.update({
                        "type": "SUMMARY",
                        "metric_name": sum_rec.metric_name,
                        "statistic_name": sum_rec.statistic_name,
                        "value_numeric": sum_rec.value_numeric,
                        "unit": sum_rec.unit,
                        "valid_samples": sum_rec.sample_count_valid,
                        "total_samples": sum_rec.sample_count_total,
                    })

            results.append(item)

        return results
