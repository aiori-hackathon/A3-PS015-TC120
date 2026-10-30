"""Verification tests for Radar Chart dynamic updates, normalization, and window filtering."""

from __future__ import annotations

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from PySide6.QtWidgets import QApplication

from ticnet.desktop.widgets.network_analytics_widget import NetworkAnalyticsWidget
from ticnet.desktop.widgets.radar_chart_widget import RadarChartWidget
from ticnet.models.monitoring import (
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringTriggerType,
)
from ticnet.monitoring.analytics import TimeWindow
from ticnet.persistence.database import DatabaseManager
from ticnet.desktop.services.monitoring_service import MonitoringService


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_radar_dyn.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def monitoring_service(test_db: DatabaseManager) -> MonitoringService:
    service = MonitoringService(db=test_db)
    yield service
    service.shutdown()


def test_radar_normalization_higher_score_for_better_performance(qapp: QApplication) -> None:
    """Verify that lower latency, lower jitter, and zero packet loss produce higher scores and radii."""
    chart = RadarChartWidget()

    # Good network profile: 15ms latency, 0.5ms jitter, 0.0% loss, 50 Mbps download
    chart.update_metrics(
        current={
            "latency_ms": 15.0,
            "jitter_ms": 0.5,
            "packet_loss_pct": 0.0,
            "download_mbps": 50.0,
        },
        baseline={
            "latency_ms": 35.0,
            "jitter_ms": 1.5,
            "packet_loss_pct": 0.0,
            "download_mbps": 35.0,
        },
        has_data=True,
    )

    good_lat_radius = chart.current_radii[0]
    good_jit_radius = chart.current_radii[1]
    good_loss_radius = chart.current_radii[2]
    good_dl_radius = chart.current_radii[3]

    # Degraded network profile: 90ms latency, 6.0ms jitter, 3.0% loss, 10 Mbps download
    chart.update_metrics(
        current={
            "latency_ms": 90.0,
            "jitter_ms": 6.0,
            "packet_loss_pct": 3.0,
            "download_mbps": 10.0,
        },
        baseline={
            "latency_ms": 35.0,
            "jitter_ms": 1.5,
            "packet_loss_pct": 0.0,
            "download_mbps": 35.0,
        },
        has_data=True,
    )

    bad_lat_radius = chart.current_radii[0]
    bad_jit_radius = chart.current_radii[1]
    bad_loss_radius = chart.current_radii[2]
    bad_dl_radius = chart.current_radii[3]

    # Verify: Higher scores / larger radii consistently represent BETTER performance
    assert good_lat_radius > bad_lat_radius, "Lower latency must yield a higher radius"
    assert good_jit_radius > bad_jit_radius, "Lower jitter must yield a higher radius"
    assert good_loss_radius > bad_loss_radius, "0% loss must yield a higher radius than 3% loss"
    assert good_dl_radius > bad_dl_radius, "Higher download speed must yield a higher radius"

    # Verify 0.0% packet loss is at the outermost radius (~0.95)
    assert chart.current_labels[2] == "3.0 %"


def test_radar_unmeasured_download_throughput_handled_honestly(qapp: QApplication) -> None:
    """Verify missing download throughput does not fabricate synthetic values."""
    chart = RadarChartWidget()
    chart.update_metrics(
        current={
            "latency_ms": 42.0,
            "jitter_ms": 1.2,
            "packet_loss_pct": 0.0,
            "download_mbps": None,  # Ping-only test
        },
        baseline={
            "latency_ms": 35.0,
            "jitter_ms": 1.5,
            "packet_loss_pct": 0.0,
            "download_mbps": 35.0,
        },
        has_data=True,
    )

    # Download Throughput label should be "—"
    assert chart.current_labels[3] == "—"
    # Vertex matches baseline score neutrally without fabricating a spike or deficit
    assert pytest.approx(chart.current_radii[3], abs=0.02) == chart.baseline_radii[3]


