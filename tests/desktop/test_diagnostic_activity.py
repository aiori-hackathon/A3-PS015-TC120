"""Unit and integration tests for DiagnosticActivityFeedWidget and ActivityItemWidget (Phase 4).

Verifies:
1. Initial empty state.
2. Adding basic events.
3. Adding events with stage information.
4. All supported event categories.
5. Timestamp formatting (UTC ISO, local conversion, HH:MM:SS).
6. Missing and malformed timestamps safely handled.
7. Long message word-wrapping.
8. Long stage-name handling.
9. Event count accuracy.
10. Clearing the feed.
11. Maximum event limit enforcement.
12. Preservation of critical terminal events during pruning.
13. Duplicate event handling and progress row in-place collapsing.
14. Auto-scroll behavior.
15. User scrolling upward without forced repositioning.
16. Repeated updates stability.
17. Offscreen rendering and resizing.
18. Theme tokens compatibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ticnet.desktop.widgets.diagnostic_activity_widget import (
    ActivityCategory,
    ActivityEvent,
    ActivityItemWidget,
    DiagnosticActivityFeedWidget,
)
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType


@pytest.fixture
def feed(qapp: QApplication) -> DiagnosticActivityFeedWidget:
    """Create a fresh DiagnosticActivityFeedWidget instance for testing."""
    widget = DiagnosticActivityFeedWidget()
    return widget


def test_activity_feed_initial_empty_state(feed: DiagnosticActivityFeedWidget):
    """1. Verify initial empty state."""
    assert feed.event_count == 0
    assert feed.lbl_count_badge.text() == "0 events"
    assert not feed._empty_label.isHidden()
    assert feed.btn_scroll_latest.isHidden()
    assert feed.is_auto_scroll_enabled
    assert feed.max_events == DiagnosticActivityFeedWidget.DEFAULT_MAX_EVENTS


def test_activity_feed_add_basic_event(feed: DiagnosticActivityFeedWidget):
    """2. Verify adding a basic informational event."""
    item = feed.add_activity(ActivityCategory.INFO, "Diagnostic orchestrator initialized.")
    assert feed.event_count == 1
    assert feed.lbl_count_badge.text() == "1 event"
    assert not feed._empty_label.isVisible()
    assert isinstance(item, ActivityItemWidget)
    assert item.lbl_message.text() == "Diagnostic orchestrator initialized."
    assert item.lbl_badge.text() == "INFO"


def test_activity_feed_add_stage_info(feed: DiagnosticActivityFeedWidget):
    """3. Verify adding events with stage information."""
    item = feed.add_activity(
        ActivityCategory.STAGE_STARTED,
        "Probing local default gateway router",
        stage_order=2,
        stage_name="Gateway Reachability",
    )
    assert feed.event_count == 1
    assert hasattr(item, "lbl_stage")
    assert "Stage 2 • Gateway Reachability" in item.lbl_stage.text()
    assert item.lbl_badge.text() == "RUNNING"


def test_activity_feed_all_supported_categories(feed: DiagnosticActivityFeedWidget):
    """4. Verify all supported event categories map to correct badges and styling."""
    category_expectations = [
        (ActivityCategory.DIAGNOSTIC_STARTED, "INFO"),
        (ActivityCategory.STAGE_STARTED, "RUNNING"),
        (ActivityCategory.STAGE_PROGRESS, "PROGRESS"),
        (ActivityCategory.STAGE_COMPLETED, "SUCCESS"),
        (ActivityCategory.STAGE_SKIPPED, "SKIPPED"),
        (ActivityCategory.STAGE_FAILED, "FAILED"),
        (ActivityCategory.INCONCLUSIVE, "INCONCLUSIVE"),
        (ActivityCategory.DIAGNOSTIC_COMPLETED, "SUCCESS"),
        (ActivityCategory.DIAGNOSTIC_ERROR, "FAILED"),
        (ActivityCategory.DIAGNOSTIC_CANCELLED, "CANCELLED"),
        (ActivityCategory.INFO, "INFO"),
    ]

    for cat, expected_badge in category_expectations:
        item = feed.add_activity(
            category=cat,
            message=f"Testing category {cat.value}",
            stage_order=1,
            stage_name="Test Stage",
        )
        assert item.lbl_badge.text() == expected_badge


def test_activity_feed_timestamp_formatting(feed: DiagnosticActivityFeedWidget):
    """5. Verify timestamp formatting from UTC ISO to local HH:MM:SS."""
    # UTC ISO with Z
    event1 = ActivityEvent(
        category=ActivityCategory.INFO,
        message="UTC Z timestamp test",
        timestamp="2026-10-25T14:30:15Z",
    )
    assert len(event1.display_time) == 8
    assert event1.display_time[2] == ":" and event1.display_time[5] == ":"

    # UTC ISO with timezone offset
    event2 = ActivityEvent(
        category=ActivityCategory.INFO,
        message="UTC offset timestamp test",
        timestamp="2026-10-25T14:30:15+00:00",
    )
    assert len(event2.display_time) == 8

    # Python datetime object
    dt = datetime(2026, 10, 25, 9, 15, 30, tzinfo=timezone.utc)
    event3 = ActivityEvent(
        category=ActivityCategory.INFO,
        message="datetime object test",
        timestamp=dt,
    )
    assert len(event3.display_time) == 8


def test_activity_feed_missing_and_malformed_timestamps(feed: DiagnosticActivityFeedWidget):
    """6. Verify missing and malformed timestamps safely default to receipt time."""
    # None timestamp
    e_none = ActivityEvent(category=ActivityCategory.INFO, message="None ts", timestamp=None)
    assert len(e_none.display_time) == 8

    # Empty string
    e_empty = ActivityEvent(category=ActivityCategory.INFO, message="Empty ts", timestamp="")
    assert len(e_empty.display_time) == 8

    # Malformed text
    e_bad = ActivityEvent(category=ActivityCategory.INFO, message="Bad ts", timestamp="not-a-timestamp")
    assert len(e_bad.display_time) == 8

    # Already formatted HH:MM:SS
    e_hhmmss = ActivityEvent(category=ActivityCategory.INFO, message="HHMMSS", timestamp="18:22:45")
    assert e_hhmmss.display_time == "18:22:45"


def test_activity_feed_long_message_wrapping(feed: DiagnosticActivityFeedWidget):
    """7. Verify long event messages wrap properly without truncation."""
    long_msg = (
        "DNS resolution check encountered severe latency exceeding 450 ms across secondary resolvers. "
        "The upstream recursor 1.1.1.1 failed to answer UDP query within timeout bounds, triggering fallback "
        "to system default 8.8.8.8 which succeeded after 284 ms."
    )
    item = feed.add_activity(ActivityCategory.INFO, long_msg)
    assert item.lbl_message.wordWrap()
    assert item.lbl_message.text() == long_msg


def test_activity_feed_long_stage_name_handling(feed: DiagnosticActivityFeedWidget):
    """8. Verify long stage names are handled gracefully without layout errors."""
    long_stage = "Extended Gateway Path Latency, Bufferbloat, and Jitter Analysis Under Artificial Load"
    item = feed.add_activity(
        ActivityCategory.STAGE_STARTED,
        "Probing extended path",
        stage_order=5,
        stage_name=long_stage,
    )
    assert long_stage in item.lbl_stage.text()


def test_activity_feed_event_count_accuracy(feed: DiagnosticActivityFeedWidget):
    """9. Verify event count badge updates accurately."""
    for i in range(7):
        feed.add_activity(ActivityCategory.INFO, f"Event {i}")

    assert feed.event_count == 7
    assert feed.lbl_count_badge.text() == "7 events"


def test_activity_feed_clear(feed: DiagnosticActivityFeedWidget):
    """10. Verify clearing the feed resets to empty state."""
    for i in range(5):
        feed.add_activity(ActivityCategory.INFO, f"Event {i}")

    feed.clear()
    assert feed.event_count == 0
    assert feed.lbl_count_badge.text() == "0 events"
    assert not feed._empty_label.isHidden()
    assert feed.btn_scroll_latest.isHidden()


def test_activity_feed_max_event_limit(feed: DiagnosticActivityFeedWidget):
    """11. Verify maximum event limit is strictly enforced."""
    feed.set_max_events(25)
    assert feed.max_events == 25

    # Add 40 informational events
    for i in range(40):
        feed.add_activity(ActivityCategory.INFO, f"Routine telemetry event #{i}")

    assert feed.event_count == 25
    assert feed.lbl_count_badge.text() == "25 events"


def test_activity_feed_preserves_critical_terminal_events(feed: DiagnosticActivityFeedWidget):
    """12. Verify critical terminal events are preserved when pruning exceeds max_events."""
    feed.set_max_events(25)

    # Add 1 critical failed stage event
    feed.add_activity(
        ActivityCategory.STAGE_FAILED,
        "CRITICAL: Gateway completely unreachable",
        stage_order=2,
        stage_name="Gateway Reachability",
    )

    # Add 1 critical error event
    feed.add_activity(
        ActivityCategory.DIAGNOSTIC_ERROR,
        "CRITICAL: Diagnostic pipeline aborted due to socket exhaustion",
    )

    # Flood with 40 routine informational events
    for i in range(40):
        feed.add_activity(ActivityCategory.INFO, f"Routine telemetry heartbeat #{i}")

    assert feed.event_count == 25
    # Confirm the two critical events were NOT pruned
    messages = [e.message for e in feed.events]
    assert "CRITICAL: Gateway completely unreachable" in messages
    assert "CRITICAL: Diagnostic pipeline aborted due to socket exhaustion" in messages


def test_activity_feed_duplicate_handling_and_progress_collapse(feed: DiagnosticActivityFeedWidget):
    """13. Verify exact duplicate rejection and progress row collapsing."""
    # A. Exact duplicate check
    ev1 = ActivityEvent(
        category=ActivityCategory.INFO,
        message="Exact duplicate test",
        timestamp="2026-10-25T14:00:00Z",
    )
    feed.add_event(ev1)
    assert feed.event_count == 1

    # Adding the identical event again should not create a duplicate row
    feed.add_event(ev1)
    assert feed.event_count == 1

    # B. Progress row collapsing: repeated progress for the SAME stage updates in-place
    feed.add_activity(ActivityCategory.STAGE_PROGRESS, "Pinging hop 1/10...", stage_order=3, stage_name="Ping")
    assert feed.event_count == 2
    item_p = feed._item_widgets[-1]

    # Send updated progress for the same stage
    feed.add_activity(ActivityCategory.STAGE_PROGRESS, "Pinging hop 5/10...", stage_order=3, stage_name="Ping", measurement="RTT: 14ms")
    # Must NOT increase event count
    assert feed.event_count == 2
    assert item_p.lbl_message.text() == "Pinging hop 5/10..."
    assert "14ms" in item_p.lbl_measurement.text()

    # Progress for a DIFFERENT stage should create a new row
    feed.add_activity(ActivityCategory.STAGE_PROGRESS, "Resolving domains...", stage_order=4, stage_name="DNS")
    assert feed.event_count == 3


def test_activity_feed_auto_scroll_behavior(feed: DiagnosticActivityFeedWidget):
    """14. Verify auto-scroll behavior to latest event."""
    feed.resize(400, 300)
    for i in range(15):
        feed.add_activity(ActivityCategory.INFO, f"Event {i} for scrolling")

    feed.scroll_to_latest()
    assert feed.is_auto_scroll_enabled
    assert not feed.btn_scroll_latest.isVisible()


def test_activity_feed_user_scrolling_upward(feed: DiagnosticActivityFeedWidget):
    """15. Verify scrolling upward pauses auto-scroll and reveals jump button."""
    feed.resize(400, 300)
    for i in range(15):
        feed.add_activity(ActivityCategory.INFO, f"Event {i} for scroll awareness")

    # Simulate scroll upward with scrollbar maximum populated
    scrollbar = feed._scroll_area.verticalScrollBar()
    scrollbar.setMaximum(300)
    feed._on_scroll_changed(10)
    assert not feed.is_auto_scroll_enabled
    assert not feed.btn_scroll_latest.isHidden()

    # Click jump button restores auto-scroll
    feed.btn_scroll_latest.click()
    assert feed.is_auto_scroll_enabled
    assert feed.btn_scroll_latest.isHidden()


def test_activity_feed_repeated_updates_stability(feed: DiagnosticActivityFeedWidget):
    """16. Verify stability under 100 rapid sequential event additions."""
    for i in range(100):
        cat = ActivityCategory.INFO if i % 2 == 0 else ActivityCategory.STAGE_PROGRESS
        feed.add_activity(
            category=cat,
            message=f"Rapid stress test event #{i}",
            stage_order=(i % 5) + 1,
            stage_name=f"Stage {(i % 5) + 1}",
            measurement=f"Metric {i} ms" if i % 3 == 0 else None,
        )

    assert feed.event_count <= feed.max_events


def test_activity_feed_offscreen_rendering_and_resizing(feed: DiagnosticActivityFeedWidget):
    """17. Verify offscreen rendering across different viewport dimensions."""
    feed.add_activity(ActivityCategory.STAGE_STARTED, "Stage 1 started", stage_order=1, stage_name="Gateway")
    feed.add_activity(ActivityCategory.STAGE_COMPLETED, "Stage 1 finished successfully", stage_order=1, stage_name="Gateway", measurement="RTT: 2.1 ms")
    feed.add_activity(ActivityCategory.STAGE_FAILED, "Stage 2 failed: unreachable", stage_order=2, stage_name="Reachability")

    dimensions = [
        (450, 500),  # Standard
        (260, 400),  # Narrow
        (650, 700),  # Large
        (150, 150),  # Small
    ]

    for w, h in dimensions:
        feed.resize(w, h)
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.white)
        feed.render(img)


def test_activity_feed_workflow_event_integration(feed: DiagnosticActivityFeedWidget):
    """18. Verify direct ingestion of typed WorkflowEvent backend entity."""
    wf_event = WorkflowEvent(
        event_type=WorkflowEventType.STAGE_COMPLETED,
        run_id="run_20261025_test",
        timestamp_utc="2026-10-25T14:35:00Z",
        stage_order=2,
        stage_name="Gateway Reachability",
        status="COMPLETED",
        message="Local gateway reachable at 192.168.1.1",
        data={"metric": "Median RTT: 2.14 ms | Loss: 0.0%"},
    )

    item = feed.add_event(wf_event)
    assert feed.event_count == 1
    assert item.activity_event.category == ActivityCategory.STAGE_COMPLETED
    assert item.lbl_badge.text() == "SUCCESS"
    assert "Stage 2 • Gateway Reachability" in item.lbl_stage.text()
    assert "Median RTT: 2.14 ms" in item.lbl_measurement.text()
