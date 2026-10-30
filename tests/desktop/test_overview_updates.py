"""Tests for updated Overview Page, InteractiveGlobeWidget, and OverviewWalkthroughDialog."""

import pytest
from PySide6 import QtCore
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from ticnet.desktop.dialogs.overview_walkthrough_dialog import OverviewWalkthroughDialog
from ticnet.desktop.pages.overview_page import OverviewPage
from ticnet.desktop.widgets.interactive_globe_widget import InteractiveGlobeWidget





def test_tuhin_mondal_removed_from_overview(qapp):
    """Verify that Tuhin Mondal is no longer present anywhere on the Overview page."""
    page = OverviewPage()
    for child in page.findChildren(QLabel):
        assert "Tuhin Mondal" not in child.text()
        assert "PingPlotter-Inspired Topology" not in child.text()


def test_watch_overview_button_exists_and_connected(qapp):
    """Verify btn_watch_overview is present on OverviewPage."""
    page = OverviewPage()
    assert hasattr(page, "btn_watch_overview")
    assert page.btn_watch_overview is not None
    assert "Watch Overview" in page.btn_watch_overview.text()


def test_overview_walkthrough_dialog_lifecycle(qapp):
    """Verify OverviewWalkthroughDialog steps, autoplay, and start diagnosis action."""
    start_called = False

    def on_start():
        nonlocal start_called
        start_called = True

    dialog = OverviewWalkthroughDialog(on_start_diagnosis=on_start)
    assert dialog._total_steps == 4
    assert dialog._current_step == 0
    assert "Step 1 of 4" in dialog.lbl_step_counter.text()

    # Next step
    dialog._on_next_clicked()
    assert dialog._current_step == 1
    assert "Step 2 of 4" in dialog.lbl_step_counter.text()

    # Previous step
    dialog._on_prev_clicked()
    assert dialog._current_step == 0

    # Auto-play
    dialog._toggle_autoplay()
    assert dialog._is_playing is True
    dialog._toggle_autoplay()
    assert dialog._is_playing is False

    # Start diagnosis CTA
    dialog._on_start_diag_clicked()
    assert start_called is True
    dialog.close()


def test_interactive_globe_widget_speed_and_render(qapp):
    """Verify InteractiveGlobeWidget rotates at brisk speed and renders successfully."""
    globe = InteractiveGlobeWidget()
    globe.resize(340, 330)

    # Smooth speed and right tilt verification
    assert globe.base_speed >= 0.35
    assert hasattr(globe, "tilt_deg") and globe.tilt_deg > 0
    assert globe._timer.interval() <= 25

    # Check projection
    x, y, z = globe._project_lat_lon(0.0, 0.0, 100.0)
    assert isinstance(x, float)
    assert isinstance(y, float)
    assert isinstance(z, float)

    # Ensure paint event executes cleanly
    globe.repaint()
    globe.close()


def test_live_network_status_rows_removed_and_global_ping_widget(qapp):
    """Verify Live Network Status rows (Fiber, Cellular, Satellite, Fixed Wireless) are removed

    and that GlobalPingGraphWidget is dedicated with adaptive scaling.
    """
    page = OverviewPage()
    for child in page.findChildren(QLabel):
        txt = child.text()
        assert "Live Network Status" not in txt
        assert "All systems operational" not in txt
        assert "Fiber" not in txt
        assert "Fixed Wireless" not in txt
        assert "Satellite" not in txt

    # Dedicated GlobalPingGraphWidget validation
    assert hasattr(page, "ping_graph_widget")
    ping_widget = page.ping_graph_widget
    assert ping_widget is not None
    assert ping_widget.get_ping_value() == 30.0

    # Dynamic scaling validation
    high, mid, low = ping_widget._calculate_scale_levels(30.0)
    assert (high, mid, low) == (45.0, 30.0, 15.0)

    high74, mid74, low74 = ping_widget._calculate_scale_levels(74.0)
    assert (high74, mid74, low74) == (85.0, 70.0, 55.0)

    # Telemetry update
    ping_widget.set_ping_value(42.0, 3.1)
    assert ping_widget.get_ping_value() == 42.0
    assert ping_widget._jitter == 3.1

    # Render paint check
    ping_widget.repaint()
    page.close()

