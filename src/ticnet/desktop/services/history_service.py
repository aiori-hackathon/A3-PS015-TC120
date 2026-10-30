"""History service adapter for TicNet desktop presentation layer.

Provides read access to stored diagnostic sessions, stage outcomes, and raw
observations using existing persistence repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ticnet.models.entities import Finding, Observation, Summary, TestRun, TestStage
from ticnet.persistence.database import DatabaseError, DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository
from ticnet.utils.logging import get_logger

logger = get_logger("desktop.service.history")


@dataclass(frozen=True)
class HistoricalRunDetail:
    """Consolidated historical run record with associated execution entities."""

    run: TestRun
    stages: List[TestStage]
    findings: List[Finding]
    summaries: List[Summary]
    observation_count: int


@dataclass(frozen=True)
class HistoricalSessionSummary:
    """Enriched historical session summary combining run, stage outcomes, and key metrics."""

    run_id: str
    start_time_utc: str
    end_time_utc: Optional[str]
    status: str
    gateway_ip: Optional[str]
    interface_name: Optional[str]
    overall_error: Optional[str]
    diagnosis: Optional[str]
    category: Optional[str]
    gateway_status: Optional[str]
    external_status: Optional[str]
    dns_status: Optional[str]
    packet_loss_pct: Optional[float]
    avg_latency_ms: Optional[float]
    gateway_latency_ms: Optional[float]
    throughput_mbps: Optional[float]
    loaded_latency_delta_ms: Optional[float]
    is_simulated: bool = False


class HistoryService:
    """Manages retrieval and filtering of historical diagnostic sessions."""

    def __init__(self, db: DatabaseManager) -> None:
        """Initialize the history service with database manager.

        Args:
            db: Configured DatabaseManager instance.
        """
        self.db = db
        self.run_repo = RunRepository(db)
        self.finding_repo = FindingRepository(db)
        self.obs_repo = ObservationRepository(db)
        self.summary_repo = SummaryRepository(db)

    def list_runs(self, limit: int = 1000, offset: int = 0) -> List[TestRun]:
        """Retrieve historical runs in reverse chronological order.

        Args:
            limit: Maximum records to return.
            offset: Record offset for pagination.

        Returns:
            List of TestRun entities.
        """
        try:
            return self.run_repo.list_runs(limit=limit, offset=offset)
        except Exception as exc:
            logger.error("Failed to query historical runs from database: %s", exc, exc_info=True)
            return []

    def get_run(self, run_id: str) -> Optional[TestRun]:
        """Retrieve a specific run by identifier.

        Args:
            run_id: Unique test run identifier.

        Returns:
            TestRun entity if found, otherwise None.
        """
        try:
            return self.run_repo.get_run(run_id)
        except Exception as exc:
            logger.error("Failed to fetch run %s: %s", run_id, exc, exc_info=True)
            return None

    def get_stages_for_run(self, run_id: str) -> List[TestStage]:
        """Retrieve all stages for a run in execution order.

        Args:
            run_id: Unique test run identifier.

        Returns:
            List of TestStage entities.
        """
        try:
            return self.run_repo.get_stages_for_run(run_id)
        except Exception as exc:
            logger.error("Failed to fetch stages for run %s: %s", run_id, exc, exc_info=True)
            return []

    def get_run_detail(self, run_id: str) -> Optional[HistoricalRunDetail]:
        """Assemble full historical detail for a run including stages and findings.

        Args:
            run_id: Unique test run identifier.

        Returns:
            HistoricalRunDetail dataclass instance if run exists, else None.
        """
        run = self.get_run(run_id)
        if not run:
            return None

        try:
            stages = self.run_repo.get_stages_for_run(run_id)
            findings = self.finding_repo.get_findings_for_run(run_id)
            summaries = self.summary_repo.get_summaries_for_run(run_id)
            observations = self.obs_repo.get_observations_for_run(run_id)

            return HistoricalRunDetail(
                run=run,
                stages=stages,
                findings=findings,
                summaries=summaries,
                observation_count=len(observations),
            )
        except Exception as exc:
            logger.error("Failed to assemble run detail for %s: %s", run_id, exc, exc_info=True)
            return None

    def get_observations_for_run(self, run_id: str) -> List[Observation]:
        """Retrieve all persisted empirical observations for a diagnostic session.

        Args:
            run_id: Unique test run identifier.

        Returns:
            List of Observation entities ordered chronologically by sample index.
        """
        try:
            return self.obs_repo.get_observations_for_run(run_id)
        except Exception as exc:
            logger.error("Failed to retrieve observations for run %s: %s", run_id, exc, exc_info=True)
            return []

    def get_summaries_for_run(self, run_id: str) -> List[Summary]:
        """Retrieve aggregated summary statistics for a diagnostic session.

        Args:
            run_id: Unique test run identifier.

        Returns:
            List of Summary statistical entities.
        """
        try:
            return self.summary_repo.get_summaries_for_run(run_id)
        except Exception as exc:
            logger.error("Failed to retrieve summaries for run %s: %s", run_id, exc, exc_info=True)
            return []


    def search_runs(self, query: str, limit: int = 100) -> List[TestRun]:
        """Search runs by query matching run_id, status, gateway_ip, or interface.

        Args:
            query: Substring to match.
            limit: Maximum matching records to return.

        Returns:
            Filtered list of TestRun entities.
        """
        all_runs = self.list_runs(limit=limit)
        q = query.strip().lower()
        if not q:
            return all_runs

        return [
            r
            for r in all_runs
            if (
                q in r.run_id.lower()
                or q in r.status.value.lower()
                or (r.gateway_ip and q in r.gateway_ip.lower())
                or (r.interface_name and q in r.interface_name.lower())
                or q in r.start_time_utc.lower()
            )
        ]

    def get_enriched_sessions(
        self,
        query: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        date_from_ist: Optional[str] = None,
        date_to_ist: Optional[str] = None,
        is_simulated: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[HistoricalSessionSummary]:
        """Retrieve enriched diagnostic sessions with stage outcomes, diagnosis, and metrics."""
        where_clauses: List[str] = []
        params: List[Any] = []

        if status and status.upper() != "ALL":
            where_clauses.append("r.status = ?")
            params.append(status.upper())

        if category and category.upper() != "ALL":
            where_clauses.append(
                "EXISTS (SELECT 1 FROM findings WHERE run_id = r.run_id AND category = ?)"
            )
            params.append(category.upper())

        if is_simulated is not None:
            if is_simulated == 1:
                where_clauses.append("r.is_simulated = 1")
            elif is_simulated == 0:
                where_clauses.append("(r.is_simulated = 0 OR r.is_simulated IS NULL)")

        if date_from_ist:
            where_clauses.append("DATE(DATETIME(r.start_time_utc, '+5 hours', '30 minutes')) >= ?")
            params.append(date_from_ist)

        if date_to_ist:
            where_clauses.append("DATE(DATETIME(r.start_time_utc, '+5 hours', '30 minutes')) <= ?")
            params.append(date_to_ist)

        if query and query.strip():
            q = f"%{query.strip().lower()}%"
            where_clauses.append(
                "(LOWER(r.run_id) LIKE ? OR "
                "LOWER(COALESCE(r.gateway_ip, '')) LIKE ? OR "
                "LOWER(COALESCE(r.interface_name, '')) LIKE ? OR "
                "EXISTS (SELECT 1 FROM findings WHERE run_id = r.run_id AND LOWER(plain_summary) LIKE ?))"
            )
            params.extend([q, q, q, q])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset > 0:
            limit_sql = "LIMIT -1 OFFSET ?"
            params.append(offset)

        sql = f"""
            SELECT 
                r.run_id,
                r.start_time_utc,
                r.end_time_utc,
                r.status,
                r.gateway_ip,
                r.interface_name,
                r.overall_error,
                COALESCE(r.is_simulated, 0) AS is_simulated,
                (SELECT plain_summary FROM findings WHERE run_id = r.run_id ORDER BY priority ASC, finding_id ASC LIMIT 1) AS primary_diagnosis,
                (SELECT category FROM findings WHERE run_id = r.run_id ORDER BY priority ASC, finding_id ASC LIMIT 1) AS primary_category,
                (SELECT status FROM test_stages WHERE run_id = r.run_id AND stage_order = 2 LIMIT 1) AS gw_stage_status,
                (SELECT status FROM test_stages WHERE run_id = r.run_id AND stage_order = 3 LIMIT 1) AS ext_stage_status,
                (SELECT status FROM test_stages WHERE run_id = r.run_id AND stage_order = 4 LIMIT 1) AS dns_stage_status,
                COALESCE(
                    (SELECT ROUND(AVG(value_numeric), 1) FROM summaries WHERE run_id = r.run_id AND metric_name = 'packet_loss' AND statistic_name = 'loss_percentage'),
                    (SELECT ROUND(AVG(value_numeric), 1) FROM summaries WHERE run_id = r.run_id AND metric_name = 'packet_loss' AND statistic_name = 'mean'),
                    (SELECT ROUND(100.0 * SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) / COUNT(*), 1) FROM observations WHERE run_id = r.run_id AND metric_name = 'round_trip_time')
                ) AS packet_loss_pct,
                COALESCE(
                    (SELECT ROUND(AVG(value_numeric), 1) FROM summaries WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND statistic_name IN ('median', 'mean') AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'EXTERNAL_IP')),
                    (SELECT ROUND(AVG(value_numeric), 1) FROM observations WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'EXTERNAL_IP') AND status = 'SUCCESS'),
                    (SELECT ROUND(AVG(value_numeric), 1) FROM observations WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND target_id NOT IN (SELECT target_id FROM targets WHERE target_type = 'GATEWAY') AND status = 'SUCCESS'),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND statistic_name IN ('median', 'mean') LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'latency' AND statistic_name IN ('median', 'mean') LIMIT 1),
                    (SELECT ROUND(AVG(value_numeric), 1) FROM observations WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND status = 'SUCCESS')
                ) AS avg_latency_ms,
                COALESCE(
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'GATEWAY') LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'gateway_latency' LIMIT 1),
                    (SELECT ROUND(AVG(value_numeric), 1) FROM observations WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'GATEWAY') AND status = 'SUCCESS')
                ) AS gw_latency_ms,
                COALESCE(
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'downstream_throughput' LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'download_speed' LIMIT 1),
                    (SELECT value_numeric FROM observations WHERE run_id = r.run_id AND metric_name = 'download_throughput' AND status = 'SUCCESS' LIMIT 1)
                ) AS throughput_mbps,
                COALESCE(
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'loaded_latency_delta' LIMIT 1),
                    (SELECT value_numeric FROM observations WHERE run_id = r.run_id AND metric_name = 'loaded_latency_delta' AND status = 'SUCCESS' LIMIT 1)
                ) AS loaded_latency_delta_ms
            FROM test_runs r
            {where_sql}
            ORDER BY r.start_time_utc DESC
            {limit_sql};
        """

        results: List[HistoricalSessionSummary] = []
        try:
            with self.db.connection() as conn:
                cursor = conn.execute(sql, params)
                for row in cursor.fetchall():
                    results.append(
                        HistoricalSessionSummary(
                            run_id=row["run_id"],
                            start_time_utc=row["start_time_utc"],
                            end_time_utc=row["end_time_utc"],
                            status=row["status"],
                            gateway_ip=row["gateway_ip"],
                            interface_name=row["interface_name"],
                            overall_error=row["overall_error"],
                            diagnosis=row["primary_diagnosis"],
                            category=row["primary_category"],
                            gateway_status=row["gw_stage_status"],
                            external_status=row["ext_stage_status"],
                            dns_status=row["dns_stage_status"],
                            packet_loss_pct=float(row["packet_loss_pct"]) if row["packet_loss_pct"] is not None else None,
                            avg_latency_ms=float(row["avg_latency_ms"]) if row["avg_latency_ms"] is not None else None,
                            gateway_latency_ms=float(row["gw_latency_ms"]) if row["gw_latency_ms"] is not None else None,
                            throughput_mbps=float(row["throughput_mbps"]) if row["throughput_mbps"] is not None else None,
                            loaded_latency_delta_ms=float(row["loaded_latency_delta_ms"]) if row["loaded_latency_delta_ms"] is not None else None,
                            is_simulated=bool(row["is_simulated"]),
                        )
                    )
        except Exception as exc:
            logger.error("Failed to query enriched historical sessions: %s", exc, exc_info=True)
        return results

    def get_historical_trends(
        self,
        limit: Optional[int] = None,
        date_from_ist: Optional[str] = None,
        date_to_ist: Optional[str] = None,
        is_simulated: Optional[int] = None,
    ) -> List[HistoricalSessionSummary]:
        """Retrieve historical sessions in ascending chronological order for trend charting."""
        sessions = self.get_enriched_sessions(
            limit=limit,
            offset=0,
            date_from_ist=date_from_ist,
            date_to_ist=date_to_ist,
            is_simulated=is_simulated,
        )
        # Return in chronological order (earliest to latest) for left-to-right charting
        return list(reversed(sessions))

