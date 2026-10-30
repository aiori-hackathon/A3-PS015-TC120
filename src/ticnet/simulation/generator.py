"""Historical network monitoring dataset generator and fault injection engine for TicNet.

Produces reproducible, explainable historical network diagnostic runs conforming to:
- Timezone: Indian Standard Time (IST — Asia/Kolkata, UTC+05:30)
- Target Date Range: 10 October 2026 to 29 October 2026 (inclusive, 20 days)
- Daily Distribution: 27 to 32 runs per calendar day (total >= 540 runs)
- Controlled Fault Injections: Faults A, B, C, D, E, F
- Full Stage 2 Analytics and Stage 3 Incident Management integration
- Strict Data Isolation and Idempotence via dedicated provenance dataset identifier
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
import random
from typing import Dict, List, Optional, Tuple
import uuid

from ticnet.core.timezone import IST, format_ist, now_ist, now_ist_iso, to_ist
from ticnet.models.entities import Finding, Observation, Summary, TestRun, TestStage
from ticnet.models.enums import FindingPriority, RunMode, RunStatus, StageStatus
from ticnet.models.monitoring import (
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringTriggerType,
)
from ticnet.models.notifications import (
    IncidentSeverity,
    IncidentStatus,
    NetworkIncident,
    NotificationPayload,
    NotificationPreferences,
)
from ticnet.monitoring.analytics import (
    AnalyticsEngine,
    DetectionStatus,
    SlowdownEvent,
    SlowdownReport,
    TimeWindow,
)
from ticnet.monitoring.incident_manager import IncidentManager
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.analytics_repo import AnalyticsRepository
from ticnet.persistence.repositories.incident_repo import IncidentRepository
from ticnet.persistence.repositories.monitoring_repo import MonitoringRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository
from ticnet.simulation.fault_profiles import (
    NetworkProfileMeasurement,
    SIMULATION_FAULTS,
    FaultWindow,
)
from ticnet.utils.logging import get_logger

logger = get_logger("simulation.generator")

SIMULATION_DATASET_ID = "SIM-202610-IST"
DEFAULT_SIM_SEED = 202610


@dataclass
class SimulationSummary:
    """Summary metrics of the completed historical simulation run."""

    dataset_id: str
    seed: int
    total_runs: int
    daily_run_counts: Dict[str, int]
    total_measurements: int
    suspected_slowdowns: int
    confirmed_slowdowns: int
    recoveries: int
    total_incidents: int
    open_incidents: int
    resolved_incidents: int
    start_date_ist: str
    end_date_ist: str


class NetworkSimulationEngine:
    """Generates authentic historical runs, fault injections, and analytics events."""

    def __init__(
        self,
        db: DatabaseManager,
        seed: int = DEFAULT_SIM_SEED,
        dataset_id: str = SIMULATION_DATASET_ID,
    ) -> None:
        self.db = db
        self.seed = seed
        self.dataset_id = dataset_id
        self.rng = random.Random(seed)

        self.run_repo = RunRepository(db)
        self.monitoring_repo = MonitoringRepository(db)
        self.analytics_repo = AnalyticsRepository(db)
        self.incident_repo = IncidentRepository(db)
        self.summary_repo = SummaryRepository(db)
        self.observation_repo = ObservationRepository(db)

        # Simulation-safe incident manager (never sends real OS toast notifications)
        self.incident_manager = IncidentManager(
            incident_repo=self.incident_repo,
            notification_dispatcher=lambda payload, prefs: None,
        )

    def run_simulation(self) -> SimulationSummary:
        """Execute full historical simulation from 10 Oct to 29 Oct 2026.

        Idempotent: removes any previous simulation records under this dataset ID
        prior to insertion.
        """
        logger.info("Beginning TicNet Historical Simulation (Dataset: %s, Seed: %d)", self.dataset_id, self.seed)

        # 1. Clean previous simulation records for idempotence
        self._cleanup_previous_simulation()

        # 2. Plan daily run counts (27 to 32 per day across 20 days)
        # 10 Oct 2026 to 29 Oct 2026
        start_d = date(2026, 10, 10)
        end_d = date(2026, 10, 29)
        day_count = (end_d - start_d).days + 1  # Exactly 20 days

        # Deterministic varied daily run counts between 27 and 32
        daily_counts: Dict[date, int] = {
            date(2026, 10, 10): 28,
            date(2026, 10, 11): 29,
            date(2026, 10, 12): 30,
            date(2026, 10, 13): 31,  # Fault A (Latency)
            date(2026, 10, 14): 28,  # Recovery A
            date(2026, 10, 15): 29,
            date(2026, 10, 16): 30,  # Fault B (Jitter)
            date(2026, 10, 17): 27,  # Recovery B
            date(2026, 10, 18): 32,
            date(2026, 10, 19): 28,
            date(2026, 10, 20): 30,  # Fault C (Packet loss)
            date(2026, 10, 21): 29,  # Recovery C
            date(2026, 10, 22): 31,  # Fault D (Throughput drop)
            date(2026, 10, 23): 28,  # Recovery D
            date(2026, 10, 24): 30,  # Fault E (Multi-metric storm)
            date(2026, 10, 25): 29,  # Recovery E
            date(2026, 10, 26): 31,
            date(2026, 10, 27): 28,
            date(2026, 10, 28): 30,
            date(2026, 10, 29): 29,
        }

        total_planned_runs = sum(daily_counts.values())
        logger.info("Scheduled %d runs across %d days (all dates 27-32 runs)", total_planned_runs, day_count)

        # 3. Generate chronological timestamps
        scheduled_runs: List[Tuple[datetime, NetworkProfileMeasurement, str]] = []
        run_seq = 1

        for cur_date, num_runs in sorted(daily_counts.items()):
            # Distribute runs throughout the 24 hours (approx every 40-55 mins with jitter)
            # 86400 seconds in a day
            interval_sec = 86400.0 / float(num_runs)
            for i in range(num_runs):
                base_sec = i * interval_sec
                jitter_sec = self.rng.uniform(-interval_sec * 0.25, interval_sec * 0.25)
                sec_in_day = max(60.0, min(86340.0, base_sec + jitter_sec))

                run_time_ist = datetime(
                    cur_date.year, cur_date.month, cur_date.day,
                    tzinfo=IST,
                ) + timedelta(seconds=sec_in_day)

                # Generate correlated measurement
                meas = self._generate_measurement(run_time_ist)
                run_id = f"TCN-{cur_date.strftime('%Y%m%d')}-{run_seq:06d}"
                run_seq += 1

                scheduled_runs.append((run_time_ist, meas, run_id))

        # Sort strictly chronologically
        scheduled_runs.sort(key=lambda item: item[0])

        # 4. Sequential Simulation and Pipeline Processing
        history_monitoring_runs: List[MonitoringRun] = []
        suspected_count = 0
        confirmed_count = 0
        recovery_count = 0
        total_measurements = 0

        # Register dataset in registry table
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO simulation_datasets (
                    dataset_id, name, description, random_seed,
                    start_date_ist, end_date_ist, total_runs, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    self.dataset_id,
                    "Historical Network Simulation (10-29 Oct 2026)",
                    "Controlled historical dataset with Faults A-F, Stage 2 detection and Stage 3 incidents",
                    self.seed,
                    "2026-10-10",
                    "2026-10-29",
                    len(scheduled_runs),
                    now_ist_iso(),
                ),
            )

        for run_time_ist, meas, run_id in scheduled_runs:
            mon_run = self._persist_run_bundle(run_time_ist, meas, run_id)
            history_monitoring_runs.append(mon_run)
            total_measurements += 4  # Latency, Jitter, Loss, Throughput

            # Run Stage 2 Analytics over rolling window
            # Lookback up to 7 days of runs prior to and including this run
            window_runs = [
                r for r in history_monitoring_runs
                if (to_ist(r.scheduled_for_utc) >= (run_time_ist - timedelta(days=7)))
                and (to_ist(r.scheduled_for_utc) <= run_time_ist)
            ]

            if len(window_runs) >= 3:
                # Query previous slowdown events for this window
                report: SlowdownReport = AnalyticsEngine.evaluate_runs(
                    runs=window_runs,
                    window=TimeWindow.H24,
                )

                # Persist SlowdownEvents for any non-normal metric evaluation
                for m_name, evaluation in report.evaluations.items():
                    if evaluation.status != DetectionStatus.NORMAL and evaluation.status != DetectionStatus.INSUFFICIENT_DATA:
                        if evaluation.status == DetectionStatus.SUSPECTED_SLOWDOWN:
                            suspected_count += 1
                        elif evaluation.status == DetectionStatus.CONFIRMED_SLOWDOWN:
                            confirmed_count += 1
                        elif evaluation.status == DetectionStatus.CONFIRMED_RECOVERY:
                            recovery_count += 1

                        ev = SlowdownEvent(
                            event_id=str(uuid.uuid4()),
                            monitoring_run_id=mon_run.monitoring_run_id,
                            metric_name=m_name,
                            detection_status=evaluation.status.value,
                            baseline_value=evaluation.baseline_value,
                            observed_value=evaluation.observed_value,
                            deviation_pct=evaluation.deviation_pct,
                            threshold_applied=evaluation.threshold_applied,
                            time_window="24H",
                            consecutive_breaches=evaluation.consecutive_breaches,
                            explanation=evaluation.explanation,
                            detected_at_utc=mon_run.scheduled_for_utc,
                        )
                        self._save_simulated_slowdown_event(ev)

                # Pass to Stage 3 Incident Manager for incident creation, escalation, and resolution
                self.incident_manager.process_slowdown_report(
                    report=report,
                    monitoring_run_id=mon_run.monitoring_run_id,
                    now_utc=run_time_ist,
                )

        # Count incidents
        with self.db.connection() as conn:
            tot_inc = conn.execute(
                "SELECT COUNT(*) FROM network_incidents WHERE simulation_dataset_id = ?;",
                (self.dataset_id,),
            ).fetchone()[0]
            open_inc = conn.execute(
                "SELECT COUNT(*) FROM network_incidents WHERE simulation_dataset_id = ? AND status = 'OPEN';",
                (self.dataset_id,),
            ).fetchone()[0]
            res_inc = conn.execute(
                "SELECT COUNT(*) FROM network_incidents WHERE simulation_dataset_id = ? AND status = 'RESOLVED';",
                (self.dataset_id,),
            ).fetchone()[0]

        daily_str_counts = {d.strftime("%Y-%m-%d"): cnt for d, cnt in daily_counts.items()}

        summary = SimulationSummary(
            dataset_id=self.dataset_id,
            seed=self.seed,
            total_runs=len(scheduled_runs),
            daily_run_counts=daily_str_counts,
            total_measurements=total_measurements,
            suspected_slowdowns=suspected_count,
            confirmed_slowdowns=confirmed_count,
            recoveries=recovery_count,
            total_incidents=tot_inc,
            open_incidents=open_inc,
            resolved_incidents=res_inc,
            start_date_ist="2026-10-10",
            end_date_ist="2026-10-29",
        )

        logger.info(
            "Historical simulation complete: %d runs generated across %d days. "
            "(Slowdowns: %d confirmed, %d suspected, %d recoveries. Incidents: %d total, %d resolved)",
            summary.total_runs,
            day_count,
            summary.confirmed_slowdowns,
            summary.suspected_slowdowns,
            summary.recoveries,
            summary.total_incidents,
            summary.resolved_incidents,
        )

        return summary

    def _cleanup_previous_simulation(self) -> None:
        """Remove previously generated simulation data cleanly and idempotently."""
        with self.db.transaction() as conn:
            # Delete in child-to-parent order
            conn.execute("DELETE FROM network_incidents WHERE simulation_dataset_id = ?;", (self.dataset_id,))
            conn.execute("DELETE FROM slowdown_events WHERE simulation_dataset_id = ?;", (self.dataset_id,))
            conn.execute("DELETE FROM findings WHERE run_id IN (SELECT run_id FROM test_runs WHERE simulation_dataset_id = ?);", (self.dataset_id,))
            conn.execute("DELETE FROM summaries WHERE run_id IN (SELECT run_id FROM test_runs WHERE simulation_dataset_id = ?);", (self.dataset_id,))
            conn.execute("DELETE FROM observations WHERE run_id IN (SELECT run_id FROM test_runs WHERE simulation_dataset_id = ?);", (self.dataset_id,))
            conn.execute("DELETE FROM test_stages WHERE run_id IN (SELECT run_id FROM test_runs WHERE simulation_dataset_id = ?);", (self.dataset_id,))
            conn.execute("DELETE FROM monitoring_runs WHERE simulation_dataset_id = ?;", (self.dataset_id,))
            conn.execute("DELETE FROM test_runs WHERE simulation_dataset_id = ?;", (self.dataset_id,))
            conn.execute("DELETE FROM simulation_datasets WHERE dataset_id = ?;", (self.dataset_id,))

    def _generate_measurement(self, t_ist: datetime) -> NetworkProfileMeasurement:
        """Calculate realistic correlated measurements taking active faults into account."""
        time_str = t_ist.strftime("%Y-%m-%dT%H:%M:%S")

        # 1. Base nominal performance (high-quality broadband in India: ~60 Mbps down, ~24ms latency)
        # Diurnal pattern: slightly higher latency in evening peak hours (19:00 - 22:30 IST)
        hour = t_ist.hour + t_ist.minute / 60.0
        peak_factor = 1.0 + (0.25 * math.exp(-((hour - 20.5) ** 2) / 8.0))

        nominal_latency = self.rng.gauss(23.5 * peak_factor, 1.8)
        nominal_gw_latency = self.rng.gauss(2.2, 0.4)
        nominal_jitter = self.rng.gauss(1.6 * peak_factor, 0.3)
        nominal_loss = 0.0 if self.rng.random() > 0.03 else self.rng.uniform(0.1, 0.4)
        nominal_download = self.rng.gauss(60.0 / peak_factor, 3.5)
        nominal_upload = self.rng.gauss(22.0 / peak_factor, 2.0)

        # 2. Check active faults
        active_fault: Optional[FaultWindow] = None
        progression = 0.0

        for f in SIMULATION_FAULTS:
            if f.start_time_ist <= time_str <= f.end_time_ist:
                active_fault = f
                # Gaussian bell curve progression centered at peak_time_ist
                t_peak = datetime.fromisoformat(f.peak_time_ist).replace(tzinfo=IST)
                diff_hours = abs((t_ist - t_peak).total_seconds()) / 3600.0
                progression = math.exp(-(diff_hours ** 2) / 1.8)
                break

        # 3. Apply fault perturbations
        if active_fault:
            if active_fault.metric in ("latency", "all"):
                # Fault A or E: Latency surge (+40ms to +55ms above baseline)
                nominal_latency += (45.0 * progression) + self.rng.uniform(-3.0, 5.0)

            if active_fault.metric in ("jitter", "all"):
                # Fault B or E: High jitter variation (+7ms to +12ms)
                nominal_jitter += (9.5 * progression) + self.rng.uniform(-1.0, 2.0)

            if active_fault.metric in ("packet_loss", "all"):
                # Fault C or E: Packet loss (4% to 8%)
                nominal_loss = max(nominal_loss, (6.5 * progression) + self.rng.uniform(-0.5, 1.2))

            if active_fault.metric in ("download_speed", "all"):
                # Fault D or E: Throughput collapse (drop down to ~15-20 Mbps)
                nominal_download = max(10.0, nominal_download - (42.0 * progression) + self.rng.uniform(-2.0, 2.0))

        # Clamp to realistic, physical bounds
        lat = round(max(5.0, nominal_latency), 1)
        gw_lat = round(max(0.5, nominal_gw_latency), 1)
        jit = round(max(0.2, nominal_jitter), 1)
        loss = round(max(0.0, min(100.0, nominal_loss)), 1)
        dl = round(max(1.0, nominal_download), 2)
        ul = round(max(0.5, nominal_upload), 2)
        duration = round(self.rng.uniform(9.0, 14.5), 1)

        return NetworkProfileMeasurement(
            latency_ms=lat,
            gateway_latency_ms=gw_lat,
            jitter_ms=jit,
            packet_loss_pct=loss,
            download_mbps=dl,
            upload_mbps=ul,
            duration_s=duration,
            status="COMPLETED",
            fault_label=active_fault.fault_id if active_fault else None,
            fault_description=active_fault.description if active_fault else None,
        )

    def _persist_run_bundle(
        self,
        t_ist: datetime,
        meas: NetworkProfileMeasurement,
        run_id: str,
    ) -> MonitoringRun:
        """Persist full run entity bundle across SQLite tables with simulation tags."""
        start_iso = t_ist.isoformat()
        end_iso = (t_ist + timedelta(seconds=meas.duration_s)).isoformat()
        mon_run_id = str(uuid.uuid4())

        with self.db.transaction() as conn:
            # 1. test_runs
            conn.execute(
                """
                INSERT INTO test_runs (
                    run_id, start_time_utc, end_time_utc, status, run_mode,
                    app_version, config_version, interface_name, interface_type,
                    gateway_ip, overall_error, is_simulated, simulation_dataset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    start_iso,
                    end_iso,
                    meas.status,
                    "OPERATIONAL",
                    "1.0.0",
                    "1.0.0",
                    "Wi-Fi (Intel Wi-Fi 6 AX201)",
                    "WLAN",
                    "192.168.1.1",
                    None,
                    1,
                    self.dataset_id,
                ),
            )

            # 2. monitoring_runs
            conn.execute(
                """
                INSERT INTO monitoring_runs (
                    monitoring_run_id, run_id, schedule_id, trigger_type,
                    scheduled_for_utc, started_at_utc, completed_at_utc,
                    status, skip_reason, error_message,
                    download_mbps, upload_mbps, latency_ms, jitter_ms, packet_loss_pct,
                    is_simulated, simulation_dataset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    mon_run_id,
                    run_id,
                    "default",
                    "SCHEDULED",
                    start_iso,
                    start_iso,
                    end_iso,
                    meas.status,
                    None,
                    None,
                    meas.download_mbps,
                    meas.upload_mbps,
                    meas.latency_ms,
                    meas.jitter_ms,
                    meas.packet_loss_pct,
                    1,
                    self.dataset_id,
                ),
            )

            # 3. test_stages
            stages = [
                ("stage-gw-" + run_id, "Local Gateway", 1),
                ("stage-ext-" + run_id, "External Internet", 2),
                ("stage-dns-" + run_id, "DNS Resolution", 3),
                ("stage-tp-" + run_id, "Throughput", 4),
            ]
            for s_id, s_name, s_order in stages:
                conn.execute(
                    """
                    INSERT INTO test_stages (
                        stage_id, run_id, stage_name, stage_order,
                        start_time_utc, end_time_utc, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (s_id, run_id, s_name, s_order, start_iso, end_iso, "SUCCESS"),
                )

            # 4. summaries (matching both analytics and history repo naming)
            metric_summaries = [
                ("latency", "mean", meas.latency_ms, "ms"),
                ("round_trip_time", "mean", meas.latency_ms, "ms"),
                ("round_trip_time", "median", meas.latency_ms, "ms"),
                ("latency", "min", max(5.0, meas.latency_ms - 2.0), "ms"),
                ("latency", "max", meas.latency_ms + 4.5, "ms"),
                ("jitter", "mean", meas.jitter_ms, "ms"),
                ("packet_loss", "loss_percentage", meas.packet_loss_pct, "%"),
                ("packet_loss", "mean", meas.packet_loss_pct, "%"),
                ("download_speed", "mean", meas.download_mbps, "Mbps"),
                ("downstream_throughput", "mean", meas.download_mbps, "Mbps"),
                ("gateway_latency", "mean", meas.gateway_latency_ms, "ms"),
            ]
            for m_name, stat_name, val, unit in metric_summaries:
                conn.execute(
                    """
                    INSERT INTO summaries (
                        summary_id, run_id, metric_name, statistic_name,
                        value_numeric, unit, sample_count_total, sample_count_valid,
                        calculation_method, calculation_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        m_name,
                        stat_name,
                        val,
                        unit,
                        10,
                        10,
                        "arithmetic_mean",
                        "1.0",
                    ),
                )

            # 5. findings (only for fault events or anomalies)
            if meas.fault_label:
                cat = "EXTERNAL_PATH"
                prio = "CRITICAL" if meas.fault_label in ("FAULT-E", "FAULT-C") else "WARNING"
                summary_txt = meas.fault_description or f"Fault condition: {meas.fault_label}"
                conn.execute(
                    """
                    INSERT INTO findings (
                        finding_id, run_id, category, priority, rule_id, rule_version,
                        status, evidence_strength, plain_summary, limitations, recommended_action
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        "find-" + run_id,
                        run_id,
                        cat,
                        prio,
                        meas.fault_label,
                        "1.0",
                        "SUPPORTED",
                        "STRONG",
                        summary_txt,
                        "Simulated fault injection window",
                        "Verify upstream network performance",
                    ),
                )

        return MonitoringRun(
            monitoring_run_id=mon_run_id,
            run_id=run_id,
            schedule_id="default",
            trigger_type="SCHEDULED",
            scheduled_for_utc=start_iso,
            started_at_utc=start_iso,
            completed_at_utc=end_iso,
            status=meas.status,
            download_mbps=meas.download_mbps,
            upload_mbps=meas.upload_mbps,
            latency_ms=meas.latency_ms,
            jitter_ms=meas.jitter_ms,
            packet_loss_pct=meas.packet_loss_pct,
        )

    def _save_simulated_slowdown_event(self, event: SlowdownEvent) -> None:
        """Persist slowdown event marked with simulation provenance."""
        sql = """
            INSERT INTO slowdown_events (
                event_id, monitoring_run_id, metric_name, detection_status,
                baseline_value, observed_value, deviation_pct, threshold_applied,
                time_window, consecutive_breaches, explanation, detected_at_utc,
                is_simulated, simulation_dataset_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    event.event_id,
                    event.monitoring_run_id,
                    event.metric_name,
                    event.detection_status,
                    event.baseline_value,
                    event.observed_value,
                    event.deviation_pct,
                    event.threshold_applied,
                    event.time_window,
                    event.consecutive_breaches,
                    event.explanation,
                    event.detected_at_utc,
                    1,
                    self.dataset_id,
                ),
            )
        # Also tag active incident with simulation dataset id if one was created
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE network_incidents 
                SET is_simulated = 1, simulation_dataset_id = ?
                WHERE simulation_dataset_id IS NULL;
                """,
                (self.dataset_id,),
            )
