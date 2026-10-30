"""Configuration validation and schema enforcement for TicNet.

Validates targets and thresholds configuration files against strict type constraints,
numeric boundaries, safety ceilings, and formatting rules without performing any network traffic.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# Exceptions
class ConfigError(Exception):
    """Base exception for all configuration-related errors."""
    pass


class ConfigNotFoundError(ConfigError):
    """Raised when a configuration file cannot be found."""
    pass


class ConfigSyntaxError(ConfigError):
    """Raised when a configuration file contains invalid TOML syntax."""
    pass


class ConfigValidationError(ConfigError):
    """Raised when configuration values violate schema, types, bounds, or safety ceilings."""
    pass


# Hostname RFC 1123 regex (labels 1-63 alphanumeric/hyphen, total <= 253 chars)
HOSTNAME_REGEX = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63}(?<!-))*$"
)

# Supported schema versions
SUPPORTED_SCHEMA_VERSIONS: Set[str] = {"1.0.0"}


# Validated Configuration Dataclasses
@dataclass(frozen=True)
class TargetItem:
    """Individual external measurement target."""
    id: str
    label: str
    host: str
    type: str
    enabled: bool


@dataclass(frozen=True)
class DNSResolverItem:
    """Individual DNS resolver definition."""
    id: str
    label: str
    host: str
    port: int
    enabled: bool


@dataclass(frozen=True)
class GatewayConfig:
    """Default gateway discovery and probing settings."""
    auto_discover: bool
    manual_override_ip: Optional[str]
    probe_count: int
    probe_timeout_ms: int
    probe_interval_ms: int


@dataclass(frozen=True)
class ExternalTargetsConfig:
    """Multi-target external reachability probe settings."""
    targets: List[TargetItem]
    probe_count: int
    probe_timeout_ms: int
    probe_interval_ms: int


@dataclass(frozen=True)
class DNSResolversConfig:
    """DNS subsystem measurement settings."""
    resolvers: List[DNSResolverItem]
    test_domains: List[str]
    timeout_ms: int


@dataclass(frozen=True)
class ThroughputConfig:
    """Throughput measurement safety bounds and endpoints."""
    live_endpoint_url: Optional[str]
    lab_endpoint_url: Optional[str]
    max_duration_seconds: int
    max_transfer_bytes: int


@dataclass(frozen=True)
class LoadedLatencyConfig:
    """Idle vs loaded latency (bufferbloat) measurement settings."""
    enabled: bool
    traffic_type: str
    sample_interval_ms: int
    max_duration_seconds: int


@dataclass(frozen=True)
class TargetsConfig:
    """Complete network targets configuration."""
    schema_version: str
    gateway: GatewayConfig
    external_targets: ExternalTargetsConfig
    dns_resolvers: DNSResolversConfig
    throughput: ThroughputConfig
    loaded_latency: LoadedLatencyConfig


@dataclass(frozen=True)
class SampleRequirementsConfig:
    """Sample size requirements for metric validity."""
    min_gateway_samples: int
    min_external_samples: int
    min_dns_samples: int
    min_loaded_samples: int
    min_samples_for_median: int
    min_samples_for_p95: int


@dataclass(frozen=True)
class GatewayThresholdsConfig:
    """Diagnostic thresholds for gateway evaluation."""
    packet_loss_critical_pct: float
    packet_loss_warning_pct: float
    jitter_warning_ms: float
    rtt_spike_warning_ms: float


@dataclass(frozen=True)
class ExternalThresholdsConfig:
    """Diagnostic thresholds for external path evaluation."""
    packet_loss_warning_pct: float
    jitter_warning_ms: float
    min_degraded_targets: int


@dataclass(frozen=True)
class DNSThresholdsConfig:
    """Diagnostic thresholds for DNS subsystem."""
    failure_rate_warning_pct: float
    slow_lookup_ms: float


@dataclass(frozen=True)
class LoadedThresholdsConfig:
    """Diagnostic thresholds for bufferbloat evaluation."""
    bufferbloat_rtt_delta_ms: float
    bufferbloat_ratio: float


@dataclass(frozen=True)
class RetentionConfig:
    """Diagnostic run history retention settings."""
    max_historical_runs: int
    max_retention_days: int
    auto_purge_on_limit: bool


@dataclass(frozen=True)
class ThresholdsConfig:
    """Complete diagnostic thresholds configuration."""
    schema_version: str
    sample_requirements: SampleRequirementsConfig
    gateway_thresholds: GatewayThresholdsConfig
    external_thresholds: ExternalThresholdsConfig
    dns_thresholds: DNSThresholdsConfig
    loaded_thresholds: LoadedThresholdsConfig
    retention: RetentionConfig


@dataclass(frozen=True)
class AppConfig:
    """Combined application configuration containing targets and thresholds."""
    targets: TargetsConfig
    thresholds: ThresholdsConfig


# Type and Bounds Validation Helpers
def _validate_int(
    val: Any,
    field_name: str,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
) -> int:
    """Validate that val is strictly an integer (not a bool or float) and within bounds."""
    if type(val) is not int:
        raise ConfigValidationError(
            f"Field '{field_name}' must be an integer, got {type(val).__name__} ({val!r})"
        )
    if min_val is not None and val < min_val:
        raise ConfigValidationError(
            f"Field '{field_name}' value {val} is below minimum allowed value of {min_val}"
        )
    if max_val is not None and val > max_val:
        raise ConfigValidationError(
            f"Field '{field_name}' value {val} exceeds maximum allowed value of {max_val}"
        )
    return val


def _validate_float(
    val: Any,
    field_name: str,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Validate that val is a float or int (not a bool) and within bounds."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ConfigValidationError(
            f"Field '{field_name}' must be a numeric value, got {type(val).__name__} ({val!r})"
        )
    float_val = float(val)
    if min_val is not None and float_val < min_val:
        raise ConfigValidationError(
            f"Field '{field_name}' value {float_val} is below minimum allowed value of {min_val}"
        )
    if max_val is not None and float_val > max_val:
        raise ConfigValidationError(
            f"Field '{field_name}' value {float_val} exceeds maximum allowed value of {max_val}"
        )
    return float_val


def _validate_bool(val: Any, field_name: str) -> bool:
    """Validate that val is strictly a boolean."""
    if type(val) is not bool:
        raise ConfigValidationError(
            f"Field '{field_name}' must be a boolean, got {type(val).__name__} ({val!r})"
        )
    return val


def _validate_str(val: Any, field_name: str, allow_empty: bool = False) -> str:
    """Validate that val is a string and optionally non-empty."""
    if not isinstance(val, str):
        raise ConfigValidationError(
            f"Field '{field_name}' must be a string, got {type(val).__name__}"
        )
    if not allow_empty and not val.strip():
        raise ConfigValidationError(f"Field '{field_name}' cannot be an empty string")
    return val.strip()


def validate_ip_address(ip_str: str, field_name: str) -> str:
    """Validate IPv4 or IPv6 address using ipaddress module.
    
    Ensures single host address, rejecting networks, empty strings, and malformed IPs.
    Does NOT perform any network lookups.
    """
    if not isinstance(ip_str, str) or not ip_str.strip():
        raise ConfigValidationError(f"Field '{field_name}' must be a non-empty IP address string")
    cleaned = ip_str.strip()
    try:
        ipaddress.ip_address(cleaned)
        return cleaned
    except ValueError:
        raise ConfigValidationError(
            f"Field '{field_name}' contains invalid IP address: '{cleaned}'"
        )


def validate_hostname(
    hostname: str,
    field_name: str,
    allow_special: Optional[Set[str]] = None,
) -> str:
    """Validate hostname according to RFC 1123 rules without network lookups."""
    if not isinstance(hostname, str) or not hostname.strip():
        raise ConfigValidationError(f"Field '{field_name}' must be a non-empty hostname string")
    cleaned = hostname.strip().lower()
    if allow_special and cleaned in allow_special:
        return cleaned
    if len(cleaned) > 253:
        raise ConfigValidationError(
            f"Field '{field_name}' hostname exceeds maximum length of 253 characters"
        )
    if not HOSTNAME_REGEX.match(cleaned):
        raise ConfigValidationError(
            f"Field '{field_name}' contains malformed hostname: '{hostname}'"
        )
    return cleaned


def validate_host_or_ip(
    host_str: Any,
    field_name: str,
    allow_special: Optional[Set[str]] = None,
) -> str:
    """Validate that string is either a valid IP address or a valid hostname."""
    if not isinstance(host_str, str) or not host_str.strip():
        raise ConfigValidationError(f"Field '{field_name}' must be a non-empty host string")
    cleaned = host_str.strip()
    if allow_special and cleaned.lower() in allow_special:
        return cleaned.lower()
    try:
        ipaddress.ip_address(cleaned)
        return cleaned
    except ValueError:
        return validate_hostname(cleaned, field_name, allow_special=allow_special)


def validate_url(url_str: str, field_name: str) -> str:
    """Validate basic HTTP/HTTPS URL format without network access."""
    if not isinstance(url_str, str) or not url_str.strip():
        raise ConfigValidationError(f"Field '{field_name}' must be a non-empty URL string")
    cleaned = url_str.strip()
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise ConfigValidationError(
            f"Field '{field_name}' must be an http:// or https:// URL, got: '{cleaned}'"
        )
    return cleaned


class ConfigValidator:
    """Validates loaded configuration dictionaries against schema and boundaries."""

    @classmethod
    def validate_targets(cls, data: Dict[str, Any]) -> TargetsConfig:
        """Validate raw targets configuration dictionary and return typed TargetsConfig."""
        if not isinstance(data, dict):
            raise ConfigValidationError(f"Targets configuration must be a dictionary, got {type(data).__name__}")

        # Schema Version Check
        schema_version = data.get("schema_version")
        if not schema_version:
            raise ConfigValidationError("Missing required top-level field 'schema_version'")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ConfigValidationError(
                f"Unsupported targets schema_version '{schema_version}'. Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )

        # Required Sections
        required_sections = ["gateway", "external_targets", "dns_resolvers", "throughput", "loaded_latency"]
        for section in required_sections:
            if section not in data:
                raise ConfigValidationError(f"Missing required configuration section '{section}'")
            if not isinstance(data[section], dict):
                raise ConfigValidationError(f"Section '{section}' must be a table/dictionary")

        # 1. Gateway Section
        gw_raw = data["gateway"]
        auto_discover = _validate_bool(gw_raw.get("auto_discover"), "gateway.auto_discover")

        # Distinguish omitted optional setting from explicitly configured value
        manual_override_ip: Optional[str] = None
        if "manual_override_ip" in gw_raw:
            raw_ip = gw_raw["manual_override_ip"]
            if raw_ip is not None and str(raw_ip).strip() != "":
                manual_override_ip = validate_ip_address(str(raw_ip), "gateway.manual_override_ip")

        # Probe count limits: 1 to 50
        gw_probe_count = _validate_int(gw_raw.get("probe_count"), "gateway.probe_count", min_val=1, max_val=50)
        # Probe timeout limits: 100ms to 5000ms
        gw_probe_timeout = _validate_int(gw_raw.get("probe_timeout_ms"), "gateway.probe_timeout_ms", min_val=100, max_val=5000)
        # Probe interval limits: 50ms to 5000ms
        gw_probe_interval = _validate_int(gw_raw.get("probe_interval_ms"), "gateway.probe_interval_ms", min_val=50, max_val=5000)

        gateway_config = GatewayConfig(
            auto_discover=auto_discover,
            manual_override_ip=manual_override_ip,
            probe_count=gw_probe_count,
            probe_timeout_ms=gw_probe_timeout,
            probe_interval_ms=gw_probe_interval,
        )

        # 2. External Targets Section
        ext_raw = data["external_targets"]
        ext_probe_count = _validate_int(ext_raw.get("probe_count"), "external_targets.probe_count", min_val=1, max_val=50)
        ext_probe_timeout = _validate_int(ext_raw.get("probe_timeout_ms"), "external_targets.probe_timeout_ms", min_val=100, max_val=5000)
        ext_probe_interval = _validate_int(ext_raw.get("probe_interval_ms"), "external_targets.probe_interval_ms", min_val=50, max_val=5000)

        targets_list_raw = ext_raw.get("targets")
        if not isinstance(targets_list_raw, list) or len(targets_list_raw) == 0:
            raise ConfigValidationError("external_targets.targets must be a non-empty list of targets")

        validated_targets: List[TargetItem] = []
        enabled_target_count = 0
        target_ids: Set[str] = set()

        for idx, t_raw in enumerate(targets_list_raw):
            if not isinstance(t_raw, dict):
                raise ConfigValidationError(f"Target entry #{idx} must be a dictionary")
            t_id = _validate_str(t_raw.get("id"), f"external_targets.targets[{idx}].id")
            if t_id in target_ids:
                raise ConfigValidationError(f"Duplicate target id '{t_id}' in external_targets")
            target_ids.add(t_id)

            t_label = _validate_str(t_raw.get("label"), f"external_targets.targets[{idx}].label")
            t_host = validate_host_or_ip(t_raw.get("host"), f"external_targets.targets[{idx}].host")
            t_type = _validate_str(t_raw.get("type"), f"external_targets.targets[{idx}].type").upper()
            if t_type not in {"ICMP", "UDP", "TCP"}:
                raise ConfigValidationError(f"Target '{t_id}' type '{t_type}' must be one of: ICMP, UDP, TCP")
            t_enabled = _validate_bool(t_raw.get("enabled"), f"external_targets.targets[{idx}].enabled")

            if t_enabled:
                enabled_target_count += 1

            validated_targets.append(
                TargetItem(id=t_id, label=t_label, host=t_host, type=t_type, enabled=t_enabled)
            )

        # Minimum 2 independent enabled targets required to prevent single-target bias
        if enabled_target_count < 2:
            raise ConfigValidationError(
                f"external_targets must have at least 2 enabled independent targets (found {enabled_target_count})"
            )

        external_config = ExternalTargetsConfig(
            targets=validated_targets,
            probe_count=ext_probe_count,
            probe_timeout_ms=ext_probe_timeout,
            probe_interval_ms=ext_probe_interval,
        )

        # 3. DNS Resolvers Section
        dns_raw = data["dns_resolvers"]
        dns_timeout = _validate_int(dns_raw.get("timeout_ms"), "dns_resolvers.timeout_ms", min_val=100, max_val=5000)

        test_domains_raw = dns_raw.get("test_domains")
        if not isinstance(test_domains_raw, list) or len(test_domains_raw) == 0:
            raise ConfigValidationError("dns_resolvers.test_domains must be a non-empty list of domain names")

        validated_domains: List[str] = []
        for idx, dom in enumerate(test_domains_raw):
            v_dom = validate_hostname(dom, f"dns_resolvers.test_domains[{idx}]")
            validated_domains.append(v_dom)

        resolvers_list_raw = dns_raw.get("resolvers")
        if not isinstance(resolvers_list_raw, list) or len(resolvers_list_raw) == 0:
            raise ConfigValidationError("dns_resolvers.resolvers must be a non-empty list of resolvers")

        validated_resolvers: List[DNSResolverItem] = []
        enabled_resolver_count = 0
        resolver_ids: Set[str] = set()

        for idx, r_raw in enumerate(resolvers_list_raw):
            if not isinstance(r_raw, dict):
                raise ConfigValidationError(f"Resolver entry #{idx} must be a dictionary")
            r_id = _validate_str(r_raw.get("id"), f"dns_resolvers.resolvers[{idx}].id")
            if r_id in resolver_ids:
                raise ConfigValidationError(f"Duplicate resolver id '{r_id}' in dns_resolvers")
            resolver_ids.add(r_id)

            r_label = _validate_str(r_raw.get("label"), f"dns_resolvers.resolvers[{idx}].label")
            r_host = validate_host_or_ip(
                r_raw.get("host"),
                f"dns_resolvers.resolvers[{idx}].host",
                allow_special={"gateway"},
            )
            r_port = _validate_int(r_raw.get("port"), f"dns_resolvers.resolvers[{idx}].port", min_val=1, max_val=65535)
            r_enabled = _validate_bool(r_raw.get("enabled"), f"dns_resolvers.resolvers[{idx}].enabled")

            if r_enabled:
                enabled_resolver_count += 1

            validated_resolvers.append(
                DNSResolverItem(id=r_id, label=r_label, host=r_host, port=r_port, enabled=r_enabled)
            )

        if enabled_resolver_count < 1:
            raise ConfigValidationError("dns_resolvers must contain at least 1 enabled resolver")

        dns_config = DNSResolversConfig(
            resolvers=validated_resolvers,
            test_domains=validated_domains,
            timeout_ms=dns_timeout,
        )

        # 4. Throughput Section
        tp_raw = data["throughput"]
        tp_duration = _validate_int(
            tp_raw.get("max_duration_seconds"),
            "throughput.max_duration_seconds",
            min_val=1,
            max_val=30,
        )
        # Safety bounds: maximum 100MB (104857600 bytes)
        tp_bytes = _validate_int(
            tp_raw.get("max_transfer_bytes"),
            "throughput.max_transfer_bytes",
            min_val=1024,
            max_val=104857600,
        )

        live_url = tp_raw.get("live_endpoint_url")
        if live_url is not None and str(live_url).strip() != "":
            live_url = validate_url(str(live_url), "throughput.live_endpoint_url")
        else:
            live_url = None

        lab_url = tp_raw.get("lab_endpoint_url")
        if lab_url is not None and str(lab_url).strip() != "":
            lab_url = validate_url(str(lab_url), "throughput.lab_endpoint_url")
        else:
            lab_url = None

        throughput_config = ThroughputConfig(
            live_endpoint_url=live_url,
            lab_endpoint_url=lab_url,
            max_duration_seconds=tp_duration,
            max_transfer_bytes=tp_bytes,
        )

        # 5. Loaded Latency Section
        ll_raw = data["loaded_latency"]
        ll_enabled = _validate_bool(ll_raw.get("enabled"), "loaded_latency.enabled")
        ll_traffic_type = _validate_str(ll_raw.get("traffic_type"), "loaded_latency.traffic_type").lower()
        if ll_traffic_type not in {"download", "upload", "bidirectional"}:
            raise ConfigValidationError(
                f"loaded_latency.traffic_type must be one of: download, upload, bidirectional; got '{ll_traffic_type}'"
            )
        ll_interval = _validate_int(
            ll_raw.get("sample_interval_ms"),
            "loaded_latency.sample_interval_ms",
            min_val=20,
            max_val=1000,
        )
        ll_duration = _validate_int(
            ll_raw.get("max_duration_seconds"),
            "loaded_latency.max_duration_seconds",
            min_val=1,
            max_val=30,
        )

        loaded_latency_config = LoadedLatencyConfig(
            enabled=ll_enabled,
            traffic_type=ll_traffic_type,
            sample_interval_ms=ll_interval,
            max_duration_seconds=ll_duration,
        )

        return TargetsConfig(
            schema_version=schema_version,
            gateway=gateway_config,
            external_targets=external_config,
            dns_resolvers=dns_config,
            throughput=throughput_config,
            loaded_latency=loaded_latency_config,
        )

    @classmethod
    def validate_thresholds(cls, data: Dict[str, Any]) -> ThresholdsConfig:
        """Validate raw thresholds configuration dictionary and return typed ThresholdsConfig."""
        if not isinstance(data, dict):
            raise ConfigValidationError(f"Thresholds configuration must be a dictionary, got {type(data).__name__}")

        # Schema Version Check (top-level or inside [general])
        schema_version = data.get("schema_version")
        if not schema_version and "general" in data and isinstance(data["general"], dict):
            schema_version = data["general"].get("config_version")
        if not schema_version:
            raise ConfigValidationError("Missing required schema_version field in thresholds configuration")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ConfigValidationError(
                f"Unsupported thresholds schema_version '{schema_version}'. Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )

        # Required Sections
        required_sections = [
            "sample_requirements",
            "gateway_thresholds",
            "external_thresholds",
            "dns_thresholds",
            "loaded_thresholds",
            "retention",
        ]
        for section in required_sections:
            if section not in data:
                raise ConfigValidationError(f"Missing required configuration section '{section}'")
            if not isinstance(data[section], dict):
                raise ConfigValidationError(f"Section '{section}' must be a table/dictionary")

        # 1. Sample Requirements Section
        sr_raw = data["sample_requirements"]
        min_gw = _validate_int(sr_raw.get("min_gateway_samples"), "sample_requirements.min_gateway_samples", min_val=1, max_val=50)
        min_ext = _validate_int(sr_raw.get("min_external_samples"), "sample_requirements.min_external_samples", min_val=1, max_val=50)
        min_dns = _validate_int(sr_raw.get("min_dns_samples"), "sample_requirements.min_dns_samples", min_val=1, max_val=50)
        min_ll = _validate_int(sr_raw.get("min_loaded_samples"), "sample_requirements.min_loaded_samples", min_val=1, max_val=50)
        min_median = _validate_int(sr_raw.get("min_samples_for_median"), "sample_requirements.min_samples_for_median", min_val=1, max_val=50)
        min_p95 = _validate_int(sr_raw.get("min_samples_for_p95"), "sample_requirements.min_samples_for_p95", min_val=1, max_val=50)

        # Invariant check: median sample requirement cannot exceed target sample requirement
        if min_median > min_gw:
            raise ConfigValidationError(
                f"min_samples_for_median ({min_median}) cannot be greater than min_gateway_samples ({min_gw})"
            )

        sample_config = SampleRequirementsConfig(
            min_gateway_samples=min_gw,
            min_external_samples=min_ext,
            min_dns_samples=min_dns,
            min_loaded_samples=min_ll,
            min_samples_for_median=min_median,
            min_samples_for_p95=min_p95,
        )

        # 2. Gateway Thresholds Section
        gt_raw = data["gateway_thresholds"]
        loss_crit = _validate_float(gt_raw.get("packet_loss_critical_pct"), "gateway_thresholds.packet_loss_critical_pct", min_val=0.0, max_val=100.0)
        loss_warn = _validate_float(gt_raw.get("packet_loss_warning_pct"), "gateway_thresholds.packet_loss_warning_pct", min_val=0.0, max_val=100.0)
        jitter_warn = _validate_float(gt_raw.get("jitter_warning_ms"), "gateway_thresholds.jitter_warning_ms", min_val=0.0, max_val=10000.0)
        rtt_warn = _validate_float(gt_raw.get("rtt_spike_warning_ms"), "gateway_thresholds.rtt_spike_warning_ms", min_val=0.0, max_val=10000.0)

        if loss_warn > loss_crit:
            raise ConfigValidationError(
                f"packet_loss_warning_pct ({loss_warn}%) cannot be greater than packet_loss_critical_pct ({loss_crit}%)"
            )

        gateway_thresholds = GatewayThresholdsConfig(
            packet_loss_critical_pct=loss_crit,
            packet_loss_warning_pct=loss_warn,
            jitter_warning_ms=jitter_warn,
            rtt_spike_warning_ms=rtt_warn,
        )

        # 3. External Thresholds Section
        et_raw = data["external_thresholds"]
        ext_loss_warn = _validate_float(et_raw.get("packet_loss_warning_pct"), "external_thresholds.packet_loss_warning_pct", min_val=0.0, max_val=100.0)
        ext_jitter_warn = _validate_float(et_raw.get("jitter_warning_ms"), "external_thresholds.jitter_warning_ms", min_val=0.0, max_val=10000.0)
        min_degraded = _validate_int(et_raw.get("min_degraded_targets"), "external_thresholds.min_degraded_targets", min_val=1, max_val=10)

        external_thresholds = ExternalThresholdsConfig(
            packet_loss_warning_pct=ext_loss_warn,
            jitter_warning_ms=ext_jitter_warn,
            min_degraded_targets=min_degraded,
        )

        # 4. DNS Thresholds Section
        dt_raw = data["dns_thresholds"]
        dns_fail_warn = _validate_float(dt_raw.get("failure_rate_warning_pct"), "dns_thresholds.failure_rate_warning_pct", min_val=0.0, max_val=100.0)
        dns_slow = _validate_float(dt_raw.get("slow_lookup_ms"), "dns_thresholds.slow_lookup_ms", min_val=0.0, max_val=10000.0)

        dns_thresholds = DNSThresholdsConfig(
            failure_rate_warning_pct=dns_fail_warn,
            slow_lookup_ms=dns_slow,
        )

        # 5. Loaded Latency Thresholds Section
        lt_raw = data["loaded_thresholds"]
        bb_delta = _validate_float(lt_raw.get("bufferbloat_rtt_delta_ms"), "loaded_thresholds.bufferbloat_rtt_delta_ms", min_val=0.0, max_val=10000.0)
        bb_ratio = _validate_float(lt_raw.get("bufferbloat_ratio"), "loaded_thresholds.bufferbloat_ratio", min_val=1.0, max_val=50.0)

        loaded_thresholds = LoadedThresholdsConfig(
            bufferbloat_rtt_delta_ms=bb_delta,
            bufferbloat_ratio=bb_ratio,
        )

        # 6. Retention Section
        ret_raw = data["retention"]
        max_runs = _validate_int(ret_raw.get("max_historical_runs"), "retention.max_historical_runs", min_val=1, max_val=10000)
        max_days = _validate_int(ret_raw.get("max_retention_days"), "retention.max_retention_days", min_val=1, max_val=365)
        auto_purge = _validate_bool(ret_raw.get("auto_purge_on_limit"), "retention.auto_purge_on_limit")

        retention_config = RetentionConfig(
            max_historical_runs=max_runs,
            max_retention_days=max_days,
            auto_purge_on_limit=auto_purge,
        )

        return ThresholdsConfig(
            schema_version=schema_version,
            sample_requirements=sample_config,
            gateway_thresholds=gateway_thresholds,
            external_thresholds=external_thresholds,
            dns_thresholds=dns_thresholds,
            loaded_thresholds=loaded_thresholds,
            retention=retention_config,
        )
