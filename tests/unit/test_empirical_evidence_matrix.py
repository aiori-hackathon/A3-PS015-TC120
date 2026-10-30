"""Comprehensive Automated Verification Test Suite for Empirical Evidence Matrix.

Covers all 12 verification requirements:
1. Actual gateway measurement reaches the matrix.
2. Actual ping measurements reach the matrix.
3. Actual DNS evidence reaches the matrix.
4. Actual throughput measurements reach the matrix.
5. Baseline and loaded latency are correctly displayed.
6. Missing evidence is never converted into zero.
7. Skipped stages are not displayed as successful.
8. Historical evidence is retrieved using the correct Run ID.
9. Evidence from separate runs never mixes.
10. Diagnosis explanations correspond to actual rule evaluation.
11. Inconclusive outcomes are displayed correctly.
12. Existing regression tests continue passing.
"""

from __future__ import annotations

import json
from typing import List

import pytest
from PySide6.QtWidgets import QApplication

from ticnet.desktop.widgets.evidence_matrix_widget import (
    EvidenceState,
    StructuredEvidenceMatrix,
)
from ticnet.models.entities import (
    Finding,
    Observation,
    Summary,
    Target,
    TestRun,
    TestStage,
    new_id,
    now_utc_iso,
)
from ticnet.models.enums import (
    EvidenceStrength,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    MeasurementStatus,
    RunStatus,
    StageStatus,
    TargetType,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.finding_repo import FindingRepository
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository


def _find_row(matrix: StructuredEvidenceMatrix, item_prefix: str) -> int:
    """Helper to locate a row by Evidence Item prefix."""
    for row in range(matrix.table.rowCount()):
        item = matrix.table.item(row, 0)
        if item and item_prefix.lower() in item.text().lower():
            return row
    raise AssertionError(f"Row matching '{item_prefix}' not found in Evidence Matrix table")


def _cell_text(matrix: StructuredEvidenceMatrix, row: int, col: int) -> str:
    """Helper to safely extract cell text from the table."""
    item = matrix.table.item(row, col)
    assert item is not None, f"Expected table item at ({row}, {col})"
    return item.text()


# ============================================================================
# Test 1 & 2: Gateway and Ping Measurements Reach the Matrix
# ============================================================================

def test_gateway_and_ping_measurements_reach_matrix(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    run_id = "TCN-20261024-000001"

    gw_target = Target(
        target_id="gw-target-uuid-1234",
        target_type=TargetType.GATEWAY,
        ip_address="192.168.1.1",
        provider_label="Local Router",
    )
    ext_target = Target(
        target_id="cf-dns-target",
        target_type=TargetType.EXTERNAL_IP,
        ip_address="1.1.1.1",
        provider_label="Cloudflare Anycast",
    )

    stage2 = TestStage(
        stage_id="stage-2-id",
        run_id=run_id,
        stage_name="Gateway Reachability",
        stage_order=2,
        start_time_utc=now_utc_iso(),
        status=StageStatus.SUCCESS,
    )
    stage3 = TestStage(
        stage_id="stage-3-id",
        run_id=run_id,
        stage_name="External Targets Probe",
        stage_order=3,
        start_time_utc=now_utc_iso(),
        status=StageStatus.SUCCESS,
    )

    # 4 gateway probes: 1.2ms, 1.5ms, 1.8ms, 2.1ms (all SUCCESS)
    gw_obs = [
        Observation(
            observation_id=f"obs-gw-{i}",
            run_id=run_id,
            stage_id=stage2.stage_id,
            target_id=gw_target.target_id,
            sample_index=i,
            timestamp_utc=now_utc_iso(),
            duration_ms=val,
            metric_name="round_trip_time",
            value_numeric=val,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        )
        for i, val in enumerate([1.2, 1.5, 1.8, 2.1], start=1)
    ]

    # 4 external probes: 15.0ms, 16.2ms, 14.8ms, 15.5ms
    ext_obs = [
        Observation(
            observation_id=f"obs-ext-{i}",
            run_id=run_id,
            stage_id=stage3.stage_id,
            target_id=ext_target.target_id,
            sample_index=i,
            timestamp_utc=now_utc_iso(),
            duration_ms=val,
            metric_name="round_trip_time",
            value_numeric=val,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        )
        for i, val in enumerate([15.0, 16.2, 14.8, 15.5], start=1)
    ]

    run = TestRun(
        run_id=run_id,
        start_time_utc=now_utc_iso(),
        status=RunStatus.COMPLETED,
        gateway_ip="192.168.1.1",
        interface_name="Wi-Fi 6",
        app_version="1.0.0",
        config_version="1.0.0",
    )

    matrix.populate_evidence_matrix(
        observations=gw_obs + ext_obs,
        run=run,
        targets=[gw_target, ext_target],
        stages=[stage2, stage3],
    )

    # 1. Gateway reachability row
    r_gw_reach = _find_row(matrix, "Gateway reachability")
    assert "Reachable" in _cell_text(matrix, r_gw_reach, 2)
    assert "4/4 probes" in _cell_text(matrix, r_gw_reach, 2)
    assert _cell_text(matrix, r_gw_reach, 3) == EvidenceState.COLLECTED.value
    assert "BASELINE" in _cell_text(matrix, r_gw_reach, 5)

    # 2. Gateway latency row: verify min, avg, max, and p50 (since n=4 >= 3)
    r_gw_lat = _find_row(matrix, "Gateway latency")
    gw_lat_text = _cell_text(matrix, r_gw_lat, 2)
    assert "Min: 1.2 ms" in gw_lat_text
    assert "Max: 2.1 ms" in gw_lat_text
    assert "p50: 1.6 ms" in gw_lat_text or "p50: 1.7 ms" in gw_lat_text
    assert _cell_text(matrix, r_gw_lat, 3) == EvidenceState.COLLECTED.value

    # 3. External connectivity row
    r_ext = _find_row(matrix, "External connectivity")
    assert "Reachable" in _cell_text(matrix, r_ext, 2)
    assert "4/4 probes" in _cell_text(matrix, r_ext, 2)
    assert _cell_text(matrix, r_ext, 3) == EvidenceState.COLLECTED.value


# ============================================================================
# Test 3: Actual DNS Evidence Reaches the Matrix
# ============================================================================

def test_dns_evidence_reaches_matrix(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    run_id = "TCN-20261024-000002"

    stage4 = TestStage(
        stage_id="stage-4-id",
        run_id=run_id,
        stage_name="DNS Resolution",
        stage_order=4,
        start_time_utc=now_utc_iso(),
        status=StageStatus.SUCCESS,
    )

    dns_meta = {
        "resolver_ip": "1.1.1.1",
        "resolver_port": 53,
        "domain": "example.com",
        "rcode": 0,
        "rcode_name": "NOERROR",
        "resolution_status": "RESOLVED",
        "has_answer": True,
        "answer_count": 2,
    }

    # Observations created by DNSMeasurementResult.to_observation use metric_name="dns_query_time"
    dns_obs = [
        Observation(
            observation_id=f"obs-dns-{i}",
            run_id=run_id,
            stage_id=stage4.stage_id,
            target_id="dns-cf",
            sample_index=i,
            timestamp_utc=now_utc_iso(),
            duration_ms=val,
            metric_name="dns_query_time",
            value_numeric=val,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            method_metadata=json.dumps(dns_meta),
        )
        for i, val in enumerate([18.4, 21.2, 19.0], start=1)
    ]

    matrix.populate_evidence_matrix(
        observations=dns_obs,
        stages=[stage4],
    )

    r_dns = _find_row(matrix, "DNS resolution")
    meas_text = _cell_text(matrix, r_dns, 2)
    assert "NOERROR" in meas_text
    assert "3/3 resolved" in meas_text
    assert "19.0 ms" in meas_text or "19.5 ms" in meas_text
    assert _cell_text(matrix, r_dns, 3) == EvidenceState.COLLECTED.value
    assert "BASELINE" in _cell_text(matrix, r_dns, 5)


# ============================================================================
# Test 4: Actual Throughput Measurements Reach the Matrix
# ============================================================================

def test_throughput_measurements_reach_matrix(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    run_id = "TCN-20261024-000003"

    stage5 = TestStage(
        stage_id="stage-5-id",
        run_id=run_id,
        stage_name="Throughput & Loaded Latency",
        stage_order=5,
        start_time_utc=now_utc_iso(),
        status=StageStatus.SUCCESS,
    )

    # Observation from ThroughputMeasurementResult.to_observation uses metric_name="download_throughput"
    tp_obs = Observation(
        observation_id="obs-tp-1",
        run_id=run_id,
        stage_id=stage5.stage_id,
        target_id="tp-endpoint",
        sample_index=1,
        timestamp_utc=now_utc_iso(),
        duration_ms=4500.0,
        metric_name="download_throughput",
        value_numeric=52.8,
        unit="Mbps",
        status=MeasurementStatus.SUCCESS,
        method_metadata=json.dumps({"throughput_mbps": 52.8, "bytes_transferred": 29725000}),
    )

    matrix.populate_evidence_matrix(
        observations=[tp_obs],
        stages=[stage5],
    )

    r_tp = _find_row(matrix, "Throughput capacity")
    meas_text = _cell_text(matrix, r_tp, 2)
    assert "52.8 Mbps" in meas_text
    assert _cell_text(matrix, r_tp, 3) == EvidenceState.COLLECTED.value


# ============================================================================
# Test 5: Baseline and Loaded Latency Correctly Displayed
# ============================================================================

def test_loaded_latency_baseline_loaded_delta_displayed(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    run_id = "TCN-20261024-000004"

    stage5 = TestStage(
        stage_id="stage-5-id",
        run_id=run_id,
        stage_name="Throughput & Loaded Latency",
        stage_order=5,
        start_time_utc=now_utc_iso(),
        status=StageStatus.SUCCESS,
    )

    meta = {
        "target_ip": "192.168.1.1",
        "baseline_median_rtt_ms": 12.4,
        "loaded_median_rtt_ms": 95.8,
        "delta_rtt_median_ms": 83.4,
        "bufferbloat_ratio": 7.7,
        "load_active_during_probes": True,
        "load_throughput_mbps": 48.5,
    }

    delta_obs = Observation(
        observation_id="obs-delta-1",
        run_id=run_id,
        stage_id=stage5.stage_id,
        target_id="load-target",
        sample_index=1,
        timestamp_utc=now_utc_iso(),
        duration_ms=0.0,
        metric_name="loaded_latency_delta",
        value_numeric=83.4,
        unit="ms",
        status=MeasurementStatus.SUCCESS,
        method_metadata=json.dumps(meta),
    )

    matrix.populate_evidence_matrix(
        observations=[delta_obs],
        stages=[stage5],
    )

    r_load = _find_row(matrix, "Loaded latency")
    meas_text = _cell_text(matrix, r_load, 2)
    assert "Base: 12.4 ms" in meas_text
    assert "Loaded: 95.8 ms" in meas_text
    assert "Delta: +83.4 ms" in meas_text
    assert "Ratio: 7.7x" in meas_text
    assert _cell_text(matrix, r_load, 3) == EvidenceState.COLLECTED.value
    assert "Critical" in _cell_text(matrix, r_load, 5)

    # Also verify throughput captured during load test
    r_tp = _find_row(matrix, "Throughput capacity")
    tp_text = _cell_text(matrix, r_tp, 2)
    assert "48.5 Mbps" in tp_text
    assert _cell_text(matrix, r_tp, 3) == EvidenceState.COLLECTED.value


# ============================================================================
# Test 6: Missing Evidence is Never Converted into Zero
# ============================================================================

def test_missing_evidence_is_never_converted_to_zero(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    # Completely empty run
    matrix.populate_evidence_matrix(observations=[])

    for row in range(matrix.table.rowCount()):
        meas_text = _cell_text(matrix, row, 2)
        status_text = _cell_text(matrix, row, 3)

        assert "Not Measured" in meas_text
        assert status_text == EvidenceState.NOT_TESTED.value
        assert "0.0 ms" not in meas_text
        assert "0.0 Mbps" not in meas_text
        assert "0.0%" not in meas_text


# ============================================================================
# Test 7: Skipped Stages are Not Displayed as Successful
# ============================================================================

def test_skipped_stages_displayed_as_unavailable(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    run_id = "TCN-20261024-000005"

    stage1 = TestStage(stage_id="s1", run_id=run_id, stage_name="Gateway Discovery", stage_order=1, start_time_utc=now_utc_iso(), status=StageStatus.FAILED, error_message="No default route")
    stage2 = TestStage(stage_id="s2", run_id=run_id, stage_name="Gateway Reachability", stage_order=2, start_time_utc=now_utc_iso(), status=StageStatus.SKIPPED, skip_reason="Gateway IP unresolved")
    stage5 = TestStage(stage_id="s5", run_id=run_id, stage_name="Throughput & Loaded Latency", stage_order=5, start_time_utc=now_utc_iso(), status=StageStatus.SKIPPED, skip_reason="Gateway unreachable per SRS-F-008")

    matrix.populate_evidence_matrix(
        observations=[],
        stages=[stage1, stage2, stage5],
    )

    r_gw = _find_row(matrix, "Gateway reachability")
    assert "Unavailable" in _cell_text(matrix, r_gw, 2)
    assert _cell_text(matrix, r_gw, 3) == EvidenceState.UNAVAILABLE.value

    r_tp = _find_row(matrix, "Throughput capacity")
    assert "Skipped" in _cell_text(matrix, r_tp, 2)
    assert _cell_text(matrix, r_tp, 3) == EvidenceState.UNAVAILABLE.value

    r_load = _find_row(matrix, "Loaded latency")
    assert "Skipped" in _cell_text(matrix, r_load, 2)
    assert _cell_text(matrix, r_load, 3) == EvidenceState.UNAVAILABLE.value


# ============================================================================
# Test 8 & 9: Historical Evidence Retrieval & Cross-Run Isolation
# ============================================================================

def test_database_persistence_and_cross_run_isolation(qapp: QApplication, tmp_path) -> None:
    db_file = tmp_path / "cross_run_test.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()

    run_repo = RunRepository(db)
    obs_repo = ObservationRepository(db)
    find_repo = FindingRepository(db)

    run_a_id = "TCN-20261024-000001"
    run_b_id = "TCN-20261024-000002"

    run_a = TestRun(
        run_id=run_a_id,
        start_time_utc=now_utc_iso(),
        status=RunStatus.COMPLETED,
        gateway_ip="192.168.1.1",
        app_version="1.0.0",
        config_version="1.0.0",
    )
    run_b = TestRun(
        run_id=run_b_id,
        start_time_utc=now_utc_iso(),
        status=RunStatus.FAILED,
        gateway_ip="10.0.0.1",
        app_version="1.0.0",
        config_version="1.0.0",
    )
    run_repo.create_run(run_a)
    run_repo.create_run(run_b)

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO targets (target_id, target_type, ip_address, provider_label) VALUES (?, ?, ?, ?)",
            ("gw-a", "GATEWAY", "192.168.1.1", "Gateway A"),
        )
        conn.execute(
            "INSERT INTO targets (target_id, target_type, ip_address, provider_label) VALUES (?, ?, ?, ?)",
            ("gw-b", "GATEWAY", "10.0.0.1", "Gateway B"),
        )

    # Run A has 5 fast gateway probes
    for i in range(1, 6):
        obs_repo.create_observation(
            Observation(
                observation_id=f"obs-a-{i}",
                run_id=run_a_id,
                sample_index=i,
                timestamp_utc=now_utc_iso(),
                duration_ms=1.5,
                metric_name="round_trip_time",
                value_numeric=1.5,
                unit="ms",
                status=MeasurementStatus.SUCCESS,
                target_id="gw-a",
            )
        )

    # Run B has 5 timed out probes
    for i in range(1, 6):
        obs_repo.create_observation(
            Observation(
                observation_id=f"obs-b-{i}",
                run_id=run_b_id,
                sample_index=i,
                timestamp_utc=now_utc_iso(),
                duration_ms=0.0,
                metric_name="round_trip_time",
                value_numeric=None,
                unit="ms",
                status=MeasurementStatus.TIMEOUT,
                target_id="gw-b",
            )
        )

    # Query observations exclusively for Run A
    obs_for_a = obs_repo.get_observations_for_run(run_a_id)
    obs_for_b = obs_repo.get_observations_for_run(run_b_id)

    assert len(obs_for_a) == 5
    assert all(o.run_id == run_a_id for o in obs_for_a)
    assert all(o.value_numeric == 1.5 for o in obs_for_a)

    assert len(obs_for_b) == 5
    assert all(o.run_id == run_b_id for o in obs_for_b)
    assert all(o.value_numeric is None for o in obs_for_b)

    # Test matrix populated for Run A
    matrix_a = StructuredEvidenceMatrix()
    matrix_a.populate_evidence_matrix(observations=obs_for_a, run=run_a)
    r_gw_a = _find_row(matrix_a, "Gateway reachability")
    assert "Reachable" in _cell_text(matrix_a, r_gw_a, 2)
    assert _cell_text(matrix_a, r_gw_a, 3) == EvidenceState.COLLECTED.value

    # Test matrix populated for Run B
    matrix_b = StructuredEvidenceMatrix()
    matrix_b.populate_evidence_matrix(observations=obs_for_b, run=run_b)
    r_gw_b = _find_row(matrix_b, "Gateway reachability")
    assert "Unreachable" in _cell_text(matrix_b, r_gw_b, 2)
    assert _cell_text(matrix_b, r_gw_b, 3) == EvidenceState.FAILED.value


# ============================================================================
# Test 10: Diagnosis Explanations Correspond to Actual Rule Evaluation
# ============================================================================

def test_diagnosis_explanation_corresponds_to_rule(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    run_id = "TCN-20261024-000006"

    finding = Finding(
        finding_id="find-gw-1",
        run_id=run_id,
        category=FindingCategory.LOCAL_NETWORK,
        priority=FindingPriority.CRITICAL,
        rule_id="R-GW-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Local gateway connection is unreachable.",
        limitations="Does not evaluate ISP upstream transit paths.",
        recommended_action="Check Wi-Fi cable or power cycle router.",
    )

    matrix.populate_evidence_matrix(
        observations=[],
        findings=[finding],
    )

    r_diag = _find_row(matrix, "Diagnostic root cause")
    assert "R-GW-01: SUPPORTED" in _cell_text(matrix, r_diag, 2)
    assert _cell_text(matrix, r_diag, 3) == EvidenceState.COLLECTED.value
    assert "Local gateway connection is unreachable." in _cell_text(matrix, r_diag, 4)
    assert "Critical" in _cell_text(matrix, r_diag, 5)


# ============================================================================
# Test 11: Inconclusive Outcomes are Displayed Correctly
# ============================================================================

def test_inconclusive_outcomes_displayed_correctly(qapp: QApplication) -> None:
    matrix = StructuredEvidenceMatrix()
    run_id = "TCN-20261024-000007"

    finding_inconclusive = Finding(
        finding_id="find-inc-1",
        run_id=run_id,
        category=FindingCategory.EXTERNAL_PATH,
        priority=FindingPriority.INFO,
        rule_id="R-INCONCLUSIVE-01",
        rule_version="1.0.0",
        status=FindingStatus.INSUFFICIENT_EVIDENCE,
        evidence_strength=EvidenceStrength.INCONCLUSIVE,
        plain_summary="Single-target degradation observed without multi-target corroboration.",
        limitations="Single external destination failure cannot establish last-mile impairment.",
        recommended_action="Retest connection to verify if problem persists.",
    )

    matrix.populate_evidence_matrix(
        observations=[],
        findings=[finding_inconclusive],
    )

    r_diag = _find_row(matrix, "Diagnostic root cause")
    assert "R-INCONCLUSIVE-01" in _cell_text(matrix, r_diag, 2)
    assert _cell_text(matrix, r_diag, 3) == EvidenceState.INCONCLUSIVE.value
    assert "INCONCLUSIVE" in _cell_text(matrix, r_diag, 5)
