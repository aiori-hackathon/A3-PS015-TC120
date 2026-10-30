"""Throughput measurement adapter package (TC-ADPT-04)."""

from ticnet.measurements.throughput.adapter import (
    HARD_MAX_DURATION_SECONDS,
    HARD_MAX_TRANSFER_BYTES,
    ThroughputMeasurementAdapter,
)
from ticnet.measurements.throughput.models import ThroughputMeasurementResult

__all__ = [
    "HARD_MAX_DURATION_SECONDS",
    "HARD_MAX_TRANSFER_BYTES",
    "ThroughputMeasurementAdapter",
    "ThroughputMeasurementResult",
]
