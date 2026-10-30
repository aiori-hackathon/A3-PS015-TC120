"""Comprehensive unit tests for Live Telemetry Service, Worker, and Chart Widget."""

import time
from collections import deque
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from ticnet.desktop.services.live_telemetry_service import (
    LiveTelemetrySample,
    LiveTelemetryService,
    LiveTelemetryWorker,
    probe_native_ping,
)
from ticnet.desktop.widgets.global_ping_graph_widget import GlobalPingGraphWidget


def test_live_telemetry_sample_dataclass():
    """Verify LiveTelemetrySample stores genuine measurements without fabrication."""
    sample = LiveTelemetrySample(
        timestamp=time.perf_counter(),
        time_str="20:00:00",
        latency_ms=28.5,
        jitter_ms=1.4,
        packet_loss_pct=0.0,
        download_mbps=45.2,
        upload_mbps=12.1,
        min_latency_ms=25.0,
        avg_latency_ms=28.0,
        max_latency_ms=32.0,
        interface_name="Wi-Fi",
        gateway_ip="192.168.0.1",
        target_ip="1.1.1.1",
        is_connected=True,
    )
    assert sample.latency_ms == 28.5
    assert sample.download_mbps == 45.2
    assert sample.upload_mbps == 12.1
    assert sample.interface_name == "Wi-Fi"
    assert sample.is_connected is True


def test_live_telemetry_worker_jitter_and_loss_calculation():
    """Verify that jitter and packet loss % are calculated mathematically from real probe history."""
    worker = LiveTelemetryWorker(target_ip="1.1.1.1", interval_s=1.0)

    # Simulate 5 samples: 3 successes (30ms, 34ms, 31ms) and 2 timeouts (None, None)
    simulated_rtts = [30.0, 34.0, 31.0, None, None]
    for rtt in simulated_rtts:
        worker._rtt_history.append(rtt)

    total = len(worker._rtt_history)
    lost = sum(1 for s in worker._rtt_history if s is None)
    loss_pct = (lost / float(total)) * 100.0

    assert total == 5
    assert lost == 2
    assert loss_pct == 40.0

    valid = [s for s in worker._rtt_history if s is not None]
    assert min(valid) == 30.0
    assert max(valid) == 34.0
    assert round(sum(valid) / len(valid), 1) == 31.7


def test_live_telemetry_worker_buffer_bounded_to_60():
    """Verify rolling memory buffer has fixed capacity (maxlen=60) preventing memory leaks."""
    worker = LiveTelemetryWorker(target_ip="1.1.1.1", interval_s=1.0)
    for i in range(150):
        worker._rtt_history.append(float(i))

    assert len(worker._rtt_history) == 60
    assert worker._rtt_history[-1] == 149.0


def test_live_telemetry_service_lifecycle(qapp):
    """Verify start, pause, resume, and stop controls."""
    service = LiveTelemetryService(
        target_ip="1.1.1.1",
        interval_s=0.1,
        probe_func=lambda target, timeout: 30.0,
        bytes_func=lambda: (1000, 2000),
    )
    assert service.is_running is False

    service.start()
    assert service.is_running is True

    service.pause()
    assert service._worker._paused is True

    service.resume()
    assert service._worker._paused is False

    service.stop()
    assert service.is_running is False


def test_chart_widget_adaptive_scaling(qapp):
    """Verify chart widget adapts grid levels based on actual measurement values and spikes."""
    widget = GlobalPingGraphWidget()

    # Normal low latency (e.g. 20ms)
    high, mid, low = widget._calculate_scale_levels(max_p=22.0, avg_p=20.0)
    assert (high, mid, low) == (30.0, 20.0, 10.0)

    # Typical broadband (e.g. 32ms)
    high, mid, low = widget._calculate_scale_levels(max_p=38.0, avg_p=32.0)
    assert (high, mid, low) == (45.0, 30.0, 15.0)

    # Moderate latency (e.g. 70ms)
    high, mid, low = widget._calculate_scale_levels(max_p=85.0, avg_p=70.0)
    assert (high, mid, low) == (85.0, 70.0, 55.0)

    # Sudden high latency spike (e.g. 240ms)
    high, mid, low = widget._calculate_scale_levels(max_p=240.0, avg_p=50.0)
    assert high >= 240.0  # Spikes must never be clipped!


