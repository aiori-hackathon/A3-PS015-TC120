"""Measurement adapters for network probing and topology discovery."""

from ticnet.measurements.base import (
    GatewayDiscoveryResult,
    MeasurementAdapter,
)
from ticnet.measurements.dns import (
    DNSMeasurementAdapter,
    DNSMeasurementResult,
    decode_dns_response,
    encode_dns_query,
    evaluate_dns_resolvers,
    get_rcode_name,
)
from ticnet.measurements.gateway import (
    GatewayDiscoveryAdapter,
    LinuxGatewayDiscovery,
    WindowsGatewayDiscovery,
    discover_gateway,
)
from ticnet.measurements.load import (
    DEFAULT_BASELINE_PROBE_COUNT,
    DEFAULT_LOADED_PROBE_COUNT,
    DEFAULT_SAMPLE_INTERVAL_MS,
    LoadedLatencyAdapter,
    LoadedLatencyMeasurementResult,
)
from ticnet.measurements.ping import (
    LinuxPingParser,
    PingMeasurementAdapter,
    PingMeasurementResult,
    PingProbeObservation,
    WindowsPingParser,
    probe_gateway_reachability,
)
from ticnet.measurements.statistics import (
    calculate_delay_summary_stats,
    calculate_macd_jitter,
    calculate_median_rtt,
    calculate_p95_rtt,
    calculate_packet_loss_pct,
)
from ticnet.measurements.throughput import (
    HARD_MAX_DURATION_SECONDS,
    HARD_MAX_TRANSFER_BYTES,
    ThroughputMeasurementAdapter,
    ThroughputMeasurementResult,
)

__all__ = [
    "GatewayDiscoveryResult",
    "GatewayDiscoveryAdapter",
    "LinuxGatewayDiscovery",
    "MeasurementAdapter",
    "WindowsGatewayDiscovery",
    "discover_gateway",
    "PingMeasurementAdapter",
    "PingMeasurementResult",
    "PingProbeObservation",
    "WindowsPingParser",
    "LinuxPingParser",
    "probe_gateway_reachability",
    "calculate_packet_loss_pct",
    "calculate_median_rtt",
    "calculate_p95_rtt",
    "calculate_macd_jitter",
    "calculate_delay_summary_stats",
    "DNSMeasurementAdapter",
    "DNSMeasurementResult",
    "evaluate_dns_resolvers",
    "encode_dns_query",
    "decode_dns_response",
    "get_rcode_name",
    "HARD_MAX_DURATION_SECONDS",
    "HARD_MAX_TRANSFER_BYTES",
    "ThroughputMeasurementAdapter",
    "ThroughputMeasurementResult",
    "DEFAULT_BASELINE_PROBE_COUNT",
    "DEFAULT_LOADED_PROBE_COUNT",
    "DEFAULT_SAMPLE_INTERVAL_MS",
    "LoadedLatencyAdapter",
    "LoadedLatencyMeasurementResult",
]

