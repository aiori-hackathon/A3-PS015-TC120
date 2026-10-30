"""Loaded latency / bufferbloat measurement adapter package (TC-ADPT-05)."""

from ticnet.measurements.load.adapter import (
    DEFAULT_BASELINE_PROBE_COUNT,
    DEFAULT_LOADED_PROBE_COUNT,
    DEFAULT_SAMPLE_INTERVAL_MS,
    LoadedLatencyAdapter,
)
from ticnet.measurements.load.models import LoadedLatencyMeasurementResult

__all__ = [
    "DEFAULT_BASELINE_PROBE_COUNT",
    "DEFAULT_LOADED_PROBE_COUNT",
    "DEFAULT_SAMPLE_INTERVAL_MS",
    "LoadedLatencyAdapter",
    "LoadedLatencyMeasurementResult",
]
