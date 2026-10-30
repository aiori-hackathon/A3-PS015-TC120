"""Ping reachability measurement and observation package for TicNet."""

from ticnet.measurements.ping.adapter import (
    DEFAULT_PROBE_COUNT,
    DEFAULT_PROBE_TIMEOUT_MS,
    MAX_PROBE_COUNT,
    MAX_PROBE_TIMEOUT_MS,
    MIN_PROBE_COUNT,
    MIN_PROBE_TIMEOUT_MS,
    PingMeasurementAdapter,
    probe_gateway_reachability,
)
from ticnet.measurements.ping.linux import LinuxPingParser
from ticnet.measurements.ping.models import (
    PingMeasurementResult,
    PingProbeObservation,
)
from ticnet.measurements.ping.windows import WindowsPingParser

__all__ = [
    "PingMeasurementAdapter",
    "PingMeasurementResult",
    "PingProbeObservation",
    "probe_gateway_reachability",
    "WindowsPingParser",
    "LinuxPingParser",
    "MIN_PROBE_COUNT",
    "MAX_PROBE_COUNT",
    "DEFAULT_PROBE_COUNT",
    "MIN_PROBE_TIMEOUT_MS",
    "MAX_PROBE_TIMEOUT_MS",
    "DEFAULT_PROBE_TIMEOUT_MS",
]
