"""Unit tests for Loaded Latency / Bufferbloat Measurement Adapter (TC-ADPT-05).

Verifies idle baseline collection, concurrent active-load streaming overlap,
bufferbloat delta and ratio calculations, thread cleanup, error propagation,
zero denominator safety, and non-zero null invariants.
"""

from __future__ import annotations

import json
import threading
import time
from typing import List, Optional
import pytest

from ticnet.measurements.load.adapter import (
    DEFAULT_BASELINE_PROBE_COUNT,
    DEFAULT_LOADED_PROBE_COUNT,
    LoadedLatencyAdapter,
)
from ticnet.measurements.load.models import LoadedLatencyMeasurementResult
from ticnet.measurements.ping.models import PingMeasurementResult, PingProbeObservation
from ticnet.measurements.throughput.models import ThroughputMeasurementResult
from ticnet.models.entities import TestRun
from ticnet.models.enums import MeasurementStatus, RunStatus, Status, TargetType
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository


# ============================================================================
# Test Doubles / Deterministic Mocks
# ============================================================================

class MockPingAdapter:
    """Configurable mock ping adapter."""

    def __init__(
        self,
        rtts_sequence: Optional[List[List[float]]] = None,
        status_sequence: Optional[List[Status]] = None,
        side_effect: Optional[Exception] = None,
        on_probe_callback: Optional[callable] = None,
    ) -> None:
        self.rtts_sequence = list(rtts_sequence or [])
        self.status_sequence = list(status_sequence or [])
        self.side_effect = side_effect
        self.on_probe_callback = on_probe_callback
        self.execute_calls: List[dict] = []

    def execute(
        self,
        target_ip: str,
        probe_count: int = 5,
        interval_ms: int = 100,
        **kwargs,
    ) -> PingMeasurementResult:
        self.execute_calls.append({
            "target_ip": target_ip,
            "probe_count": probe_count,
            "interval_ms": interval_ms,
        })
        if self.side_effect is not None:
            raise self.side_effect

        if self.on_probe_callback is not None:
            self.on_probe_callback()

        rtts = self.rtts_sequence.pop(0) if self.rtts_sequence else [10.0] * probe_count
        status = self.status_sequence.pop(0) if self.status_sequence else Status.SUCCESS

        probes = [
            PingProbeObservation(
                sample_index=idx + 1,
                timestamp_utc="2026-10-22T20:00:00Z",
                status=MeasurementStatus.SUCCESS if r is not None else MeasurementStatus.TIMEOUT,
                rtt_ms=r,
            )
            for idx, r in enumerate(rtts)
        ]
        valid_rtts = [r for r in rtts if r is not None]

        return PingMeasurementResult(
            target_ip=target_ip,
            ip_family="IPv4",
            probe_count_configured=probe_count,
            probe_count_attempted=len(rtts),
            probe_count_successful=len(valid_rtts),
            probe_count_failed=len(rtts) - len(valid_rtts),
            packet_loss_pct=round((len(rtts) - len(valid_rtts)) / float(len(rtts)) * 100.0, 2) if rtts else 0.0,
            individual_rtts=valid_rtts,
            status=status,
            probes=probes,
        )


class MockThroughputAdapter:
    """Configurable mock throughput adapter simulating streaming load."""

    def __init__(
        self,
        bytes_transferred: int = 5_000_000,
        throughput_mbps: float = 25.0,
        status: Status = Status.SUCCESS,
        streaming_delay_s: float = 0.1,
        fail_connect: bool = False,
    ) -> None:
        self.bytes_transferred = bytes_transferred
        self.throughput_mbps = throughput_mbps
        self.status = status
        self.streaming_delay_s = streaming_delay_s
        self.fail_connect = fail_connect
        self.execute_calls: List[dict] = []
        self.active_during_execution = False

    def execute(
        self,
        endpoint_url: str,
        cancel_event: Optional[threading.Event] = None,
        on_chunk_received: Optional[callable] = None,
        **kwargs,
    ) -> ThroughputMeasurementResult:
        self.execute_calls.append({"endpoint_url": endpoint_url})

        if self.fail_connect:
            return ThroughputMeasurementResult(
                target_url=endpoint_url,
                status=Status.FAILED,
                error_message="Mock connection failure to throughput endpoint",
            )

        self.active_during_execution = True
        try:
            # Signal initial streaming started
            if on_chunk_received is not None:
                on_chunk_received(65536, 65536)

            # Wait until cancelled or duration elapsed
            start = time.time()
            while time.time() - start < self.streaming_delay_s:
                if cancel_event is not None and cancel_event.is_set():
                    break
                time.sleep(0.01)

            if on_chunk_received is not None:
                on_chunk_received(65536, self.bytes_transferred)

            return ThroughputMeasurementResult(
                target_url=endpoint_url,
                status=self.status,
                bytes_transferred=self.bytes_transferred,
                elapsed_s=0.5,
                throughput_bps=self.throughput_mbps * 1_000_000.0,
                throughput_mbps=self.throughput_mbps,
            )
        finally:
            self.active_during_execution = False


