"""Unit tests for Stage 2 Historical Analytics and Slowdown Detection Engine.

Verifies:
- Configurable, explainable baseline calculation (median of valid empirical samples).
- Independent evaluation of latency, jitter, packet loss, and throughput metrics.
- Single noisy spike detection as transient (SUSPECTED_SLOWDOWN).
- Sustained degradation across consecutive runs (CONFIRMED_SLOWDOWN).
- Recovery detection when previously degraded metrics return to nominal (CONFIRMED_RECOVERY).
- Insufficient historical samples (< 3 samples) returning INSUFFICIENT_DATA.
- Handling of None/missing values and unsupported metrics without errors.
- Persistence of slowdown events in SQLite and 24h, 7d, 30d time-window filtering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from ticnet.models.monitoring import (
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringTriggerType,
)
from ticnet.monitoring.analytics import (
    AnalyticsEngine,
    DetectionStatus,
    SlowdownEvent,
    TimeWindow,
    calculate_median,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.analytics_repo import AnalyticsRepository
from ticnet.persistence.repositories.monitoring_repo import MonitoringRepository


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh SQLite database with migrations V001, V002, V003 applied."""
    db_file = tmp_path / "test_stage2_analytics.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


def test_calculate_median_baseline() -> None:
    """Verify median baseline calculation across odd, even, and empty sample lists."""
    assert calculate_median([]) is None
    assert calculate_median([None, None]) is None
    assert calculate_median([15.0]) == 15.0
    assert calculate_median([10.0, 20.0, 30.0]) == 20.0
    assert calculate_median([10.0, 20.0, 30.0, 40.0]) == 25.0
    # Ignores None values
    assert calculate_median([10.0, None, 20.0, None, 30.0]) == 20.0


def test_insufficient_data_returns_insufficient_status() -> None:
    """Verify that fewer than 3 samples returns INSUFFICIENT_DATA and does not claim failure."""
    # 0 samples
    res0 = AnalyticsEngine.evaluate_metric("latency", [])
    assert res0.status == DetectionStatus.INSUFFICIENT_DATA
    assert res0.baseline_value is None

    # 2 samples
    res2 = AnalyticsEngine.evaluate_metric("latency", [20.0, 45.0])
    assert res2.status == DetectionStatus.INSUFFICIENT_DATA
    assert "Insufficient history" in res2.explanation


def test_nominal_metric_evaluation() -> None:
    """Verify metrics conforming to baseline are evaluated as NORMAL."""
    # 5 samples of stable latency around 20 ms
    series = [20.1, 19.8, 20.5, 21.0, 20.2]
    eval_res = AnalyticsEngine.evaluate_metric("latency", series)
    assert eval_res.status == DetectionStatus.NORMAL
    assert eval_res.baseline_value == pytest.approx(20.3, abs=0.2)
    assert eval_res.consecutive_breaches == 0
    assert "nominal" in eval_res.explanation.lower()


def test_single_transient_spike_is_suspected_not_confirmed() -> None:
    """Verify that an isolated spike is flagged as SUSPECTED_SLOWDOWN and not confirmed."""
    # Stable baseline around 20ms, followed by 1 spike of 65ms
    series = [20.0, 21.0, 19.5, 20.5, 65.0]
    eval_res = AnalyticsEngine.evaluate_metric("latency", series)

    assert eval_res.status == DetectionStatus.SUSPECTED_SLOWDOWN
    assert eval_res.consecutive_breaches == 1
    assert "Isolated deviation" in eval_res.explanation
    assert eval_res.baseline_value == pytest.approx(20.25, abs=0.2)


def test_sustained_degradation_triggers_confirmed_slowdown() -> None:
    """Verify that 2 or more consecutive breaches trigger CONFIRMED_SLOWDOWN."""
    # Stable baseline around 20ms, followed by 2 consecutive degraded readings (60ms and 70ms)
    series = [20.0, 21.0, 19.5, 20.5, 60.0, 70.0]
    eval_res = AnalyticsEngine.evaluate_metric("latency", series)

    assert eval_res.status == DetectionStatus.CONFIRMED_SLOWDOWN
    assert eval_res.consecutive_breaches == 2
    assert "Sustained degradation confirmed" in eval_res.explanation
    assert eval_res.observed_value == 70.0


