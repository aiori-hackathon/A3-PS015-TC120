"""Unit and integration tests for RadialGaugeWidget (Phase 3).

Verifies:
- Initial idle state defaults.
- Progress transitions at 0%, 20%, 40%, 60%, 80%, and 100%.
- Progress value clamping below 0 and above 100.
- Safe handling of invalid progress inputs.
- All supported overall execution states.
- Active-stage label updates.
- Metric label updates and clearing.
- Reset behavior.
- Safe animation interruption by subsequent updates.
- Cancellation preserving last confirmed progress value.
- Widget resizing and offscreen rendering across dimension extremes.
- Stability during repeated rapid updates.
"""

from __future__ import annotations

import math
import os
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from ticnet.desktop.widgets.radial_gauge_widget import GaugeState, RadialGaugeWidget


@pytest.fixture
def gauge(qapp: QApplication) -> RadialGaugeWidget:
    """Create a fresh RadialGaugeWidget instance for testing."""
    widget = RadialGaugeWidget()
    return widget


def test_radial_gauge_initial_state(gauge: RadialGaugeWidget):
    """Verify default initial state of the radial gauge."""
    assert gauge.progress == 0.0
    assert gauge.displayed_progress == 0.0
    assert gauge.status == GaugeState.IDLE
    assert gauge.active_stage == ""
    assert gauge.current_metric is None
    assert not gauge.is_animating
    assert gauge.sizeHint() == QSize(220, 220)
    assert gauge.minimumSizeHint() == QSize(140, 140)


def test_radial_gauge_progress_steps(gauge: RadialGaugeWidget):
    """Verify setting progress at 0%, 20%, 40%, 60%, 80%, and 100%."""
    steps = [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]
    for step in steps:
        gauge.set_progress(step, animate=False)
        assert gauge.progress == pytest.approx(step, 0.001)
        assert gauge.displayed_progress == pytest.approx(step, 0.001)


def test_radial_gauge_progress_clamping(gauge: RadialGaugeWidget):
    """Verify that progress values below 0 and above 100 are strictly clamped."""
    gauge.set_progress(-15.0, animate=False)
    assert gauge.progress == 0.0
    assert gauge.displayed_progress == 0.0

    gauge.set_progress(-0.01, animate=False)
    assert gauge.progress == 0.0

    gauge.set_progress(105.5, animate=False)
    assert gauge.progress == 100.0
    assert gauge.displayed_progress == 100.0

    gauge.set_progress(9999.0, animate=False)
    assert gauge.progress == 100.0


def test_radial_gauge_invalid_progress_input(gauge: RadialGaugeWidget):
    """Verify that invalid progress values are safely rejected without raising exceptions."""
    gauge.set_progress(45.0, animate=False)
    assert gauge.progress == 45.0

    # Non-numeric types
    gauge.set_progress(None, animate=False)
    assert gauge.progress == 45.0

    gauge.set_progress("not-a-number", animate=False)
    assert gauge.progress == 45.0

    gauge.set_progress([50], animate=False)
    assert gauge.progress == 45.0

    # Special float values
    gauge.set_progress(float("nan"), animate=False)
    assert gauge.progress == 45.0

    gauge.set_progress(float("inf"), animate=False)
    assert gauge.progress == 45.0

    gauge.set_progress(float("-inf"), animate=False)
    assert gauge.progress == 45.0


def test_radial_gauge_supported_states(gauge: RadialGaugeWidget):
    """Verify all supported overall execution states via enum and string."""
    expected_states = [
        (GaugeState.IDLE, "READY"),
        (GaugeState.RUNNING, "RUNNING"),
        (GaugeState.COMPLETED, "COMPLETED"),
        (GaugeState.COMPLETED_WITH_WARNINGS, "WARNING"),
        (GaugeState.FAILED, "FAILED"),
        (GaugeState.CANCELLED, "CANCELLED"),
    ]

    for state_enum, expected_label in expected_states:
        gauge.set_status(state_enum)
        assert gauge.status == state_enum
        assert gauge._get_status_label_text() == expected_label

    # String variations (case-insensitive and heuristics)
    gauge.set_status("idle")
    assert gauge.status == GaugeState.IDLE

    gauge.set_status("RUNNING")
    assert gauge.status == GaugeState.RUNNING

    gauge.set_status("completed")
    assert gauge.status == GaugeState.COMPLETED

    gauge.set_status("completed_with_warnings")
    assert gauge.status == GaugeState.COMPLETED_WITH_WARNINGS

    gauge.set_status("warning")
    assert gauge.status == GaugeState.COMPLETED_WITH_WARNINGS

    gauge.set_status("failed")
    assert gauge.status == GaugeState.FAILED

    gauge.set_status("cancelled")
    assert gauge.status == GaugeState.CANCELLED

    # Invalid string fallback to IDLE
    gauge.set_status("unknown_custom_status")
    assert gauge.status == GaugeState.IDLE