# ============================================================================
# Section 1: Baseline and Loaded Latency Calculation
# ============================================================================

class TestLoadedLatencyCalculations:
    """Verifies baseline, loaded, delta, ratio, and percentage calculations."""

    def test_valid_baseline_and_loaded_samples_success(self):
        # Baseline RTTs: [10.0, 12.0, 10.0, 11.0, 12.0] -> Median = 11.0 ms
        # Loaded RTTs: [40.0, 45.0, 50.0, 60.0, 55.0, 48.0, 52.0, 58.0, 62.0, 70.0] -> Median = 53.5 ms
        baseline_rtts = [10.0, 12.0, 10.0, 11.0, 12.0]
        loaded_rtts = [40.0, 45.0, 50.0, 60.0, 55.0, 48.0, 52.0, 58.0, 62.0, 70.0]

        mock_ping = MockPingAdapter(rtts_sequence=[baseline_rtts, loaded_rtts])
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(
            ping_adapter=mock_ping,
            throughput_adapter=mock_tp,
        )

        res = adapter.execute(target_ip="192.168.1.1", traffic_url="https://speed.example.com/test")
        assert res.status == Status.SUCCESS
        assert res.baseline_valid_count == 5
        assert res.loaded_valid_count == 10
        assert res.baseline_median_rtt_ms == 11.0
        assert res.loaded_median_rtt_ms == 53.5
        # Delta: 53.5 - 11.0 = 42.5 ms
        assert res.delta_rtt_median_ms == 42.5
        # Delta pct: (42.5 / 11.0) * 100 = 386.36%
        assert res.delta_pct == 386.36
        # Bufferbloat ratio: p95(loaded) / median(baseline)
        # p95 of 10 sorted loaded items: rank = 10 -> 70.0; ratio = 70.0 / 11.0 = 6.36
        assert res.bufferbloat_ratio == 6.36
        assert res.load_active_during_probes is True
        assert res.load_bytes_transferred == 5_000_000
        assert res.load_throughput_mbps == 25.0

    def test_zero_baseline_latency_prevents_division_by_zero(self):
        # Baseline: all 0.0 ms
        baseline_rtts = [0.0, 0.0, 0.0, 0.0, 0.0]
        loaded_rtts = [15.0, 15.0, 15.0, 15.0, 15.0]

        mock_ping = MockPingAdapter(rtts_sequence=[baseline_rtts, loaded_rtts])
        mock_tp = MockThroughputAdapter()
        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)

        res = adapter.execute(target_ip="127.0.0.1", traffic_url="https://speed.example.com/test")
        assert res.status == Status.SUCCESS
        assert res.baseline_median_rtt_ms == 0.0
        assert res.loaded_median_rtt_ms == 15.0
        assert res.delta_rtt_median_ms == 15.0
        # Denominator is 0.0; ratio and percentage must be None (no division by zero!)
        assert res.delta_pct is None
        assert res.bufferbloat_ratio is None


# ============================================================================
# Section 2: Baseline Validation and Early Termination
# ============================================================================