def test_recovery_detection_when_metrics_return_to_normal() -> None:
    """Verify that returning to normal after a slowdown triggers CONFIRMED_RECOVERY."""
    # Previous status was CONFIRMED_SLOWDOWN, but latest reading returns to normal (20.5ms)
    series = [20.0, 20.5, 21.0, 65.0, 70.0, 20.5]
    eval_res = AnalyticsEngine.evaluate_metric(
        metric_name="latency",
        series=series,
        previous_status=DetectionStatus.CONFIRMED_SLOWDOWN,
    )

    assert eval_res.status == DetectionStatus.CONFIRMED_RECOVERY
    assert eval_res.consecutive_breaches == 0
    assert "recovered" in eval_res.explanation.lower()


def test_download_throughput_slowdown_evaluation() -> None:
    """Verify that download speed deterioration (lower is worse) is detected correctly."""
    # Baseline around 100 Mbps, followed by 2 drops to 40 Mbps (<65% of baseline)
    series = [100.0, 98.0, 102.0, 99.0, 40.0, 38.0]
    eval_res = AnalyticsEngine.evaluate_metric("download_speed", series)

    assert eval_res.status == DetectionStatus.CONFIRMED_SLOWDOWN
    assert eval_res.consecutive_breaches == 2
    assert eval_res.observed_value == 38.0
    assert eval_res.baseline_value >= 98.0


def test_packet_loss_slowdown_evaluation() -> None:
    """Verify packet loss >= 3% triggers sustained slowdown when consecutive."""
    # Baseline 0%, followed by 2 consecutive loss runs of 5.0%
    series = [0.0, 0.0, 0.0, 0.0, 5.0, 6.0]
    eval_res = AnalyticsEngine.evaluate_metric("packet_loss", series)

    assert eval_res.status == DetectionStatus.CONFIRMED_SLOWDOWN
    assert eval_res.consecutive_breaches == 2


def test_analytics_repository_time_window_filtering(test_db: DatabaseManager) -> None:
    """Verify AnalyticsRepository correctly filters 24H, 7D, 30D time windows."""
    repo = MonitoringRepository(test_db)
    analytics_repo = AnalyticsRepository(test_db)

    # Ensure schedule exists
    repo.save_schedule(MonitoringSchedule(schedule_id="default"))

    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    # Insert 3 runs: 2 hours ago, 3 days ago, and 15 days ago
    t_2h = (now_utc - timedelta(hours=2)).isoformat()
    t_3d = (now_utc - timedelta(days=3)).isoformat()
    t_15d = (now_utc - timedelta(days=15)).isoformat()

    r1 = MonitoringRun(schedule_id="default", scheduled_for_utc=t_2h, status="COMPLETED", latency_ms=15.0)
    r2 = MonitoringRun(schedule_id="default", scheduled_for_utc=t_3d, status="COMPLETED", latency_ms=16.0)
    r3 = MonitoringRun(schedule_id="default", scheduled_for_utc=t_15d, status="COMPLETED", latency_ms=17.0)

    repo.create_monitoring_run(r1)
    repo.create_monitoring_run(r2)
    repo.create_monitoring_run(r3)

    # 24 Hours: only r1
    runs_24h = analytics_repo.get_monitoring_runs_in_window(TimeWindow.H24, now_utc=now_utc)
    assert len(runs_24h) == 1
    assert runs_24h[0].latency_ms == 15.0

    # 7 Days: r1 and r2
    runs_7d = analytics_repo.get_monitoring_runs_in_window(TimeWindow.D7, now_utc=now_utc)
    assert len(runs_7d) == 2

    # 30 Days: r1, r2, and r3
    runs_30d = analytics_repo.get_monitoring_runs_in_window(TimeWindow.D30, now_utc=now_utc)
    assert len(runs_30d) == 3