def test_radial_gauge_active_stage_updates(gauge: RadialGaugeWidget):
    """Verify active-stage label updates and clearing."""
    gauge.set_active_stage("Stage 1: Interface & Local Gateway")
    assert gauge.active_stage == "Stage 1: Interface & Local Gateway"

    gauge.set_active_stage("Stage 2: DNS Resolution")
    assert gauge.active_stage == "Stage 2: DNS Resolution"

    gauge.set_active_stage(None)
    assert gauge.active_stage == ""


def test_radial_gauge_metric_label_updates_and_clearing(gauge: RadialGaugeWidget):
    """Verify metric label updates and clearing."""
    gauge.set_current_metric("Gateway RTT: 2.1 ms")
    assert gauge.current_metric == "Gateway RTT: 2.1 ms"

    gauge.set_current_metric("Packet Loss: 0.0%")
    assert gauge.current_metric == "Packet Loss: 0.0%"

    # Clearing via None
    gauge.set_current_metric(None)
    assert gauge.current_metric is None

    # Clearing via empty / whitespace string
    gauge.set_current_metric("  ")
    assert gauge.current_metric is None


def test_radial_gauge_reset_behavior(gauge: RadialGaugeWidget):
    """Verify reset behavior restoring gauge to pristine idle state."""
    gauge.set_progress(75.0, animate=False)
    gauge.set_status(GaugeState.RUNNING)
    gauge.set_active_stage("Stage 3: Upstream Path")
    gauge.set_current_metric("RTT: 14.2 ms")

    gauge.reset()

    assert gauge.progress == 0.0
    assert gauge.displayed_progress == 0.0
    assert gauge.status == GaugeState.IDLE
    assert gauge.active_stage == ""
    assert gauge.current_metric is None
    assert not gauge.is_animating


def test_radial_gauge_animation_interruption(gauge: RadialGaugeWidget):
    """Verify animation handles rapid updates without race conditions or leaks."""
    # Start first animation
    gauge.set_progress(40.0, animate=True)
    assert gauge.progress == 40.0
    assert gauge.is_animating

    # Interrupt with a second update
    gauge.set_progress(70.0, animate=True)
    assert gauge.progress == 70.0
    assert gauge.is_animating

    # Immediate non-animated override stops the animation
    gauge.set_progress(85.0, animate=False)
    assert gauge.progress == 85.0
    assert gauge.displayed_progress == 85.0
    assert not gauge.is_animating


def test_radial_gauge_cancellation_preserves_progress(gauge: RadialGaugeWidget):
    """Verify cancellation retains the last confirmed progress value."""
    gauge.set_progress(58.5, animate=False)
    gauge.set_status(GaugeState.RUNNING)
    assert gauge.progress == 58.5

    gauge.set_status(GaugeState.CANCELLED)
    assert gauge.status == GaugeState.CANCELLED
    # Progress must NOT be reset to 0 or jumped to 100
    assert gauge.progress == 58.5
    assert gauge.displayed_progress == 58.5


def test_radial_gauge_resizing_and_offscreen_rendering(gauge: RadialGaugeWidget):
    """Verify rendering at various dimensions including extremes."""
    dimensions = [
        (220, 220),  # Standard
        (140, 140),  # Minimum recommended
        (100, 260),  # Narrow vertical
        (350, 180),  # Wide horizontal
        (400, 400),  # Large
        (30, 30),    # Sub-minimum edge case
        (0, 0),      # Degenerate
    ]

    gauge.set_progress(62.0, animate=False)
    gauge.set_status(GaugeState.RUNNING)
    gauge.set_active_stage("Stage 2: Gateway Latency Testing (Detailed)")
    gauge.set_current_metric("RTT: 2.1 ms | Jitter: 0.4 ms")

    for w, h in dimensions:
        gauge.resize(w, h)
        if w > 0 and h > 0:
            img = QImage(w, h, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.white)
            gauge.render(img)


def test_radial_gauge_repeated_updates_stability(gauge: RadialGaugeWidget):
    """Verify widget stability under repeated rapid updates."""
    for i in range(100):
        val = (i * 3.7) % 105.0
        gauge.set_progress(val, animate=False)
        gauge.set_active_stage(f"Rapid Stage Update #{i}")
        if i % 3 == 0:
            gauge.set_current_metric(f"Telemetry Metric {i}")
        else:
            gauge.set_current_metric(None)

        if i == 90:
            gauge.set_status(GaugeState.COMPLETED_WITH_WARNINGS)

    assert gauge.status == GaugeState.COMPLETED_WITH_WARNINGS
    assert gauge.progress <= 100.0