class TestLoadedLatencyBaselineGuards:
    """Verifies that failed baseline prevents unnecessary traffic load."""

    def test_insufficient_baseline_samples_halts_without_load(self):
        # Only 2 valid samples (minimum is 3)
        baseline_rtts = [10.0, 12.0, None, None, None]
        mock_ping = MockPingAdapter(rtts_sequence=[baseline_rtts])
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute(target_ip="192.168.1.1", traffic_url="https://speed.example.com/test")

        # Must NOT have started the throughput load!
        assert len(mock_tp.execute_calls) == 0
        assert res.status in (Status.FAILED, Status.INCOMPLETE)
        assert res.delta_rtt_median_ms is None
        assert "Insufficient valid baseline samples" in (res.error_message or "")

    def test_baseline_ping_failure_halts_without_load(self):
        mock_ping = MockPingAdapter(
            rtts_sequence=[[]],
            status_sequence=[Status.FAILED],
        )
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute(target_ip="192.168.1.1", traffic_url="https://speed.example.com/test")

        assert len(mock_tp.execute_calls) == 0
        assert res.status == Status.FAILED
        assert res.delta_rtt_median_ms is None

    def test_baseline_ping_exception_handled_safely(self):
        mock_ping = MockPingAdapter(side_effect=OSError("Host network down"))
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute(target_ip="192.168.1.1", traffic_url="https://speed.example.com/test")

        assert len(mock_tp.execute_calls) == 0
        assert res.status == Status.FAILED
        assert "Baseline ping failed: Host network down" in (res.error_message or "")


# ============================================================================
# Section 3: Concurrent Active-Load Overlap and Coordination
# ============================================================================

class TestActiveLoadOverlapAndConcurrency:
    """Verifies that probes overlap real load and threads terminate cleanly."""

    def test_probes_execute_while_throughput_load_is_active(self):
        overlap_observed = False
        mock_tp = MockThroughputAdapter(streaming_delay_s=0.2)

        def verify_load_is_active():
            nonlocal overlap_observed
            if mock_tp.active_during_execution:
                overlap_observed = True

        mock_ping = MockPingAdapter(
            rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0], [30.0, 30.0, 30.0, 30.0, 30.0]],
            on_probe_callback=verify_load_is_active,
        )

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute(target_ip="192.168.1.1", traffic_url="https://speed.example.com/test")

        assert res.status == Status.SUCCESS
        assert res.load_active_during_probes is True
        assert overlap_observed is True
        # Verify throughput adapter was cleanly terminated
        assert mock_tp.active_during_execution is False

    def test_throughput_load_failure_reported_cleanly(self):
        mock_ping = MockPingAdapter(rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0]])
        mock_tp = MockThroughputAdapter(fail_connect=True)

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute(target_ip="192.168.1.1", traffic_url="https://speed.example.com/test")

        assert res.status == Status.FAILED
        assert res.load_active_during_probes is False
        assert "Throughput load error" in (res.error_message or "")
        assert res.delta_rtt_median_ms is None

    def test_insufficient_loaded_samples_yields_incomplete_status(self):
        # Baseline ok (5 samples), but loaded only has 2 valid samples
        baseline_rtts = [10.0, 10.0, 10.0, 10.0, 10.0]
        loaded_rtts = [40.0, 42.0, None, None, None]

        mock_ping = MockPingAdapter(rtts_sequence=[baseline_rtts, loaded_rtts])
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute(target_ip="192.168.1.1", traffic_url="https://speed.example.com/test")

        assert res.status == Status.INCOMPLETE
        assert res.loaded_valid_count == 2
        assert res.delta_rtt_median_ms is None
        assert "Insufficient valid loaded samples" in (res.error_message or "")

    def test_cancellation_before_baseline(self):
        cancel_ev = threading.Event()
        cancel_ev.set()

        mock_ping = MockPingAdapter()
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test", cancel_event=cancel_ev)

        assert res.status == Status.CANCELLED
        assert len(mock_ping.execute_calls) == 0
        assert len(mock_tp.execute_calls) == 0

    def test_no_orphaned_threads_after_completion(self):
        mock_ping = MockPingAdapter(
            rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0], [20.0, 20.0, 20.0, 20.0, 20.0]]
        )
        mock_tp = MockThroughputAdapter(streaming_delay_s=0.05)

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        adapter.execute("192.168.1.1", "https://speed.example.com/test")

        # Check all active threads
        active_names = [t.name for t in threading.enumerate()]
        assert "ticnet-loaded-latency-load" not in active_names

    def test_cancellation_after_baseline_before_load(self):
        cancel_ev = threading.Event()
        def cancel_after_baseline():
            cancel_ev.set()

        mock_ping = MockPingAdapter(
            rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0]],
            on_probe_callback=cancel_after_baseline,
        )
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test", cancel_event=cancel_ev)

        assert res.status == Status.CANCELLED
        assert res.baseline_valid_count == 5
        assert len(mock_tp.execute_calls) == 0
        assert "cancelled after baseline" in (res.error_message or "")

    def test_packet_loss_during_loaded_probes_uses_valid_samples(self):
        # 5 baseline samples, 10 loaded attempts with 2 timeouts (8 valid)
        baseline_rtts = [10.0, 10.0, 10.0, 10.0, 10.0]
        loaded_rtts = [40.0, None, 45.0, 50.0, None, 55.0, 48.0, 52.0, 58.0, 60.0]

        mock_ping = MockPingAdapter(rtts_sequence=[baseline_rtts, loaded_rtts])
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test")

        assert res.status == Status.SUCCESS
        assert res.loaded_sample_count == 10
        assert res.loaded_valid_count == 8
        assert res.delta_rtt_median_ms is not None
        assert res.bufferbloat_ratio is not None

    def test_no_fabricated_root_cause_in_result(self):
        # Verifies adapter is strictly empirical and does not assert root-cause claims
        mock_ping = MockPingAdapter(
            rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0], [200.0, 250.0, 300.0, 350.0, 400.0]]
        )
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test")

        assert res.status == Status.SUCCESS
        assert res.delta_rtt_median_ms == 290.0
        # Result contains purely factual observations, no speculative assertions
        assert not hasattr(res, "root_cause")
        assert not hasattr(res, "fault_blame")



