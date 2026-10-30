"""Loaded latency and bufferbloat evaluation adapter (TC-ADPT-05).

Measures round-trip latency under controlled, bounded traffic load and compares
it against quiescent baseline latency to quantify observed latency degradation.

Safety Guarantees:
- Reuses throughput adapter's 10s and 25MB hard ceilings.
- Requires valid baseline samples before dispatching any load.
- Strict concurrency coordination: Latency probes overlap active load streaming.
- Deterministic cleanup: Halts load thread promptly; zero orphaned threads.
- Non-zero nulls: Never fabricates delta or ratio when baseline is missing or 0.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ticnet.measurements.base import MeasurementAdapter
from ticnet.measurements.load.models import LoadedLatencyMeasurementResult
from ticnet.measurements.ping.adapter import PingMeasurementAdapter
from ticnet.measurements.ping.models import PingMeasurementResult
from ticnet.measurements.statistics import calculate_median_rtt, calculate_p95_rtt
from ticnet.measurements.throughput.adapter import (
    HARD_MAX_DURATION_SECONDS,
    HARD_MAX_TRANSFER_BYTES,
    ThroughputMeasurementAdapter,
)
from ticnet.measurements.throughput.models import ThroughputMeasurementResult
from ticnet.models.enums import Status
from ticnet.utils.logging import get_logger

logger = get_logger("measurements.load.adapter")

DEFAULT_BASELINE_PROBE_COUNT: int = 5
DEFAULT_LOADED_PROBE_COUNT: int = 10
DEFAULT_SAMPLE_INTERVAL_MS: int = 100
MIN_REQUIRED_SAMPLES: int = 3


class LoadedLatencyAdapter(MeasurementAdapter):
    """Measures latency under active throughput load to evaluate bufferbloat."""

    def __init__(
        self,
        ping_adapter: Optional[PingMeasurementAdapter] = None,
        throughput_adapter: Optional[ThroughputMeasurementAdapter] = None,
        baseline_probe_count: int = DEFAULT_BASELINE_PROBE_COUNT,
        loaded_probe_count: int = DEFAULT_LOADED_PROBE_COUNT,
        sample_interval_ms: int = DEFAULT_SAMPLE_INTERVAL_MS,
        max_duration_s: float = 10.0,
        max_transfer_bytes: int = 26214400,
        clock_func: Optional[Callable[[], float]] = None,
    ) -> None:
        """Initialize loaded latency adapter with safety limits."""
        if baseline_probe_count < 1:
            raise ValueError("baseline_probe_count must be >= 1")
        if loaded_probe_count < 1:
            raise ValueError("loaded_probe_count must be >= 1")
        if sample_interval_ms < 10:
            raise ValueError("sample_interval_ms must be >= 10")

        # Clamp max duration and transfer bytes to hard ceilings
        duration_s = min(float(max_duration_s), HARD_MAX_DURATION_SECONDS)
        transfer_bytes = min(int(max_transfer_bytes), HARD_MAX_TRANSFER_BYTES)

        super().__init__(timeout_s=duration_s + 10.0)

        self.baseline_probe_count = int(baseline_probe_count)
        self.loaded_probe_count = int(loaded_probe_count)
        self.sample_interval_ms = int(sample_interval_ms)
        self.max_duration_s = duration_s
        self.max_transfer_bytes = transfer_bytes
        self._clock = clock_func or time.perf_counter

        self.ping_adapter = ping_adapter or PingMeasurementAdapter()
        self.throughput_adapter = throughput_adapter or ThroughputMeasurementAdapter(
            max_duration_s=self.max_duration_s,
            max_transfer_bytes=self.max_transfer_bytes,
        )

    @property
    def adapter_name(self) -> str:
        return "LoadedLatencyAdapter"

    @property
    def supported_platforms(self) -> Tuple[str, ...]:
        return ("win32", "linux", "darwin")

    def execute(
        self,
        target_ip: str,
        traffic_url: str,
        cancel_event: Optional[threading.Event] = None,
    ) -> LoadedLatencyMeasurementResult:
        """Execute complete loaded latency comparison workflow.
        
        Sequence:
        1. Collect idle baseline latency samples against target_ip.
        2. Validate baseline sufficiency (halt if insufficient/failed).
        3. Launch background throughput download against traffic_url.
        4. Collect loaded latency samples against target_ip while load is active.
        5. Stop throughput load promptly and wait for thread termination.
        6. Compute medians, p95s, delta, ratio, and percentage increase.
        """
        # A. Pre-flight cancellation check
        if cancel_event is not None and cancel_event.is_set():
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.CANCELLED,
                error_message="Measurement cancelled before baseline collection",
            )

        # --------------------------------------------------------------------
        # 1. Idle Baseline Latency Phase
        # --------------------------------------------------------------------
        logger.info("Collecting %d baseline latency samples for %s", self.baseline_probe_count, target_ip)
        try:
            baseline_ping_res: PingMeasurementResult = self.ping_adapter.execute(
                target_ip=target_ip,
                probe_count=self.baseline_probe_count,
                interval_ms=self.sample_interval_ms,
            )
        except Exception as exc:
            logger.error("Baseline ping failed with exception: %s", exc)
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.FAILED,
                error_message=f"Baseline ping failed: {exc}",
            )

        baseline_rtts = list(baseline_ping_res.individual_rtts)
        baseline_sample_count = baseline_ping_res.probe_count_attempted
        baseline_valid_count = len(baseline_rtts)

        # 2. Baseline Validity Check
        if baseline_ping_res.status != Status.SUCCESS or baseline_valid_count < MIN_REQUIRED_SAMPLES:
            error_msg = (
                f"Insufficient valid baseline samples: got {baseline_valid_count}, "
                f"required minimum {MIN_REQUIRED_SAMPLES} (status: {baseline_ping_res.status.value})"
            )
            logger.warning(error_msg)
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.FAILED if baseline_ping_res.status == Status.FAILED else Status.INCOMPLETE,
                baseline_sample_count=baseline_sample_count,
                baseline_valid_count=baseline_valid_count,
                baseline_rtts=baseline_rtts,
                error_message=error_msg,
            )

        baseline_median = calculate_median_rtt(baseline_rtts, min_samples=MIN_REQUIRED_SAMPLES)
        baseline_p95 = calculate_p95_rtt(baseline_rtts, min_samples=10)

        # Check cancellation before launching traffic load
        if cancel_event is not None and cancel_event.is_set():
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.CANCELLED,
                baseline_sample_count=baseline_sample_count,
                baseline_valid_count=baseline_valid_count,
                baseline_rtts=baseline_rtts,
                baseline_median_rtt_ms=baseline_median,
                baseline_p95_rtt_ms=baseline_p95,
                error_message="Measurement cancelled after baseline collection",
            )

        # --------------------------------------------------------------------
        # 3. Concurrent Loaded Latency Phase (Active Overlap)
        # --------------------------------------------------------------------
        load_streaming_started = threading.Event()
        load_stop_event = threading.Event()
        load_result_container: List[ThroughputMeasurementResult] = []
        load_exception_container: List[Exception] = []

        def _run_throughput_load():
            try:
                def _on_chunk(chunk_len: int, total_bytes: int):
                    # Flag that HTTP streaming has actively begun
                    load_streaming_started.set()

                tp_res = self.throughput_adapter.execute(
                    endpoint_url=traffic_url,
                    cancel_event=load_stop_event,
                    on_chunk_received=_on_chunk,
                )
                load_result_container.append(tp_res)
            except Exception as e:
                load_exception_container.append(e)

        load_thread = threading.Thread(
            target=_run_throughput_load,
            name="ticnet-loaded-latency-load",
            daemon=True,
        )

        loaded_ping_res: Optional[PingMeasurementResult] = None
        load_active_during_probes = False

        load_failed_reason: Optional[str] = None
        loaded_ping_error: Optional[str] = None

        try:
            logger.info("Starting background throughput load against %s", traffic_url)
            load_thread.start()

            # Wait up to 5.0s for load stream to connect and receive initial data
            stream_started = load_streaming_started.wait(timeout=5.0)

            if not stream_started or (load_thread.is_alive() is False and not load_result_container):
                # Load failed before streaming started
                tp_err = "Throughput load failed to establish active streaming"
                if load_result_container and load_result_container[0].error_message:
                    tp_err = f"Throughput load error: {load_result_container[0].error_message}"
                elif load_exception_container:
                    tp_err = f"Throughput load exception: {load_exception_container[0]}"

                logger.warning(tp_err)
                load_failed_reason = tp_err
            else:
                # Load is actively running!
                load_active_during_probes = True
                logger.info("Load active. Dispatching %d loaded latency probes to %s", self.loaded_probe_count, target_ip)

                try:
                    loaded_ping_res = self.ping_adapter.execute(
                        target_ip=target_ip,
                        probe_count=self.loaded_probe_count,
                        interval_ms=self.sample_interval_ms,
                    )
                except Exception as ping_exc:
                    logger.error("Loaded ping failed with exception: %s", ping_exc)
                    loaded_ping_error = f"Loaded ping error: {ping_exc}"

        finally:
            # 4. Deterministic Cleanup: Signal load thread to stop and join
            load_stop_event.set()
            if hasattr(self.throughput_adapter, "abort"):
                self.throughput_adapter.abort()
            load_thread.join(timeout=3.0)
            thread_orphaned = load_thread.is_alive()
            if thread_orphaned:
                logger.error("CRITICAL: load_thread remained alive after 3.0s join timeout")

        # Explicitly fail if background thread was orphaned (timed join is not proof of termination)
        if thread_orphaned:
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.FAILED,
                baseline_sample_count=baseline_sample_count,
                baseline_valid_count=baseline_valid_count,
                baseline_rtts=baseline_rtts,
                baseline_median_rtt_ms=baseline_median,
                baseline_p95_rtt_ms=baseline_p95,
                load_active_during_probes=False,
                error_message="Background throughput thread failed to terminate cleanly within timeout",
            )

        if load_failed_reason is not None:
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.FAILED,
                baseline_sample_count=baseline_sample_count,
                baseline_valid_count=baseline_valid_count,
                baseline_rtts=baseline_rtts,
                baseline_median_rtt_ms=baseline_median,
                baseline_p95_rtt_ms=baseline_p95,
                load_active_during_probes=False,
                error_message=load_failed_reason,
            )

        if loaded_ping_error is not None:
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.FAILED,
                baseline_sample_count=baseline_sample_count,
                baseline_valid_count=baseline_valid_count,
                baseline_rtts=baseline_rtts,
                baseline_median_rtt_ms=baseline_median,
                baseline_p95_rtt_ms=baseline_p95,
                load_active_during_probes=load_active_during_probes,
                error_message=loaded_ping_error,
            )

        # --------------------------------------------------------------------
        # 5. Extract Loaded Results & Calculate Statistics
        # --------------------------------------------------------------------
        loaded_rtts = list(loaded_ping_res.individual_rtts) if loaded_ping_res else []
        loaded_sample_count = loaded_ping_res.probe_count_attempted if loaded_ping_res else 0
        loaded_valid_count = len(loaded_rtts)

        tp_res = load_result_container[0] if load_result_container else None
        load_bytes = tp_res.bytes_transferred if tp_res else 0
        load_throughput = tp_res.throughput_mbps if tp_res else None

        # Verify that throughput load actually ran successfully during probing
        if tp_res is None or tp_res.status == Status.FAILED or load_bytes == 0:
            err = (
                f"Throughput load failed or produced zero payload bytes (status: {tp_res.status.value if tp_res else 'NONE'}, "
                f"bytes: {load_bytes}); cannot validate loaded latency without active traffic"
            )
            logger.warning(err)
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.FAILED,
                baseline_sample_count=baseline_sample_count,
                baseline_valid_count=baseline_valid_count,
                baseline_rtts=baseline_rtts,
                baseline_median_rtt_ms=baseline_median,
                baseline_p95_rtt_ms=baseline_p95,
                loaded_sample_count=loaded_sample_count,
                loaded_valid_count=loaded_valid_count,
                loaded_rtts=loaded_rtts,
                load_active_during_probes=False,
                load_bytes_transferred=load_bytes,
                load_throughput_mbps=load_throughput,
                error_message=err,
            )

        # Check for loaded sample validity
        if not loaded_ping_res or loaded_valid_count < MIN_REQUIRED_SAMPLES:
            error_msg = (
                f"Insufficient valid loaded samples: got {loaded_valid_count}, "
                f"required minimum {MIN_REQUIRED_SAMPLES}"
            )
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.INCOMPLETE,
                baseline_sample_count=baseline_sample_count,
                baseline_valid_count=baseline_valid_count,
                baseline_rtts=baseline_rtts,
                baseline_median_rtt_ms=baseline_median,
                baseline_p95_rtt_ms=baseline_p95,
                loaded_sample_count=loaded_sample_count,
                loaded_valid_count=loaded_valid_count,
                loaded_rtts=loaded_rtts,
                load_active_during_probes=load_active_during_probes,
                load_bytes_transferred=load_bytes,
                load_throughput_mbps=load_throughput,
                error_message=error_msg,
            )

        loaded_median = calculate_median_rtt(loaded_rtts, min_samples=MIN_REQUIRED_SAMPLES)
        loaded_p95 = calculate_p95_rtt(loaded_rtts, min_samples=10)

        # 6. Delta and Bufferbloat Ratio Calculation
        delta_rtt_median_ms: Optional[float] = None
        bufferbloat_ratio: Optional[float] = None
        delta_pct: Optional[float] = None

        if loaded_median is not None and baseline_median is not None:
            delta_rtt_median_ms = round(loaded_median - baseline_median, 3)

            # Only calculate percentage and ratio when baseline median is strictly positive
            if baseline_median > 0:
                delta_pct = round(((loaded_median - baseline_median) / baseline_median) * 100.0, 2)
                if loaded_p95 is not None:
                    bufferbloat_ratio = round(loaded_p95 / baseline_median, 2)
                elif loaded_median is not None:
                    bufferbloat_ratio = round(loaded_median / baseline_median, 2)

        return LoadedLatencyMeasurementResult(
            target_ip=target_ip,
            traffic_url=traffic_url,
            status=Status.SUCCESS,
            baseline_sample_count=baseline_sample_count,
            baseline_valid_count=baseline_valid_count,
            baseline_rtts=baseline_rtts,
            baseline_median_rtt_ms=baseline_median,
            baseline_p95_rtt_ms=baseline_p95,
            loaded_sample_count=loaded_sample_count,
            loaded_valid_count=loaded_valid_count,
            loaded_rtts=loaded_rtts,
            loaded_median_rtt_ms=loaded_median,
            loaded_p95_rtt_ms=loaded_p95,
            delta_rtt_median_ms=delta_rtt_median_ms,
            bufferbloat_ratio=bufferbloat_ratio,
            delta_pct=delta_pct,
            load_active_during_probes=load_active_during_probes,
            load_bytes_transferred=load_bytes,
            load_throughput_mbps=load_throughput,
            error_message=None,
        )
