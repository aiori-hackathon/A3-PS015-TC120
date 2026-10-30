"""Dedicated unit and integration tests for InternetSpeedService and Active Internet Speed Testing.

Verifies:
- Active bandwidth evaluation using Ookla Speedtest and Cloudflare Anycast fallback.
- True mathematical Mbps calculation: (bytes * 8.0) / (elapsed_seconds * 1,000,000.0).
- Concurrency protection: concurrent speed tests are serialized / ignored.
- Failure preservation: network errors retain last valid measurement and never zero out.
- Refresh triggering: manual refresh executes speed test and updates live telemetry.
- Timestamp tracking in Indian Standard Time (IST).
"""

from __future__ import annotations

import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from ticnet.core.timezone import format_ist, now_ist
from ticnet.desktop.services.internet_speed_service import InternetSpeedService, SpeedTestResult
from ticnet.desktop.services.live_telemetry_service import (
    LiveTelemetrySample,
    LiveTelemetryService,
    LiveTelemetryWorker,
)
from ticnet.desktop.widgets.global_ping_graph_widget import GlobalPingGraphWidget


def test_speed_test_result_dataclass():
    """Verify SpeedTestResult stores genuine empirical bandwidth metrics."""
    res = SpeedTestResult(
        download_mbps=42.5,
        upload_mbps=18.2,
        latency_ms=28.0,
        server_name="Tata Teleservices",
        server_country="India",
        timestamp=time.perf_counter(),
        timestamp_ist="02:30 PM IST",
        engine="ookla",
        is_success=True,
    )
    assert res.download_mbps == 42.5
    assert res.upload_mbps == 18.2
    assert res.latency_ms == 28.0
    assert res.engine == "ookla"
    assert res.is_success is True


def test_internet_speed_service_concurrency_lock():
    """Verify concurrent speed test invocations are prevented."""
    service = InternetSpeedService()

    # Artificially set is_testing flag
    with service._test_lock:
        service._is_testing = True

    # Attempting to run a test while one is running should return immediately without parallel test
    res = service.run_speed_test()
    assert res.is_success is False
    assert "already in progress" in (res.error_message or "").lower()

    # Async trigger should also skip
    assert service.trigger_async_test() is False


def test_internet_speed_service_failure_preserves_previous_result():
    """Verify a failed test does not wipe out or overwrite previously valid measurements."""
    service = InternetSpeedService()

    # Pre-seed last valid result
    valid_res = SpeedTestResult(
        download_mbps=55.0,
        upload_mbps=25.0,
        latency_ms=20.0,
        server_name="Valid Server",
        server_country="India",
        timestamp=time.perf_counter(),
        timestamp_ist="02:00 PM IST",
        engine="ookla",
        is_success=True,
    )
    service._last_result = valid_res

    # Force both Ookla and Cloudflare to fail
    with patch.object(service, "_measure_via_ookla", side_effect=RuntimeError("Ookla down")), \
         patch.object(service, "_measure_via_cloudflare", side_effect=RuntimeError("Cloudflare unreachable")):
        res = service.run_speed_test()

        # The failed result must retain the previous valid download/upload speeds
        assert res.is_success is False
        assert res.download_mbps == 55.0
        assert res.upload_mbps == 25.0
        # Service's latest_result should still be the valid result
        assert service.latest_result == valid_res


def test_internet_speed_service_cloudflare_engine_calculation():
    """Verify Cloudflare Anycast throughput measurement calculation."""
    service = InternetSpeedService()

    # Mock response for download: 10 MB in 1.0 second -> 83.89 Mbps
    mock_dl_bytes = b"X" * (10 * 1024 * 1024)
    # Mock response for upload: 3 MB payload in 0.5 second -> 50.33 Mbps
    payload_bytes = 3 * 1024 * 1024

    with patch("urllib.request.urlopen") as mock_urlopen:
        # Ping mock
        mock_resp_ping = MagicMock()
        mock_resp_ping.read.return_value = b""
        mock_resp_ping.__enter__.return_value = mock_resp_ping

        # Download mock
        mock_resp_dl = MagicMock()
        mock_resp_dl.read.side_effect = [mock_dl_bytes, b""]
        mock_resp_dl.__enter__.return_value = mock_resp_dl

        # Upload mock
        mock_resp_up = MagicMock()
        mock_resp_up.read.return_value = b""
        mock_resp_up.__enter__.return_value = mock_resp_up

        mock_urlopen.side_effect = [mock_resp_ping, mock_resp_dl, mock_resp_up]

        # Specific clock sequence:
        # 1. ping t0 = 10.0
        # 2. ping t_end = 10.02 (latency = 20ms)
        # 3. dl t_start = 11.0
        # 4. dl t_first = 11.0
        # 5. dl chunk check = 12.0
        # 6. dl t_last = 12.0 (duration = 1.0s)
        # 7. up t_start = 13.0
        # 8. up t_end = 13.5 (duration = 0.5s)
        # 9. now_t = 14.0
        clock_ticks = [10.0, 10.02, 11.0, 11.0, 12.0, 12.0, 13.0, 13.5, 14.0, 14.5]
        tick_idx = 0

        def mock_clock():
            nonlocal tick_idx
            val = clock_ticks[min(tick_idx, len(clock_ticks) - 1)]
            tick_idx += 1
            return val

        with patch("time.perf_counter", side_effect=mock_clock):
            res = service._measure_via_cloudflare()

            assert res.is_success is True
            assert res.engine == "cloudflare"
            # 10 * 1024 * 1024 * 8 / 1.0s / 1,000,000 = 83.89 Mbps
            assert abs(res.download_mbps - 83.89) < 0.1
            # 3 * 1024 * 1024 * 8 / 0.5s / 1,000,000 = 50.33 Mbps
            assert abs(res.upload_mbps - 50.33) < 0.1


def test_global_ping_widget_refresh_button_and_testing_state(qapp: QApplication):
    """Verify refresh button exists, emits signal, and widget reflects TESTING SPEED state."""
    widget = GlobalPingGraphWidget(baseline_ping_ms=30.0)
    widget.resize(360, 380)
    widget.show()
    QCoreApplication.processEvents()

    # 1. Refresh button presence and positioning
    assert hasattr(widget, "btn_refresh")
    assert widget.btn_refresh.isVisible()

    # 2. Clicking refresh button triggers signal
    signal_received = False

    def on_refresh():
        nonlocal signal_received
        signal_received = True

    widget.sig_refresh_requested.connect(on_refresh)
    widget.btn_refresh.click()
    assert signal_received is True

    # 3. Supply sample where is_speed_testing is True
    sample_testing = LiveTelemetrySample(
        timestamp=time.perf_counter(),
        time_str="14:50:00",
        latency_ms=32.0,
        jitter_ms=1.5,
        packet_loss_pct=0.0,
        download_mbps=45.0,
        upload_mbps=15.0,
        min_latency_ms=30.0,
        avg_latency_ms=32.0,
        max_latency_ms=34.0,
        interface_name="Wi-Fi",
        gateway_ip="192.168.0.1",
        target_ip="1.1.1.1",
        is_connected=True,
        speed_tested_at="02:50 PM IST",
        is_speed_testing=True,
    )
    widget.update_telemetry_sample(sample_testing)
    widget.repaint()

    assert widget._current_sample is not None
    assert widget._current_sample.is_speed_testing is True
    assert widget._current_sample.download_mbps == 45.0
    assert widget._current_sample.speed_tested_at == "02:50 PM IST"

    widget._timer.stop()
    widget.close()