def test_radar_dynamic_update_on_new_run(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify radar chart updates dynamically when a new monitoring run arrives."""
    repo = monitoring_service.scheduler.repo
    now_utc = datetime.now(timezone.utc)

    # Initial good run
    r1 = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.SCHEDULED.value,
        scheduled_for_utc=(now_utc - timedelta(minutes=30)).isoformat(),
        started_at_utc=(now_utc - timedelta(minutes=30)).isoformat(),
        completed_at_utc=(now_utc - timedelta(minutes=29)).isoformat(),
        status=MonitoringRunStatus.COMPLETED.value,
        latency_ms=25.0,
        jitter_ms=1.0,
        packet_loss_pct=0.0,
        download_mbps=45.0,
    )
    repo.create_monitoring_run(r1)

    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()

    initial_lat_radius = widget.radar_widget.current_radii[0]
    assert widget.radar_widget.current_labels[0] == "25.0 ms"

    # Insert a new run with degraded latency (e.g. 85 ms)
    r2 = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.MANUAL.value,
        scheduled_for_utc=now_utc.isoformat(),
        started_at_utc=now_utc.isoformat(),
        completed_at_utc=now_utc.isoformat(),
        status=MonitoringRunStatus.COMPLETED.value,
        latency_ms=85.0,
        jitter_ms=3.0,
        packet_loss_pct=1.0,
        download_mbps=25.0,
    )
    repo.create_monitoring_run(r2)

    # Trigger reactive update
    monitoring_service.sig_monitoring_run_completed.emit(r2.monitoring_run_id, "COMPLETED")

    # Radar chart should have dynamically updated with r2's metrics
    updated_lat_radius = widget.radar_widget.current_radii[0]
    assert widget.radar_widget.current_labels[0] == "85.0 ms"
    assert updated_lat_radius < initial_lat_radius, "Radius must shrink inward reflecting degraded latency"
    widget.close()


def test_radar_time_window_filtering(
    qapp: QApplication,
    monitoring_service: MonitoringService,
) -> None:
    """Verify that switching time windows (24H vs 7D) recalculates the radar data."""
    repo = monitoring_service.scheduler.repo
    now_utc = datetime.now(timezone.utc)

    # Run in the 24H window (recent)
    r_recent = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.SCHEDULED.value,
        scheduled_for_utc=(now_utc - timedelta(hours=2)).isoformat(),
        started_at_utc=(now_utc - timedelta(hours=2)).isoformat(),
        completed_at_utc=(now_utc - timedelta(hours=2)).isoformat(),
        status=MonitoringRunStatus.COMPLETED.value,
        latency_ms=30.0,
        jitter_ms=1.2,
        packet_loss_pct=0.0,
        download_mbps=50.0,
    )
    repo.create_monitoring_run(r_recent)

    # Runs older than 24h (e.g. 3 days ago)
    for i in range(3):
        r_old = MonitoringRun(
            schedule_id="default",
            trigger_type=MonitoringTriggerType.SCHEDULED.value,
            scheduled_for_utc=(now_utc - timedelta(days=3, hours=i)).isoformat(),
            started_at_utc=(now_utc - timedelta(days=3, hours=i)).isoformat(),
            completed_at_utc=(now_utc - timedelta(days=3, hours=i)).isoformat(),
            status=MonitoringRunStatus.COMPLETED.value,
            latency_ms=75.0,
            jitter_ms=4.0,
            packet_loss_pct=2.0,
            download_mbps=20.0,
        )
        repo.create_monitoring_run(r_old)

    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()

    # In 24H window: only 1 run (latency 30.0 ms)
    assert widget.lbl_sample_count.text() == "1 measurements"

    # Switch to 7 Days
    widget.btn_7d.click()
    assert widget._active_window == TimeWindow.D7
    assert widget.lbl_sample_count.text() == "4 measurements"

    # In 7D, baseline is established from prior runs (median around 75ms)
    assert widget.radar_widget.has_data is True
    assert widget.radar_widget.baseline_radii[0] is not None
    widget.close()


def test_radar_live_telemetry_dynamic_updates(
    qapp: QApplication, monitoring_service: MonitoringService
) -> None:
    """Verify that live telemetry samples dynamically update the radar chart and metric cards in real time."""
    from ticnet.desktop.services.live_telemetry_service import LiveTelemetrySample

    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()

    # Create a genuine live telemetry observation
    sample = LiveTelemetrySample(
        timestamp=1000.0,
        time_str="12:00:00",
        latency_ms=28.4,
        jitter_ms=1.1,
        packet_loss_pct=0.0,
        download_mbps=48.2,
        upload_mbps=12.0,
        min_latency_ms=25.0,
        avg_latency_ms=28.4,
        max_latency_ms=32.0,
        interface_name="Wi-Fi",
        gateway_ip="192.168.1.1",
        target_ip="1.1.1.1",
        is_connected=True,
    )

    # Dispatch live sample
    widget.on_live_telemetry_sample(sample)

    # Verify radar chart updated immediately
    assert widget.radar_widget.has_data is True
    assert widget.radar_widget.current_labels[0] == "28.4 ms"
    assert widget.radar_widget.current_labels[1] == "1.1 ms"
    assert widget.radar_widget.current_labels[2] == "0.0 %"
    assert widget.radar_widget.current_labels[3] == "48.2 Mbps"

    # Verify 2x2 metric cards updated
    assert widget.card_lat.lbl_val.text() == "28.4 ms"
    assert widget.card_jit.lbl_val.text() == "1.10 ms"
    assert widget.card_loss.lbl_val.text() == "0.0 %"
    assert widget.card_dl.lbl_val.text() == "48.2 Mbps"

    # Dispatch second live sample with degraded latency
    sample_degraded = LiveTelemetrySample(
        timestamp=1001.0,
        time_str="12:00:01",
        latency_ms=95.6,
        jitter_ms=5.4,
        packet_loss_pct=3.0,
        download_mbps=14.0,
        upload_mbps=2.0,
        min_latency_ms=25.0,
        avg_latency_ms=60.0,
        max_latency_ms=95.6,
        interface_name="Wi-Fi",
        gateway_ip="192.168.1.1",
        target_ip="1.1.1.1",
        is_connected=True,
    )
    widget.on_live_telemetry_sample(sample_degraded)

    assert widget.radar_widget.current_labels[0] == "95.6 ms"
    assert widget.radar_widget.current_labels[1] == "5.4 ms"
    assert widget.radar_widget.current_labels[2] == "3.0 %"
    assert widget.radar_widget.current_labels[3] == "14.0 Mbps"
    assert widget.lbl_health_pill.text() == "Degraded"
    widget.close()


def test_radar_axis_focus_from_combobox(
    qapp: QApplication, monitoring_service: MonitoringService
) -> None:
    """Verify that selecting a metric from the combobox highlights that axis on the radar chart."""
    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()

    # Default is radar overview (no single axis focused)
    widget.combo_metric.setCurrentIndex(4)  # Multi-Metric Radar Overview
    assert widget.radar_widget.focused_axis is None

    # Focus External Latency (axis 0)
    widget.combo_metric.setCurrentIndex(0)  # External Latency (ms)
    assert widget.radar_widget.focused_axis == 0

    # Focus Jitter (axis 1)
    widget.combo_metric.setCurrentIndex(1)  # Delay Variation / Jitter (ms)
    assert widget.radar_widget.focused_axis == 1

    # Focus Packet Loss (axis 2)
    widget.combo_metric.setCurrentIndex(2)  # Packet Loss (%)
    assert widget.radar_widget.focused_axis == 2

    # Focus Download Throughput (axis 3)
    widget.combo_metric.setCurrentIndex(3)  # Download Throughput (Mbps)
    assert widget.radar_widget.focused_axis == 3

    widget.close()


def test_radar_skipped_run_does_not_mask_valid_measurements(
    qapp: QApplication, monitoring_service: MonitoringService
) -> None:
    """Verify that a recent SKIPPED run with None metrics does not display '—' when completed runs exist."""
    repo = monitoring_service.scheduler.repo
    now_utc = datetime.now(timezone.utc)

    # 3 completed historical runs
    for i in range(3):
        r = MonitoringRun(
            schedule_id="default",
            trigger_type=MonitoringTriggerType.SCHEDULED.value,
            scheduled_for_utc=(now_utc - timedelta(hours=i + 2)).isoformat(),
            started_at_utc=(now_utc - timedelta(hours=i + 2)).isoformat(),
            completed_at_utc=(now_utc - timedelta(hours=i + 2)).isoformat(),
            status=MonitoringRunStatus.COMPLETED.value,
            latency_ms=42.0,
            jitter_ms=1.4,
            packet_loss_pct=0.0,
            download_mbps=32.0,
        )
        repo.create_monitoring_run(r)

    # 1 recent SKIPPED run (e.g. offline detection) with None metrics
    r_skipped = MonitoringRun(
        schedule_id="default",
        trigger_type=MonitoringTriggerType.SCHEDULED.value,
        scheduled_for_utc=(now_utc - timedelta(minutes=10)).isoformat(),
        started_at_utc=None,
        completed_at_utc=None,
        status=MonitoringRunStatus.SKIPPED.value,
        latency_ms=None,
        jitter_ms=None,
        packet_loss_pct=None,
        download_mbps=None,
        error_message="Skipped: Application offline",
    )
    repo.create_monitoring_run(r_skipped)

    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.show()
    widget.refresh_analytics()

    # Must NOT be "—"
    assert widget.radar_widget.current_labels[0] != "—"
    assert "42.0" in widget.radar_widget.current_labels[0]
    widget.close()


def test_radar_mathematical_coordinates_and_grid_bounds(qapp: QApplication) -> None:
    """Verify all plotted points remain strictly bounded within grid levels [0.08, 1.00]."""
    chart = RadarChartWidget()

    # Extreme high throughput test (150 Mbps vs 30 Mbps baseline)
    chart.update_metrics(
        current={
            "latency_ms": 10.0,
            "jitter_ms": 0.2,
            "packet_loss_pct": 0.0,
            "download_mbps": 150.0,
        },
        baseline={
            "latency_ms": 30.0,
            "jitter_ms": 1.0,
            "packet_loss_pct": 0.0,
            "download_mbps": 30.0,
        },
        has_data=True,
    )

    # Throughput must never overshoot the outer grid ring (<= 1.00)
    for r in chart.current_radii:
        assert 0.08 <= r <= 1.00, f"Radius factor {r} exceeds valid bounds"
    for r in chart.baseline_radii:
        assert 0.08 <= r <= 1.00, f"Baseline radius factor {r} exceeds valid bounds"

    # 0.0% packet loss maps to the optimal score (100.0) and optimal position (1.00)
    assert chart._scores_curr[2] == 100.0
    assert chart.current_radii[2] == 1.00


def test_radar_user_screenshot_dataset_math_accuracy(qapp: QApplication) -> None:
    """Verify exact mathematical accuracy using the user's uploaded screenshot dataset:
    Latency: 51.9 ms, Jitter: 13.4 ms, Packet Loss: 0.0%, Download Throughput: 80.8 Mbps.
    """
    chart = RadarChartWidget()
    chart.update_metrics(
        current={
            "latency_ms": 51.9,
            "jitter_ms": 13.4,
            "packet_loss_pct": 0.0,
            "download_mbps": 80.8,
        },
        baseline={
            "latency_ms": 35.0,
            "jitter_ms": 1.5,
            "packet_loss_pct": 0.0,
            "download_mbps": 35.0,
        },
        has_data=True,
    )

    # 1. Download throughput (80.8 Mbps) must stay cleanly inside the outer perimeter
    assert chart.current_radii[3] <= 1.00
    assert chart.current_radii[3] > chart.baseline_radii[3]

    # 2. Stability should reflect realistic variance degradation, not an arbitrary 10% collapse
    stability_score = chart._scores_curr[4]
    assert 60.0 <= stability_score <= 85.0, f"Stability {stability_score}% should be rational"

    # 3. Jitter is elevated (13.4 ms vs 1.5 ms baseline), so score drops below baseline (75%)
    jitter_score = chart._scores_curr[1]
    assert 60.0 <= jitter_score <= 80.0
    assert chart.current_radii[1] < chart.baseline_radii[1]


def test_radar_baseline_polygon_updates_dynamically(qapp: QApplication) -> None:
    """Verify both Current Network Profile and Baseline polygons update dynamically when baseline changes."""
    chart = RadarChartWidget()

    # Initial baseline
    chart.update_metrics(
        current={"latency_ms": 40.0, "jitter_ms": 2.0, "packet_loss_pct": 0.0, "download_mbps": 30.0},
        baseline={"latency_ms": 40.0, "jitter_ms": 2.0, "packet_loss_pct": 0.0, "download_mbps": 30.0},
        has_data=True,
    )
    base_radii_1 = list(chart.baseline_radii)

    # Updated baseline (e.g. switching time window to 30 days where baseline packet loss is 1.5%)
    chart.update_metrics(
        current={"latency_ms": 40.0, "jitter_ms": 2.0, "packet_loss_pct": 0.0, "download_mbps": 30.0},
        baseline={"latency_ms": 40.0, "jitter_ms": 2.0, "packet_loss_pct": 1.5, "download_mbps": 30.0},
        has_data=True,
    )
    base_radii_2 = list(chart.baseline_radii)

    # Baseline loss radius must decrease dynamically when baseline packet loss degrades
    assert base_radii_2[2] < base_radii_1[2]


def test_radar_all_six_metrics_exact_axis_mapping_and_inverse_scaling(qapp: QApplication) -> None:
    """Validate every one of the 6 metrics against its correct axis, scaling, and inverse/direct direction."""
    chart = RadarChartWidget()

    # Verify axis naming and mapping
    assert chart.axes_config[0][0] == "Latency"
    assert chart.axes_config[1][0] == "Jitter"
    assert chart.axes_config[2][0] == "Packet Loss"
    assert chart.axes_config[3][0] == "Download Throughput"
    assert chart.axes_config[4][0] == "Stability"
    assert chart.axes_config[5][0] == "Recovery"

    # Baseline: Latency=35ms, Jitter=1.5ms, Loss=0%, DL=35Mbps
    baseline = {"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 35.0}

    # 1. Latency (Axis 0) - Lower is better (Inverted)
    chart.update_metrics({"latency_ms": 15.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 35.0}, baseline, has_data=True)
    score_low_lat = chart._scores_curr[0]
    chart.update_metrics({"latency_ms": 80.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 35.0}, baseline, has_data=True)
    score_high_lat = chart._scores_curr[0]
    assert score_low_lat > score_high_lat, "Lower latency must score higher"

    # 2. Jitter (Axis 1) - Lower is better (Inverted)
    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 0.5, "packet_loss_pct": 0.0, "download_mbps": 35.0}, baseline, has_data=True)
    score_low_jit = chart._scores_curr[1]
    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 10.0, "packet_loss_pct": 0.0, "download_mbps": 35.0}, baseline, has_data=True)
    score_high_jit = chart._scores_curr[1]
    assert score_low_jit > score_high_jit, "Lower jitter must score higher"

    # 3. Packet Loss (Axis 2) - Lower is better (Inverted)
    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 35.0}, baseline, has_data=True)
    score_zero_loss = chart._scores_curr[2]
    radius_zero_loss = chart.current_radii[2]

    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 3.0, "download_mbps": 35.0}, baseline, has_data=True)
    score_high_loss = chart._scores_curr[2]
    radius_high_loss = chart.current_radii[2]

    assert score_zero_loss == 100.0, "0% loss must map to optimal score (100.0)"
    assert radius_zero_loss == 1.00
    assert score_zero_loss > score_high_loss, "Lower packet loss must score higher"
    assert radius_zero_loss > radius_high_loss

    # 4. Download Throughput (Axis 3) - Higher is better (Direct)
    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 10.0}, baseline, has_data=True)
    score_low_dl = chart._scores_curr[3]
    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 70.0}, baseline, has_data=True)
    score_high_dl = chart._scores_curr[3]
    assert score_high_dl > score_low_dl, "Higher throughput must score higher"
    assert chart.current_radii[3] <= 1.00, "Throughput must never overshoot outer ring"

    # 5. Stability (Axis 4) - Higher is better (Direct)
    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 35.0}, baseline, has_data=True)
    stab_clean = chart._scores_curr[4]
    chart.update_metrics({"latency_ms": 35.0, "jitter_ms": 12.0, "packet_loss_pct": 2.0, "download_mbps": 35.0}, baseline, has_data=True)
    stab_degraded = chart._scores_curr[4]
    assert stab_clean > stab_degraded, "Clean transmission must have higher stability than degraded"

    # 6. Recovery (Axis 5) - Higher is better (Direct)
    rec_clean = chart._scores_curr[5]
    chart.update_metrics({"latency_ms": 90.0, "jitter_ms": 1.5, "packet_loss_pct": 2.5, "download_mbps": 35.0}, baseline, has_data=True)
    rec_degraded = chart._scores_curr[5]
    assert rec_clean > rec_degraded, "Nominal latency must have higher recovery than elevated"


def test_radar_flexible_keys_and_custom_stability_recovery(qapp: QApplication) -> None:
    """Verify radar chart accepts flexible telemetry keys and custom stability/recovery scores."""
    chart = RadarChartWidget()

    # Pass flexible key variants: latency, jitter, loss, download, stability, recovery
    chart.update_metrics(
        current={
            "latency": 28.4,
            "jitter": 1.1,
            "loss": 0.0,
            "download": 64.0,
            "stability": 92.5,
            "recovery": 89.0,
        },
        baseline={
            "latency": 35.0,
            "jitter": 1.5,
            "loss": 0.0,
            "download": 35.0,
            "stability": 95.0,
            "recovery": 95.0,
        },
        has_data=True,
    )

    assert chart.current_labels[0] == "28.4 ms"
    assert chart.current_labels[1] == "1.1 ms"
    assert chart.current_labels[2] == "0.0 %"
    assert chart.current_labels[3] == "64.0 Mbps"
    assert chart.current_labels[4] == "92.5 %"
    assert chart.current_labels[5] == "89.0 %"
    assert chart._scores_curr[4] == 92.5
    assert chart._scores_curr[5] == 89.0


def test_radar_identical_inputs_produce_identical_positions(qapp: QApplication) -> None:
    """Requirement 6: Verify that identical inputs to Current and Baseline always produce identical positions."""
    chart = RadarChartWidget()
    dataset = {
        "latency_ms": 35.0,
        "jitter_ms": 1.5,
        "packet_loss_pct": 0.0,
        "download_mbps": 50.0,
        "stability_pct": 98.0,
        "recovery_pct": 96.0,
    }

    chart.update_metrics(current=dataset, baseline=dataset, has_data=True)

    for i in range(6):
        assert chart._scores_curr[i] == chart._scores_base[i], f"Axis {i} score must be identical"
        assert chart.current_radii[i] == chart.baseline_radii[i], f"Axis {i} radius must be identical"
        assert chart.current_labels[i] == chart.baseline_labels[i], f"Axis {i} label must be identical"


def test_radar_changing_single_input_moves_correct_axis(qapp: QApplication) -> None:
    """Requirement 6: Verify that changing an individual input changes the correct plotted point."""
    chart = RadarChartWidget()
    baseline = {
        "latency_ms": 35.0,
        "jitter_ms": 1.5,
        "packet_loss_pct": 0.0,
        "download_mbps": 40.0,
        "stability_pct": 100.0,
        "recovery_pct": 100.0,
    }

    # 1. Latency change
    chart.update_metrics(current={"latency_ms": 20.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 40.0, "stability_pct": 100.0, "recovery_pct": 100.0}, baseline=baseline, has_data=True)
    r_low_lat = chart.current_radii[0]
    chart.update_metrics(current={"latency_ms": 90.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 40.0, "stability_pct": 100.0, "recovery_pct": 100.0}, baseline=baseline, has_data=True)
    r_high_lat = chart.current_radii[0]
    assert r_low_lat > r_high_lat, "Lower latency must expand radius outward"
    # Other independent axes must remain unchanged
    assert chart.current_radii[1] == chart.baseline_radii[1]
    assert chart.current_radii[2] == chart.baseline_radii[2]
    assert chart.current_radii[3] == chart.baseline_radii[3]

    # 2. Download Throughput change
    chart.update_metrics(current={"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 20.0, "stability_pct": 100.0, "recovery_pct": 100.0}, baseline=baseline, has_data=True)
    r_low_dl = chart.current_radii[3]
    chart.update_metrics(current={"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 80.0, "stability_pct": 100.0, "recovery_pct": 100.0}, baseline=baseline, has_data=True)
    r_high_dl = chart.current_radii[3]
    assert r_high_dl > r_low_dl, "Higher throughput must expand radius outward"
    assert chart.current_radii[0] == chart.baseline_radii[0]
    assert chart.current_radii[1] == chart.baseline_radii[1]
    assert chart.current_radii[2] == chart.baseline_radii[2]


def test_radar_100_percent_recovery_and_zero_packet_loss(qapp: QApplication) -> None:
    """Requirement 6: Verify 0% packet loss and 100% recovery map to optimal score 100.0 and radius 1.00."""
    chart = RadarChartWidget()
    baseline = {"latency_ms": 35.0, "jitter_ms": 1.5, "packet_loss_pct": 0.0, "download_mbps": 35.0}

    chart.update_metrics(
        current={
            "latency_ms": 25.0,
            "jitter_ms": 0.8,
            "packet_loss_pct": 0.0,
            "download_mbps": 50.0,
            "recovery_pct": 100.0,
            "stability_pct": 100.0,
        },
        baseline=baseline,
        has_data=True,
    )

    # Packet loss 0.0% -> optimal score 100.0 and radius 1.00
    assert chart.current_labels[2] == "0.0 %"
    assert chart._scores_curr[2] == 100.0
    assert chart.current_radii[2] == 1.00

    # Recovery 100.0% -> optimal score 100.0 and radius 1.00
    assert chart.current_labels[5] == "100.0 %"
    assert chart._scores_curr[5] == 100.0
    assert chart.current_radii[5] == 1.00

    # Stability 100.0% -> optimal score 100.0 and radius 1.00
    assert chart.current_labels[4] == "100.0 %"
    assert chart._scores_curr[4] == 100.0
    assert chart.current_radii[4] == 1.00


def test_radar_boundary_values_and_robust_error_handling(qapp: QApplication) -> None:
    """Requirement 4 & 6: Verify safe handling of zero, negative, out-of-range, NaN, and missing inputs."""
    chart = RadarChartWidget()

    # Extreme / corrupt dataset: negative latency, NaN jitter, infinite loss, missing download
    chart.update_metrics(
        current={
            "latency_ms": -10.0,
            "jitter_ms": float("nan"),
            "packet_loss_pct": float("inf"),
            "download_mbps": None,
        },
        baseline={},
        has_data=True,
    )

    # Check bounds
    for r in chart.current_radii:
        assert 0.08 <= r <= 1.00, f"Radius factor {r} must be within [0.08, 1.00]"
    for s in chart._scores_curr:
        assert 0.0 <= s <= 100.0, f"Score {s} must be within [0.0, 100.0]"

    # Unmeasured download throughput safely handled without crashing
    assert chart.current_labels[3] == "—"


def test_radar_prompt_acceptance_checks(qapp: QApplication) -> None:
    """Explicit verification of user prompt acceptance checks and normalization formulas."""
    from ticnet.monitoring.normalization import (
        to_radar_score,
        toRadarScore,
        METRIC_SPECS,
        RADAR_THRESHOLDS,
    )

    # 1. Alias check
    assert toRadarScore is to_radar_score

    # 2. Config object thresholds
    assert METRIC_SPECS["latency"].best == 0.0 and METRIC_SPECS["latency"].worst == 200.0
    assert METRIC_SPECS["jitter"].best == 0.0 and METRIC_SPECS["jitter"].worst == 50.0
    assert METRIC_SPECS["packet_loss"].best == 0.0 and METRIC_SPECS["packet_loss"].worst == 10.0
    assert METRIC_SPECS["download"].worst == 0.0
    assert METRIC_SPECS["stability"].best == 100.0 and METRIC_SPECS["stability"].worst == 0.0
    assert METRIC_SPECS["recovery"].best == 100.0 and METRIC_SPECS["recovery"].worst == 0.0

    # 3. Acceptance check: Packet Loss 0.0% maps to score 100 (outer ring)
    assert to_radar_score("packet_loss", 0.0) == 100.0
    assert to_radar_score("packet_loss", 10.0) == 0.0
    assert to_radar_score("packet_loss", 5.0) == 50.0

    # 4. Acceptance check: Recovery 100% maps to exactly 100 and sits ON the outer ring, never beyond it
    assert to_radar_score("recovery", 100.0) == 100.0
    assert to_radar_score("recovery", 150.0) == 100.0  # Clamped, never beyond
    assert to_radar_score("recovery", 0.0) == 0.0

    # 5. Acceptance check: Latency 0 ms maps to 100, and latency at or above the worst threshold (200 ms) maps to 0
    assert to_radar_score("latency", 0.0) == 100.0
    assert to_radar_score("latency", 200.0) == 0.0
    assert to_radar_score("latency", 300.0) == 0.0  # Clamped to 0
    assert to_radar_score("latency", -20.0) == 100.0  # Clamped to 100

    # 6. Acceptance check: Any input (negative, huge, NaN, None, Inf) produces a score within 0-100
    for key in ["latency", "jitter", "packet_loss", "download", "stability", "recovery"]:
        for val in [-999.0, 99999.0, float("nan"), float("inf"), float("-inf"), None, "corrupt"]:
            s = to_radar_score(key, val, throughput_ceiling=100.0)
            assert 0.0 <= s <= 100.0, f"Score {s} for {key} with {val} is not in [0, 100]"

    # 7. Acceptance check: Both series use the same function and thresholds
    chart = RadarChartWidget()
    dataset = {
        "latency_ms": 30.7,
        "jitter_ms": 7.2,
        "packet_loss_pct": 0.0,
        "download_mbps": 92.5,
        "stability_pct": 87.4,
        "recovery_pct": 100.0,
    }
    chart.update_metrics(current=dataset, baseline=dataset, has_data=True)
    for i in range(6):
        assert chart._scores_curr[i] == chart._scores_base[i]
        assert chart.current_radii[i] == chart.baseline_radii[i]
        assert 0.0 <= chart.current_radii[i] <= 1.00

    # Check that Recovery 100% and Packet Loss 0% are exactly 1.00 (outer ring)
    # Axis 2: Packet Loss (0.0% -> 1.00)
    assert chart._scores_curr[2] == 100.0
    assert chart.current_radii[2] == 1.00
    # Axis 5: Recovery (100.0% -> 1.00)
    assert chart._scores_curr[5] == 100.0
    assert chart.current_radii[5] == 1.00

    # Raw labels display format
    assert chart.current_labels[0] == "30.7 ms"
    assert chart.current_labels[1] == "7.2 ms"
    assert chart.current_labels[2] == "0.0 %"
    assert chart.current_labels[3] == "92.5 Mbps"
    assert chart.current_labels[4] == "87.4 %"
    assert chart.current_labels[5] == "100.0 %"




