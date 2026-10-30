"""Repository for Finding and FindingEvidence persistence."""

import sqlite3
from typing import List, Optional

from ticnet.models.entities import Finding, FindingEvidence
from ticnet.models.enums import (
    FindingCategory,
    FindingPriority,
    FindingStatus,
    EvidenceStrength,
    EvidenceRelationship,
)
from ticnet.persistence.database import DatabaseManager


class FindingRepository:
    """Manages persistence and retrieval of diagnostic Findings and supporting FindingEvidence."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_finding(self, finding: Finding) -> Finding:
        """Persist a single diagnostic finding."""
        sql = """
            INSERT INTO findings (
                finding_id, run_id, category, priority, rule_id, rule_version,
                status, evidence_strength, plain_summary, limitations,
                recommended_action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    finding.finding_id,
                    finding.run_id,
                    finding.category.value,
                    finding.priority.value,
                    finding.rule_id,
                    finding.rule_version,
                    finding.status.value,
                    finding.evidence_strength.value,
                    finding.plain_summary,
                    finding.limitations,
                    finding.recommended_action,
                ),
            )
        return finding

    def get_finding(self, finding_id: str) -> Optional[Finding]:
        """Retrieve a finding by its identifier."""
        sql = "SELECT * FROM findings WHERE finding_id = ?;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (finding_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_finding(row)

    def get_findings_for_run(self, run_id: str) -> List[Finding]:
        """Retrieve all diagnostic findings for a run."""
        sql = "SELECT * FROM findings WHERE run_id = ? ORDER BY priority ASC, finding_id ASC;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (run_id,))
            return [self._row_to_finding(row) for row in cursor.fetchall()]

    def link_evidence(self, evidence: FindingEvidence) -> FindingEvidence:
        """Link an observation or summary to a diagnostic finding."""
        sql = """
            INSERT INTO finding_evidence (
                evidence_id, finding_id, observation_id, summary_id, relationship_type
            ) VALUES (?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    evidence.evidence_id,
                    evidence.finding_id,
                    evidence.observation_id,
                    evidence.summary_id,
                    evidence.relationship_type.value,
                ),
            )
        return evidence

    def link_evidence_batch(self, evidence_list: List[FindingEvidence]) -> List[FindingEvidence]:
        """Link multiple evidence records in a single atomic transaction."""
        if not evidence_list:
            return []

        sql = """
            INSERT INTO finding_evidence (
                evidence_id, finding_id, observation_id, summary_id, relationship_type
            ) VALUES (?, ?, ?, ?, ?);
        """
        params = [
            (
                e.evidence_id,
                e.finding_id,
                e.observation_id,
                e.summary_id,
                e.relationship_type.value,
            )
            for e in evidence_list
        ]
        with self.db.transaction() as conn:
            conn.executemany(sql, params)
        return evidence_list

    def get_evidence_for_finding(self, finding_id: str) -> List[FindingEvidence]:
        """Retrieve all evidence links for a specific finding."""
        sql = "SELECT * FROM finding_evidence WHERE finding_id = ? ORDER BY evidence_id ASC;"
        with self.db.connection() as conn:
            cursor = conn.execute(sql, (finding_id,))
            return [self._row_to_evidence(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_finding(row: sqlite3.Row) -> Finding:
        return Finding(
            finding_id=row["finding_id"],
            run_id=row["run_id"],
            category=FindingCategory(row["category"]),
            priority=FindingPriority(row["priority"]),
            rule_id=row["rule_id"],
            rule_version=row["rule_version"],
            status=FindingStatus(row["status"]),
            evidence_strength=EvidenceStrength(row["evidence_strength"]),
            plain_summary=row["plain_summary"],
            limitations=row["limitations"],
            recommended_action=row["recommended_action"],
        )

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> FindingEvidence:
        return FindingEvidence(
            evidence_id=row["evidence_id"],
            finding_id=row["finding_id"],
            relationship_type=EvidenceRelationship(row["relationship_type"]),
            observation_id=row["observation_id"],
            summary_id=row["summary_id"],
        )