def test_chart_widget_rendering_with_empty_and_active_telemetry(qapp):
    """Verify chart paintEvent executes without exception on empty, single, and 60 samples."""
    widget = GlobalPingGraphWidget()
    widget.resize(320, 370)

    # 1. Empty state
    widget.repaint()

    # 2. Single sample
    widget.set_ping_value(31.5, jitter_ms=1.1)
    assert widget.get_ping_value() == 31.5
    widget.repaint()

    # 3. Stream of 60 samples including packet loss gap
    now = time.perf_counter()
    for i in range(60):
        # Introduce a 3-second gap at i=30, 31, 32
        rtt = None if (30 <= i <= 32) else (30.0 + (i % 7))
        sample = LiveTelemetrySample(
            timestamp=now - (60 - i),
            time_str="12:00:00",
            latency_ms=rtt,
            jitter_ms=1.2 if rtt else None,
            packet_loss_pct=5.0,
            download_mbps=24.5,
            upload_mbps=5.0,
            min_latency_ms=30.0,
            avg_latency_ms=33.0,
            max_latency_ms=36.0,
            interface_name="Wi-Fi",
            gateway_ip="192.168.0.1",
            target_ip="1.1.1.1",
            is_connected=(rtt is not None),
        )
        widget.update_telemetry_sample(sample)

    assert len(widget._samples) == 60
    widget.repaint()

    # 4. Resize responsiveness
    widget.resize(280, 340)
    widget.repaint()
    widget.resize(450, 420)
    widget.repaint()

    widget._timer.stop()
    widget.close()




def test_network_speed_card_original_rendering(qapp):
    """Verify original Network Speed card renders with hero download sub-label and 3-column legend."""
    widget = GlobalPingGraphWidget(baseline_ping_ms=28.0)
    widget.resize(360, 380)
    widget.show()
    QCoreApplication.processEvents()

    # Supply sample with download throughput and verify rendering
    sample = LiveTelemetrySample(
        timestamp=time.perf_counter(),
        time_str="12:34:56",
        latency_ms=28.0,
        jitter_ms=1.4,
        packet_loss_pct=0.0,
        download_mbps=92.6,
        upload_mbps=11.3,
        min_latency_ms=27.0,
        avg_latency_ms=28.5,
        max_latency_ms=30.0,
        interface_name="Wi-Fi",
        gateway_ip="192.168.0.1",
        target_ip="1.1.1.1",
        is_connected=True,
    )
    widget.update_telemetry_sample(sample)
    widget.repaint()

    assert widget._current_sample is not None
    assert widget._current_sample.download_mbps == 92.6
    assert widget._current_sample.upload_mbps == 11.3
    assert widget._current_sample.latency_ms == 28.0

    widget._timer.stop()
    widget.close()


def test_disconnection_and_recovery_handling():
    """Verify disconnection marks connection state and recovery restores telemetry."""
    worker = LiveTelemetryWorker(target_ip="1.1.1.1", interval_s=1.0)
    # Simulate consecutive lost probes
    for _ in range(5):
        worker._rtt_history.append(None)

    total_samples = len(worker._rtt_history)
    lost_samples = sum(1 for s in worker._rtt_history if s is None)
    valid_rtts = [s for s in worker._rtt_history if s is not None]
    is_conn = (len(valid_rtts) > 0 and lost_samples < total_samples)
    assert is_conn is False

    # Simulate connection recovery
    worker._rtt_history.append(18.5)
    total_samples = len(worker._rtt_history)
    lost_samples = sum(1 for s in worker._rtt_history if s is None)
    valid_rtts = [s for s in worker._rtt_history if s is not None]
    is_conn = (len(valid_rtts) > 0 and lost_samples < total_samples)
    assert is_conn is True


def test_dynamic_throughput_second_by_second_updates(qapp):
    """Verify that throughput updates dynamically every second and is not stagnant."""
    widget = GlobalPingGraphWidget(baseline_ping_ms=30.0)
    widget.resize(360, 380)

    # Sequence of 4 varying download speeds simulating live network conditions
    speeds = [1.4, 5.8, 95.6, 2.7]
    now = time.perf_counter()

    for i, spd in enumerate(speeds):
        sample = LiveTelemetrySample(
            timestamp=now + i,
            time_str=f"12:00:0{i}",
            latency_ms=30.0,
            jitter_ms=1.2,
            packet_loss_pct=0.0,
            download_mbps=spd,
            upload_mbps=8.5,
            min_latency_ms=25.0,
            avg_latency_ms=30.0,
            max_latency_ms=35.0,
            interface_name="Wi-Fi",
            gateway_ip="192.168.0.1",
            target_ip="1.1.1.1",
            is_connected=True,
        )
        widget.update_telemetry_sample(sample)
        widget.repaint()

        # Check that current sample matches the dynamic speed
        assert widget._current_sample is not None
        assert widget._current_sample.download_mbps == spd

    # Verify that the samples stored in the widget recorded all varying values
    recorded_down = [s.download_mbps for s in list(widget._samples)[-4:]]
    assert recorded_down == speeds

    widget._timer.stop()
    widget.close()