def test_slowdown_events_persistence_and_query(test_db: DatabaseManager) -> None:
    """Verify persisting and querying SlowdownEvent audit records."""
    repo = AnalyticsRepository(test_db)

    ev = SlowdownEvent(
        metric_name="latency",
        detection_status=DetectionStatus.CONFIRMED_SLOWDOWN.value,
        baseline_value=20.0,
        observed_value=65.0,
        deviation_pct=225.0,
        threshold_applied="+40% and +15ms",
        time_window=TimeWindow.H24.value,
        consecutive_breaches=2,
        explanation="Sustained high latency detected",
    )
    repo.save_slowdown_event(ev)

    events = repo.get_recent_slowdown_events(limit=5)
    assert len(events) == 1
    assert events[0].metric_name == "latency"
    assert events[0].detection_status == DetectionStatus.CONFIRMED_SLOWDOWN.value
    assert events[0].consecutive_breaches == 2

    # Query latest status
    latest_st = repo.get_latest_slowdown_status(metric_name="latency")
    assert latest_st == DetectionStatus.CONFIRMED_SLOWDOWN


def test_baseline_strictly_excludes_evaluated_observation() -> None:
    """Audit verification 2: Baseline uses only valid prior measurements and excludes current observation."""
    # Prior measurements: [10.0, 20.0, 30.0] -> median is 20.0.
    # Current observation being evaluated: 500.0 (extreme spike).
    # If the observation were mistakenly included in the baseline, median([10, 20, 30, 500]) would be 25.0!
    series = [10.0, 20.0, 30.0, 500.0]
    res = AnalyticsEngine.evaluate_metric("latency", series)

    assert res.baseline_value == 20.0, "Baseline must equal median of prior measurements [10, 20, 30], excluding 500"
    assert res.observed_value == 500.0
    assert res.status == DetectionStatus.SUSPECTED_SLOWDOWN

    # 3 total samples: prior measurements [15.0, 25.0] -> median 20.0, current observation 80.0
    series_3 = [15.0, 25.0, 80.0]
    res_3 = AnalyticsEngine.evaluate_metric("latency", series_3)
    assert res_3.baseline_value == 20.0, "Baseline must be strictly computed from prior [15, 25], excluding 80"
    assert res_3.observed_value == 80.0


def test_near_zero_and_zero_baselines() -> None:
    """Audit verification 3: Slowdown thresholds behave correctly for zero and near-zero baselines."""
    # 1. Zero baseline for latency (LAN / localhost)
    zero_lat_series = [0.0, 0.0, 0.0, 25.0]
    res_lat = AnalyticsEngine.evaluate_metric("latency", zero_lat_series)
    assert res_lat.baseline_value == 0.0
    assert res_lat.observed_value == 25.0
    assert res_lat.status == DetectionStatus.SUSPECTED_SLOWDOWN

    # 2. Near-zero baseline for jitter
    near_zero_jit_series = [0.1, 0.1, 0.2, 5.0]
    res_jit = AnalyticsEngine.evaluate_metric("jitter", near_zero_jit_series)
    assert res_jit.baseline_value == pytest.approx(0.1, abs=0.05)
    assert res_jit.status == DetectionStatus.SUSPECTED_SLOWDOWN

    # 3. Near-zero / low-bandwidth baseline for download speed (e.g. 5 Mbps link dropping to 0.5 Mbps)
    low_dl_series = [5.0, 5.2, 4.8, 0.5]
    res_dl = AnalyticsEngine.evaluate_metric("download_speed", low_dl_series)
    assert res_dl.baseline_value == 5.0
    assert res_dl.observed_value == 0.5
    assert res_dl.status == DetectionStatus.SUSPECTED_SLOWDOWN
    assert "Drop >=" in res_dl.threshold_applied

    # 4. Zero baseline for download speed (no prior throughput established -> cannot breach)
    zero_dl_series = [0.0, 0.0, 0.0, 0.0]
    res_zero_dl = AnalyticsEngine.evaluate_metric("download_speed", zero_dl_series)
    assert res_zero_dl.status == DetectionStatus.NORMAL
    assert "Baseline is zero" in res_zero_dl.threshold_applied


