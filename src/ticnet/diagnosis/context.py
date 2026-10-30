"""Diagnosis context for rule evaluation.

Encapsulates all empirical observations, statistical summaries, target definitions,
and configured thresholds for a specific diagnostic test run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ticnet.configuration.validator import ThresholdsConfig
from ticnet.models.entities import Observation, Summary, Target
from ticnet.models.enums import TargetType


@dataclass
class DiagnosisContext:
    """Immutable context encapsulating evidence and configuration for rule evaluation."""

    run_id: str
    observations: List[Observation] = field(default_factory=list)
    summaries: List[Summary] = field(default_factory=list)
    targets: Dict[str, Target] = field(default_factory=dict)
    thresholds: Optional[ThresholdsConfig] = None

    # Cached lookups
    _obs_by_target: Dict[str, List[Observation]] = field(default_factory=dict, init=False)
    _obs_by_metric: Dict[str, List[Observation]] = field(default_factory=dict, init=False)
    _summaries_by_target: Dict[str, List[Summary]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        # Build indexes for fast and reliable querying
        obs_by_target: Dict[str, List[Observation]] = {}
        obs_by_metric: Dict[str, List[Observation]] = {}
        for obs in self.observations:
            if obs.target_id:
                obs_by_target.setdefault(obs.target_id, []).append(obs)
            obs_by_metric.setdefault(obs.metric_name, []).append(obs)

        summaries_by_target: Dict[str, List[Summary]] = {}
        for s in self.summaries:
            if s.target_id:
                summaries_by_target.setdefault(s.target_id, []).append(s)

        self._obs_by_target = obs_by_target
        self._obs_by_metric = obs_by_metric
        self._summaries_by_target = summaries_by_target

    # Target Queries
    @property
    def gateway_target(self) -> Optional[Target]:
        """Return the target classified as GATEWAY, if present."""
        for t in self.targets.values():
            if t.target_type == TargetType.GATEWAY:
                return t
        return None

    @property
    def external_targets(self) -> List[Target]:
        """Return all targets classified as EXTERNAL_IP."""
        return [t for t in self.targets.values() if t.target_type == TargetType.EXTERNAL_IP]

    @property
    def dns_targets(self) -> List[Target]:
        """Return all targets classified as DNS_RESOLVER."""
        return [t for t in self.targets.values() if t.target_type == TargetType.DNS_RESOLVER]

    @property
    def throughput_target(self) -> Optional[Target]:
        """Return the target classified as THROUGHPUT_ENDPOINT, if present."""
        for t in self.targets.values():
            if t.target_type == TargetType.THROUGHPUT_ENDPOINT:
                return t
        return None

    # Observation Queries
    def get_observations_for_target(self, target_id: str) -> List[Observation]:
        """Return all observations associated with a specific target_id."""
        return self._obs_by_target.get(target_id, [])

    def get_observations_by_metric(self, metric_name: str) -> List[Observation]:
        """Return all observations with a specific metric name."""
        return self._obs_by_metric.get(metric_name, [])

    @property
    def gateway_observations(self) -> List[Observation]:
        """Return all raw observations for the local gateway."""
        gw = self.gateway_target
        if not gw:
            return []
        return self.get_observations_for_target(gw.target_id)

    @property
    def dns_observations(self) -> List[Observation]:
        """Return all raw DNS query observations."""
        dns_obs: List[Observation] = []
        for t in self.dns_targets:
            dns_obs.extend(self.get_observations_for_target(t.target_id))
        if not dns_obs:
            # Fallback to query metric name if targets were not registered with target_type
            dns_obs = self.get_observations_by_metric("dns_lookup_time")
            if not dns_obs:
                dns_obs = self.get_observations_by_metric("dns_resolution_time")
        return dns_obs

    @property
    def loaded_latency_delta_observation(self) -> Optional[Observation]:
        """Return the primary loaded latency delta observation containing comparison metadata."""
        obs_list = self.get_observations_by_metric("loaded_latency_delta")
        return obs_list[0] if obs_list else None

    @property
    def loaded_latency_metadata(self) -> Optional[Dict[str, Any]]:
        """Parse and return the JSON metadata from the loaded latency delta observation."""
        obs = self.loaded_latency_delta_observation
        if obs and obs.method_metadata:
            try:
                return json.loads(obs.method_metadata)
            except Exception:
                return None
        return None

    # Summary Queries
    def get_summaries_for_target(self, target_id: str) -> List[Summary]:
        """Return all statistical summaries associated with a specific target_id."""
        return self._summaries_by_target.get(target_id, [])

    def get_summary(
        self,
        target_id: Optional[str],
        metric_name: str,
        statistic_name: str,
    ) -> Optional[Summary]:
        """Retrieve a specific summary by target_id, metric_name, and statistic_name."""
        for s in self.summaries:
            target_match = (target_id is None) or (s.target_id == target_id)
            if target_match and s.metric_name == metric_name and s.statistic_name == statistic_name:
                return s
        return None

    @property
    def gateway_loss_summary(self) -> Optional[Summary]:
        """Return the loss percentage summary for the local gateway."""
        gw = self.gateway_target
        if not gw:
            return None
        return self.get_summary(gw.target_id, "packet_loss", "loss_percentage")

    @property
    def gateway_median_summary(self) -> Optional[Summary]:
        """Return the median RTT summary for the local gateway."""
        gw = self.gateway_target
        if not gw:
            return None
        return self.get_summary(gw.target_id, "round_trip_time", "median")

    @property
    def gateway_jitter_summary(self) -> Optional[Summary]:
        """Return the MACD delay variation summary for the local gateway."""
        gw = self.gateway_target
        if not gw:
            return None
        return self.get_summary(gw.target_id, "delay_variation", "macd_jitter")