# ============================================================================
# Section 4: Persistence Mapping & SQLite Invariants
# ============================================================================

class TestLoadedLatencyPersistenceMapping:
    """Verifies domain entity conversion and SQLite persistence."""

    def test_observation_mapping(self):
        res = LoadedLatencyMeasurementResult(
            target_ip="192.168.1.1",
            traffic_url="https://speed.example.com/down",
            status=Status.SUCCESS,
            baseline_sample_count=5,
            baseline_valid_count=5,
            baseline_median_rtt_ms=10.0,
            baseline_p95_rtt_ms=12.0,
            baseline_rtts=[10.0, 10.0, 10.0, 11.0, 12.0],
            loaded_sample_count=5,
            loaded_valid_count=5,
            loaded_median_rtt_ms=45.0,
            loaded_p95_rtt_ms=50.0,
            loaded_rtts=[40.0, 42.0, 45.0, 48.0, 50.0],
            delta_rtt_median_ms=35.0,
            bufferbloat_ratio=4.5,
            delta_pct=350.0,
            load_active_during_probes=True,
            load_bytes_transferred=2_000_000,
            load_throughput_mbps=20.0,
        )

        obs_list = res.to_observations(run_id="run-load-test")
        assert len(obs_list) == 6  # 1 summary delta + 5 loaded probe observations

        delta_obs = obs_list[0]
        assert delta_obs.metric_name == "loaded_latency_delta"
        assert delta_obs.unit == "ms"
        assert delta_obs.value_numeric == 35.0
        assert delta_obs.status == MeasurementStatus.SUCCESS

        meta = json.loads(delta_obs.method_metadata)
        assert meta["bufferbloat_ratio"] == 4.5
        assert meta["delta_pct"] == 350.0
        assert meta["load_active_during_probes"] is True

        probe_obs = obs_list[1]
        assert probe_obs.metric_name == "loaded_rtt"
        assert probe_obs.value_numeric == 40.0

    def test_persistence_in_sqlite(self, tmp_path):
        db_file = tmp_path / "test_load.db"
        db = DatabaseManager(str(db_file))
        db.run_migrations()
        run_repo = RunRepository(db)
        obs_repo = ObservationRepository(db)

        run = TestRun(
            run_id="run-load-db",
            start_time_utc="2026-10-22T20:00:00Z",
            status=RunStatus.RUNNING,
            app_version="1.0.0",
            config_version="1.0.0",
        )
        run_repo.create_run(run)

        res = LoadedLatencyMeasurementResult(
            target_ip="192.168.1.1",
            traffic_url="https://speed.example.com/down",
            status=Status.SUCCESS,
            baseline_sample_count=5,
            baseline_valid_count=5,
            baseline_median_rtt_ms=10.0,
            loaded_sample_count=5,
            loaded_valid_count=5,
            loaded_median_rtt_ms=30.0,
            loaded_rtts=[25.0, 28.0, 30.0, 32.0, 35.0],
            delta_rtt_median_ms=20.0,
            bufferbloat_ratio=3.0,
            delta_pct=200.0,
            load_active_during_probes=True,
        )

        obs_list = res.to_observations(run_id=run.run_id)
        obs_repo.create_observations_batch(obs_list)

        loaded = obs_repo.get_observations_for_run(run.run_id)
        assert len(loaded) == 6
        assert loaded[0].metric_name == "loaded_latency_delta"
        assert loaded[0].value_numeric == 20.0

    def test_orphaned_load_thread_detected_and_fails_result(self, monkeypatch):
        # Verify that if join times out and thread remains alive, adapter fails explicitly
        mock_ping = MockPingAdapter(
            rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0], [20.0, 20.0, 20.0, 20.0, 20.0]]
        )
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)

        # Mock Thread.is_alive after join to simulate an unkillable/orphaned background thread
        orig_thread = threading.Thread
        class StubStuckThread(orig_thread):
            def is_alive(self):
                # Pretend thread is still running after join
                return True

        monkeypatch.setattr(threading, "Thread", StubStuckThread)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test")

        assert res.status == Status.FAILED
        assert res.load_active_during_probes is False
        assert "failed to terminate cleanly" in (res.error_message or "")
        assert res.delta_rtt_median_ms is None

    def test_throughput_zero_bytes_fails_loaded_latency(self):
        # If throughput returns 0 bytes transferred, loaded latency cannot claim valid load
        mock_ping = MockPingAdapter(
            rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0], [20.0, 20.0, 20.0, 20.0, 20.0]]
        )
        mock_tp = MockThroughputAdapter(bytes_transferred=0, throughput_mbps=0.0)

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test")

        assert res.status == Status.FAILED
        assert res.load_active_during_probes is False
        assert "zero payload bytes" in (res.error_message or "")
        assert res.delta_rtt_median_ms is None

    def test_probe_exception_during_loaded_phase_cleans_up_load_thread(self):
        # Ping adapter succeeds on baseline, but raises exception during loaded probing
        mock_ping = MockPingAdapter(rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0]])
        def explode_on_second_call():
            if len(mock_ping.execute_calls) > 1:
                raise RuntimeError("Ping probe subagent crashed")

        mock_ping.on_probe_callback = explode_on_second_call
        mock_tp = MockThroughputAdapter()

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test")

        assert res.status == Status.FAILED
        assert "Loaded ping error: Ping probe subagent crashed" in (res.error_message or "")
        # Background throughput load thread must have been stopped and joined
        assert "ticnet-loaded-latency-load" not in [t.name for t in threading.enumerate()]

    def test_early_throughput_termination_before_probes_complete(self):
        # Throughput finishes very quickly (e.g. 50ms) while loaded ping is still running
        mock_ping = MockPingAdapter(
            rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0], [25.0, 30.0, 35.0, 40.0, 45.0]]
        )
        mock_tp = MockThroughputAdapter(streaming_delay_s=0.01, bytes_transferred=1_000_000)

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test")

        assert res.status == Status.SUCCESS
        assert res.load_bytes_transferred == 1_000_000
        assert res.delta_rtt_median_ms is not None

    def test_orphaned_thread_detected_even_if_load_startup_failed(self, monkeypatch):
        # Simulate thread failing to start stream and remaining stuck alive
        mock_ping = MockPingAdapter(rtts_sequence=[[10.0, 10.0, 10.0, 10.0, 10.0]])
        # fail_connect=True means streaming never starts
        mock_tp = MockThroughputAdapter(fail_connect=True)

        adapter = LoadedLatencyAdapter(ping_adapter=mock_ping, throughput_adapter=mock_tp)

        orig_thread = threading.Thread
        class StubStuckThread(orig_thread):
            def is_alive(self):
                return True

        monkeypatch.setattr(threading, "Thread", StubStuckThread)
        res = adapter.execute("192.168.1.1", "https://speed.example.com/test")

        assert res.status == Status.FAILED
        assert "failed to terminate cleanly" in (res.error_message or "")