def test_recovery_and_subsequent_normal_run() -> None:
    """Audit verification 4 & 5: Recovery is detected on transition, and subsequent normal run does not re-trigger recovery."""
    # Run 1-3 normal, Run 4-5 degraded, Run 6 returns to normal -> CONFIRMED_RECOVERY
    series_recovery = [20.0, 20.0, 20.0, 65.0, 70.0, 20.0]
    res_rec = AnalyticsEngine.evaluate_metric("latency", series_recovery)
    assert res_rec.status == DetectionStatus.CONFIRMED_RECOVERY
    assert "recovered" in res_rec.explanation.lower()

    # Run 7 is another normal measurement after recovery -> should be NORMAL, not repeated recovery!
    series_next_normal = [20.0, 20.0, 20.0, 65.0, 70.0, 20.0, 20.5]
    res_normal = AnalyticsEngine.evaluate_metric(
        "latency", 
        series_next_normal,
        previous_status=DetectionStatus.CONFIRMED_RECOVERY,
    )
    assert res_normal.status == DetectionStatus.NORMAL
    assert "nominal" in res_normal.explanation.lower()


def test_repeated_evaluations_do_not_duplicate_slowdown_events(test_db: DatabaseManager) -> None:
    """Audit verification 5: Saving slowdown events repeatedly for the same run updates rather than duplicates."""
    m_repo = MonitoringRepository(test_db)
    repo = AnalyticsRepository(test_db)

    # Ensure schedule and monitoring run exist for FK
    m_repo.save_schedule(MonitoringSchedule(schedule_id="default"))
    m_run = MonitoringRun(
        monitoring_run_id="test-run-123",
        schedule_id="default",
        status="COMPLETED",
        latency_ms=65.0,
    )
    m_repo.create_monitoring_run(m_run)

    run_id = "test-run-123"
    ev1 = SlowdownEvent(
        monitoring_run_id=run_id,
        metric_name="overall",
        detection_status=DetectionStatus.SUSPECTED_SLOWDOWN.value,
        baseline_value=20.0,
        observed_value=65.0,
        time_window=TimeWindow.H24.value,
        consecutive_breaches=1,
        explanation="First evaluation",
    )
    repo.save_slowdown_event(ev1)

    # Save a second time with updated status for the same run_id and window
    ev2 = SlowdownEvent(
        monitoring_run_id=run_id,
        metric_name="overall",
        detection_status=DetectionStatus.CONFIRMED_SLOWDOWN.value,
        baseline_value=20.0,
        observed_value=75.0,
        time_window=TimeWindow.H24.value,
        consecutive_breaches=2,
        explanation="Re-evaluation",
    )
    repo.save_slowdown_event(ev2)

    # Verify only ONE event exists for this run_id
    events = repo.get_recent_slowdown_events(limit=10)
    matching = [e for e in events if e.monitoring_run_id == run_id]
    assert len(matching) == 1
    assert matching[0].detection_status == DetectionStatus.CONFIRMED_SLOWDOWN.value
    assert matching[0].consecutive_breaches == 2


def test_malformed_sqlite_values_and_unsupported_upload(test_db: DatabaseManager) -> None:
    """Audit verification 6: Malformed database values and unsupported upload metrics do not cause exceptions."""
    repo = MonitoringRepository(test_db)
    analytics_repo = AnalyticsRepository(test_db)

    # Ensure schedule exists for FK
    repo.save_schedule(MonitoringSchedule(schedule_id="default"))

    now_iso = datetime.now(timezone.utc).isoformat()

    # Insert a run with raw non-numeric / malformed strings into SQLite
    with test_db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO monitoring_runs (
                monitoring_run_id, schedule_id, trigger_type, scheduled_for_utc,
                status, latency_ms, jitter_ms, packet_loss_pct, download_mbps, upload_mbps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("malformed-run-1", "default", "SCHEDULED", now_iso,
             "COMPLETED", "invalid_lat", "nan", None, 50.0, 10.0),
        )

    # Querying runs in window should safely handle malformed values without throwing ValueError
    runs = analytics_repo.get_monitoring_runs_in_window(TimeWindow.H24)
    assert len(runs) >= 1
    m_run = [r for r in runs if r.monitoring_run_id == "malformed-run-1"][0]
    assert m_run.latency_ms is None
    assert m_run.jitter_ms is None
    assert m_run.download_mbps == 50.0

    # Evaluate report on runs: unsupported upload is never evaluated as a failure
    report = AnalyticsEngine.evaluate_runs(runs, window=TimeWindow.H24)
    assert "upload_speed" not in report.evaluations
    assert report.overall_status in (DetectionStatus.INSUFFICIENT_DATA, DetectionStatus.NORMAL)


