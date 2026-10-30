"""Fault profiles and network behavior models for TicNet historical simulation.

Defines deterministic, reproducible network characteristics including:
- Nominal baseline distributions
- Transient anomalies
- Controlled fault injections (Faults A, B, C, D, E, F)
- Gradual recovery trajectories
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class NetworkProfileMeasurement:
    """Simulated measurements for a single diagnostic observation."""

    latency_ms: float
    gateway_latency_ms: float
    jitter_ms: float
    packet_loss_pct: float
    download_mbps: float
    upload_mbps: float
    duration_s: float
    status: str = "COMPLETED"
    fault_label: Optional[str] = None
    fault_description: Optional[str] = None


@dataclass(frozen=True)
class FaultWindow:
    """Time-bounded network fault injection specification."""

    fault_id: str                          # e.g. "FAULT-A"
    name: str                              # e.g. "Latency Degradation"
    metric: str                            # e.g. "latency", "jitter", "packet_loss", "download_speed", "all"
    start_time_ist: str                    # "YYYY-MM-DDTHH:MM:SS"
    end_time_ist: str                      # "YYYY-MM-DDTHH:MM:SS"
    peak_time_ist: str                     # "YYYY-MM-DDTHH:MM:SS"
    severity: str                          # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    description: str


# Authoritative fault injection schedule across the 20-day historical window (IST)
SIMULATION_FAULTS: Tuple[FaultWindow, ...] = (
    # Fault A: Sustained Latency Degradation (13 Oct afternoon)
    FaultWindow(
        fault_id="FAULT-A",
        name="External Transit Latency Degradation",
        metric="latency",
        start_time_ist="2026-10-13T14:30:00",
        peak_time_ist="2026-10-13T16:30:00",
        end_time_ist="2026-10-13T19:00:00",
        severity="MEDIUM",
        description="Upstream ISP peering congestion introducing +80% to +140% latency inflation.",
    ),
    # Fault B: Jitter Instability & Buffer Variation (16 Oct mid-day)
    FaultWindow(
        fault_id="FAULT-B",
        name="Jitter Instability & Wi-Fi Contention",
        metric="jitter",
        start_time_ist="2026-10-16T10:30:00",
        peak_time_ist="2026-10-16T12:30:00",
        end_time_ist="2026-10-16T14:30:00",
        severity="MEDIUM",
        description="Radio queue contention causing high delay variation (>8ms jitter).",
    ),
    # Fault C: Intermittent Packet Loss Event (20 Oct afternoon)
    FaultWindow(
        fault_id="FAULT-C",
        name="Intermittent Packet Loss Event",
        metric="packet_loss",
        start_time_ist="2026-10-20T12:45:00",
        peak_time_ist="2026-10-20T14:15:00",
        end_time_ist="2026-10-20T16:30:00",
        severity="HIGH",
        description="Local gateway buffer drop and edge router link degradation producing 4-7% packet loss.",
    ),
    # Fault D: Download Throughput Bottleneck (22 Oct afternoon/evening)
    FaultWindow(
        fault_id="FAULT-D",
        name="Download Throughput Degradation",
        metric="download_speed",
        start_time_ist="2026-10-22T13:45:00",
        peak_time_ist="2026-10-22T16:30:00",
        end_time_ist="2026-10-22T20:00:00",
        severity="HIGH",
        description="ISP transit bandwidth throttling reducing download capacity by 65%.",
    ),
    # Fault E: Multi-Metric Degradation Storm (24 Oct evening)
    FaultWindow(
        fault_id="FAULT-E",
        name="Multi-Metric Network Degradation Storm",
        metric="all",
        start_time_ist="2026-10-24T15:45:00",
        peak_time_ist="2026-10-24T18:30:00",
        end_time_ist="2026-10-24T21:30:00",
        severity="CRITICAL",
        description="Severe regional upstream link failure causing simultaneous latency, loss, and throughput collapse.",
    ),
)

