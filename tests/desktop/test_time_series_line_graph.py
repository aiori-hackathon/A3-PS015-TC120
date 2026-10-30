"""Unit and integration tests for TimeSeriesLineGraphWidget and analytics dashboard line graph."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
import pytest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ticnet.desktop.services.live_telemetry_service import LiveTelemetrySample
from ticnet.desktop.services.monitoring_service import MonitoringService
from ticnet.desktop.widgets.network_analytics_widget import NetworkAnalyticsWidget
from ticnet.desktop.widgets.time_series_line_graph_widget import (
    METRIC_CONFIGS,
    TimeSeriesLineGraphWidget,
    TimeSeriesPoint,
)
from ticnet.models.monitoring import (
    IntervalType,
    MonitoringRun,
    MonitoringRunStatus,
    MonitoringSchedule,
    MonitoringTriggerType,
)
from ticnet.monitoring.analytics import DetectionStatus, TimeWindow
from ticnet.persistence.database import DatabaseManager


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_line_graph_analytics.sqlite3"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def monitoring_service(test_db: DatabaseManager) -> MonitoringService:
    service = MonitoringService(db=test_db)
    yield service
    service.shutdown()


@pytest.fixture
def mock_points() -> List[TimeSeriesPoint]:
    """Sample chronological telemetry points."""
    base_time = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc)
    pts = []
    # 24 data points, one per hour
    for h in range(25):
        dt = base_time + timedelta(hours=h)
        # Latency fluctuates around 35ms with a peak at 12:30
        lat = 30.0 + (12.6 if h == 12 else (h % 5) * 1.5)
        base = 38.0
        pts.append(
            TimeSeriesPoint(
                timestamp_dt=dt,
                actual_value=lat,
                baseline_value=base,
                time_str=dt.strftime("%H:%M"),
                tooltip_time_str=dt.strftime("%Y-%m-%d %H:%M"),
            )
        )
    return pts


def test_time_series_point_creation(mock_points: List[TimeSeriesPoint]) -> None:
    """Verify TimeSeriesPoint attributes and chronological ordering."""
    assert len(mock_points) == 25
    assert mock_points[0].time_str == "00:00"
    assert mock_points[12].actual_value == 42.6
    assert mock_points[12].baseline_value == 38.0
    assert mock_points[12].tooltip_time_str == "2026-10-29 12:00"


def test_widget_set_data_and_metrics(qapp: QApplication, mock_points: List[TimeSeriesPoint]) -> None:
    """Verify widget data setter and metric configuration."""
    widget = TimeSeriesLineGraphWidget()
    widget.resize(600, 320)
    widget.set_data(mock_points, metric_key="latency", window=TimeWindow.H24)

    assert len(widget.points) == 25
    assert widget.active_metric_key == "latency"
    assert widget.active_window == TimeWindow.H24

    # Test metric switching
    widget.set_metric("jitter")
    assert widget.active_metric_key == "jitter"

    widget.set_metric("loss")
    assert widget.active_metric_key == "loss"

    widget.set_metric("download")
    assert widget.active_metric_key == "download"

    widget.set_window(TimeWindow.D7)
    assert widget.active_window == TimeWindow.D7

    widget.close()


def test_widget_render_paint_event(qapp: QApplication, mock_points: List[TimeSeriesPoint]) -> None:
    """Verify that paintEvent completes cleanly without errors across all metrics."""
    widget = TimeSeriesLineGraphWidget()
    widget.resize(600, 320)
    widget.show()

    pixmap = QPixmap(600, 320)
    for metric_key in METRIC_CONFIGS.keys():
        widget.set_data(mock_points, metric_key=metric_key, window=TimeWindow.H24)
        widget.render(pixmap)

    # Empty state rendering
    widget.set_data([], metric_key="latency")
    widget.render(pixmap)

    widget.close()


def test_widget_mouse_hover_and_tooltip(qapp: QApplication, mock_points: List[TimeSeriesPoint]) -> None:
    """Verify interactive mouse tracking and hover state updates."""
    widget = TimeSeriesLineGraphWidget()
    widget.resize(600, 320)
    widget.show()
    widget.set_data(mock_points, metric_key="latency", window=TimeWindow.H24)

    pixmap = QPixmap(600, 320)
    widget.render(pixmap)

    # Coordinates cached after render
    assert len(widget._cached_actual_pts) == 25

    # Simulate mouse movement to the middle point (around hour 12)
    mid_px, mid_py, mid_idx = widget._cached_actual_pts[12]
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(mid_px, mid_py),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(event)

    assert widget._hover_idx == 12

    # Simulate mouse moving far away
    far_event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(10.0, 10.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(far_event)
    assert widget._hover_idx is None

    widget.close()


def test_network_analytics_widget_line_graph_integration(
    qapp: QApplication, monitoring_service: MonitoringService
) -> None:
    """Verify NetworkAnalyticsWidget initializes TimeSeriesLineGraphWidget and binds data."""
    # Seed historical runs in database
    now = datetime.now(timezone.utc)
    for i in range(5):
        run = MonitoringRun(
            monitoring_run_id=f"run-{i}",
            schedule_id="default",
            trigger_type=MonitoringTriggerType.SCHEDULED.value,
            scheduled_for_utc=(now - timedelta(hours=5 - i)).isoformat(),
            started_at_utc=(now - timedelta(hours=5 - i)).isoformat(),
            completed_at_utc=(now - timedelta(hours=5 - i) + timedelta(seconds=15)).isoformat(),
            status=MonitoringRunStatus.COMPLETED.value,
            latency_ms=30.0 + i * 2.0,
            jitter_ms=1.2 + i * 0.1,
            packet_loss_pct=0.0,
            download_mbps=85.0 - i * 2.0,
        )
        monitoring_service.scheduler.repo.create_monitoring_run(run)

    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.resize(700, 480)
    widget.show()

    # Widget should display View 0 (the line graph)
    assert widget.chart_stack.currentIndex() == 0
    assert hasattr(widget, "line_graph_widget")
    assert isinstance(widget.line_graph_widget, TimeSeriesLineGraphWidget)
    assert len(widget.line_graph_widget.points) >= 5

    # Health indicator should be Normal
    assert widget.lbl_health_pill.text() == "Normal"

    # Test metric dropdown switching
    widget.combo_metric.setCurrentIndex(1)  # Delay Variation / Jitter
    assert widget.line_graph_widget.active_metric_key == "jitter"

    widget.combo_metric.setCurrentIndex(2)  # Packet Loss
    assert widget.line_graph_widget.active_metric_key == "loss"

    widget.combo_metric.setCurrentIndex(3)  # Download Throughput
    assert widget.line_graph_widget.active_metric_key == "download"

    # Test time window switching
    QTest.mouseClick(widget.btn_7d, Qt.MouseButton.LeftButton)
    assert widget._active_window == TimeWindow.D7
    assert widget.line_graph_widget.active_window == TimeWindow.D7

    QTest.mouseClick(widget.btn_30d, Qt.MouseButton.LeftButton)
    assert widget._active_window == TimeWindow.D30
    assert widget.line_graph_widget.active_window == TimeWindow.D30

    QTest.mouseClick(widget.btn_24h, Qt.MouseButton.LeftButton)
    assert widget._active_window == TimeWindow.H24
    assert widget.line_graph_widget.active_window == TimeWindow.H24

    widget.close()


def test_network_analytics_live_telemetry_stream(
    qapp: QApplication, monitoring_service: MonitoringService
) -> None:
    """Verify real-time live telemetry stream updates the line graph dynamically."""
    widget = NetworkAnalyticsWidget(monitoring_service=monitoring_service)
    widget.resize(700, 480)
    widget.show()

    # Emit normal live sample
    sample_normal = LiveTelemetrySample(
        timestamp=1000.0,
        time_str="12:00:00",
        latency_ms=28.5,
        jitter_ms=1.1,
        packet_loss_pct=0.0,
        download_mbps=94.0,
        upload_mbps=22.0,
        min_latency_ms=27.0,
        avg_latency_ms=28.5,
        max_latency_ms=30.0,
        interface_name="Wi-Fi",
        gateway_ip="192.168.1.1",
        target_ip="1.1.1.1",
        is_connected=True,
    )
    widget.on_live_telemetry_sample(sample_normal)

    assert widget.lbl_health_pill.text() == "Normal"
    assert widget.card_lat.lbl_val.text() == "28.5 ms"
    assert widget.card_dl.lbl_val.text() == "94.0 Mbps"

    # Emit degraded live sample (high packet loss and high latency)
    sample_degraded = LiveTelemetrySample(
        timestamp=1005.0,
        time_str="12:00:05",
        latency_ms=125.0,
        jitter_ms=14.2,
        packet_loss_pct=5.0,
        download_mbps=15.0,
        upload_mbps=5.0,
        min_latency_ms=27.0,
        avg_latency_ms=75.0,
        max_latency_ms=140.0,
        interface_name="Wi-Fi",
        gateway_ip="192.168.1.1",
        target_ip="1.1.1.1",
        is_connected=True,
    )
    widget.on_live_telemetry_sample(sample_degraded)

    assert widget.lbl_health_pill.text() == "Degraded"
    assert widget.card_lat.lbl_val.text() == "125.0 ms"
    assert widget.card_loss.lbl_val.text() == "5.0 %"

    widget.close()
