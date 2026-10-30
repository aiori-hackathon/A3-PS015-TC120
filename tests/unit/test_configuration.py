"""Unit tests for Phase 2: Configuration Loader, Bounds Validator, and Structured Logging.

Covers all 16 required verification scenarios including boundary conditions,
type safety, boolean rejection in numeric fields, IP/hostname validation,
offline guarantee (no network traffic), privacy redaction, and logging handler idempotency.
"""

import io
import logging
import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict

import pytest

from ticnet.configuration.loader import (
    find_config_dir,
    load_app_config,
    load_targets_config,
    load_thresholds_config,
    load_toml_file,
)
from ticnet.configuration.validator import (
    AppConfig,
    ConfigError,
    ConfigNotFoundError,
    ConfigSyntaxError,
    ConfigValidationError,
    ConfigValidator,
    TargetsConfig,
    ThresholdsConfig,
    validate_host_or_ip,
    validate_hostname,
    validate_ip_address,
)
from ticnet.utils.logging import (
    RedactingFormatter,
    get_logger,
    mask_credentials,
    mask_mac_address,
    mask_public_ip_address,
    redact_sensitive_text,
    setup_logging,
)


# Test 1: Valid default configurations load correctly
@pytest.mark.unit
def test_valid_default_configurations_load():
    """Verify that default targets and thresholds TOML files load and produce typed configs."""
    app_config = load_app_config()
    assert isinstance(app_config, AppConfig)
    assert isinstance(app_config.targets, TargetsConfig)
    assert isinstance(app_config.thresholds, ThresholdsConfig)

    # Verify targets structure
    assert app_config.targets.schema_version == "1.0.0"
    assert app_config.targets.gateway.auto_discover is True
    assert app_config.targets.gateway.probe_count == 10
    assert app_config.targets.gateway.probe_timeout_ms == 1000
    assert len(app_config.targets.external_targets.targets) >= 2
    assert len(app_config.targets.dns_resolvers.resolvers) >= 1
    assert app_config.targets.throughput.max_transfer_bytes == 26214400

    # Verify thresholds structure
    assert app_config.thresholds.schema_version == "1.0.0"
    assert app_config.thresholds.gateway_thresholds.packet_loss_critical_pct == 100.0
    assert app_config.thresholds.sample_requirements.min_samples_for_median == 3
    assert app_config.thresholds.sample_requirements.min_samples_for_p95 == 10
    assert app_config.thresholds.retention.max_historical_runs == 100


# Test 2: Invalid TOML produces a controlled error
@pytest.mark.unit
def test_invalid_toml_syntax_raises_controlled_error(tmp_path: Path):
    """Verify that malformed TOML content raises ConfigSyntaxError."""
    bad_toml_file = tmp_path / "broken.toml"
    bad_toml_file.write_text("invalid = [ unclosed array", encoding="utf-8")

    with pytest.raises(ConfigSyntaxError) as exc_info:
        load_toml_file(bad_toml_file)
    assert "Malformed TOML syntax" in str(exc_info.value)
    assert issubclass(ConfigSyntaxError, ConfigError)


# Test 3: Missing configuration files are reported clearly
@pytest.mark.unit
def test_missing_configuration_file_raises_clear_error(tmp_path: Path):
    """Verify that a non-existent configuration file raises ConfigNotFoundError."""
    non_existent = tmp_path / "does_not_exist.toml"

    with pytest.raises(ConfigNotFoundError) as exc_info:
        load_targets_config(non_existent)
    assert "Configuration file not found" in str(exc_info.value)
    assert "does_not_exist.toml" in str(exc_info.value)
    assert issubclass(ConfigNotFoundError, ConfigError)


# Test 4: Missing required fields are rejected
@pytest.mark.unit
def test_missing_required_fields_rejected():
    """Verify that missing top-level or section fields raise ConfigValidationError."""
    # Missing schema_version in targets
    data_missing_version = {
        "gateway": {"auto_discover": True, "probe_count": 10, "probe_timeout_ms": 1000, "probe_interval_ms": 250},
        "external_targets": {"targets": [], "probe_count": 10, "probe_timeout_ms": 1000, "probe_interval_ms": 250},
        "dns_resolvers": {"resolvers": [], "test_domains": ["example.com"], "timeout_ms": 2000},
        "throughput": {"max_duration_seconds": 10, "max_transfer_bytes": 1000000},
        "loaded_latency": {"enabled": True, "traffic_type": "download", "sample_interval_ms": 100, "max_duration_seconds": 10},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(data_missing_version)
    assert "Missing required top-level field 'schema_version'" in str(exc.value)

    # Missing section in targets
    data_missing_section = {"schema_version": "1.0.0", "gateway": {}}
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(data_missing_section)
    assert "Missing required configuration section" in str(exc.value)


# Test 5: Unsupported schema versions are rejected
@pytest.mark.unit
@pytest.mark.parametrize("bad_version", ["0.9.0", "2.0.0", "v1.0", "beta-1"])
def test_unsupported_schema_versions_rejected(bad_version: str):
    """Verify that schema versions other than 1.0.0 are rejected."""
    raw_targets = {
        "schema_version": bad_version,
        "gateway": {},
        "external_targets": {},
        "dns_resolvers": {},
        "throughput": {},
        "loaded_latency": {},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(raw_targets)
    assert f"Unsupported targets schema_version '{bad_version}'" in str(exc.value)


# Test 6: Invalid types and booleans in numeric fields are rejected
@pytest.mark.unit
def test_invalid_types_and_booleans_in_numeric_fields_rejected():
    """Verify that passing boolean True/False into integer or float fields is rejected."""
    # Boolean in integer field: probe_count = True
    bad_data = {
        "schema_version": "1.0.0",
        "gateway": {
            "auto_discover": True,
            "probe_count": True,  # INVALID: bool must not be accepted as int!
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "external_targets": {"targets": [], "probe_count": 10, "probe_timeout_ms": 1000, "probe_interval_ms": 250},
        "dns_resolvers": {"resolvers": [], "test_domains": ["example.com"], "timeout_ms": 2000},
        "throughput": {"max_duration_seconds": 10, "max_transfer_bytes": 1000000},
        "loaded_latency": {"enabled": True, "traffic_type": "download", "sample_interval_ms": 100, "max_duration_seconds": 10},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(bad_data)
    assert "Field 'gateway.probe_count' must be an integer, got bool" in str(exc.value)

    # String in integer field
    bad_data["gateway"]["probe_count"] = "10"
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(bad_data)
    assert "Field 'gateway.probe_count' must be an integer, got str" in str(exc.value)

    # Boolean in float threshold field
    bad_thresh = {
        "schema_version": "1.0.0",
        "sample_requirements": {
            "min_gateway_samples": 5, "min_external_samples": 10, "min_dns_samples": 3,
            "min_loaded_samples": 5, "min_samples_for_median": 3, "min_samples_for_p95": 10,
        },
        "gateway_thresholds": {
            "packet_loss_critical_pct": False,  # INVALID: bool in float field
            "packet_loss_warning_pct": 10.0,
            "jitter_warning_ms": 25.0,
            "rtt_spike_warning_ms": 100.0,
        },
        "external_thresholds": {"packet_loss_warning_pct": 15.0, "jitter_warning_ms": 30.0, "min_degraded_targets": 2},
        "dns_thresholds": {"failure_rate_warning_pct": 80.0, "slow_lookup_ms": 300.0},
        "loaded_thresholds": {"bufferbloat_rtt_delta_ms": 50.0, "bufferbloat_ratio": 2.5},
        "retention": {"max_historical_runs": 100, "max_retention_days": 30, "auto_purge_on_limit": True},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_thresholds(bad_thresh)
    assert "must be a numeric value, got bool" in str(exc.value)


# Test 7: Numeric minimum and maximum boundaries are tested
@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value,expected_error",
    [
        ("probe_count", 0, "below minimum allowed value of 1"),
        ("probe_count", 51, "exceeds maximum allowed value of 50"),
        ("probe_count", -5, "below minimum allowed value of 1"),
        ("probe_timeout_ms", 99, "below minimum allowed value of 100"),
        ("probe_timeout_ms", 5001, "exceeds maximum allowed value of 5000"),
        ("probe_interval_ms", 49, "below minimum allowed value of 50"),
        ("probe_interval_ms", 5001, "exceeds maximum allowed value of 5000"),
    ],
)
def test_gateway_numeric_boundaries_enforced(field: str, value: int, expected_error: str):
    """Verify gateway numeric parameters obey strict boundaries (TC-UNIT-03)."""
    base_data = {
        "schema_version": "1.0.0",
        "gateway": {
            "auto_discover": True,
            "probe_count": 10,
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "external_targets": {
            "targets": [
                {"id": "t1", "label": "T1", "host": "1.1.1.1", "type": "ICMP", "enabled": True},
                {"id": "t2", "label": "T2", "host": "8.8.8.8", "type": "ICMP", "enabled": True},
            ],
            "probe_count": 10,
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "dns_resolvers": {
            "resolvers": [{"id": "r1", "label": "R1", "host": "1.1.1.1", "port": 53, "enabled": True}],
            "test_domains": ["example.com"],
            "timeout_ms": 2000,
        },
        "throughput": {"max_duration_seconds": 10, "max_transfer_bytes": 10000000},
        "loaded_latency": {"enabled": True, "traffic_type": "download", "sample_interval_ms": 100, "max_duration_seconds": 10},
    }
    base_data["gateway"][field] = value
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(base_data)
    assert expected_error in str(exc.value)


# Test 8: Invalid IP addresses are rejected
@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_ip",
    [
        "999.999.999.999",
        "1.2.3.4.5",
        "192.168.1.1/24",
        "not_an_ip",
        "1234::5678::9999",
        "",
        "   ",
    ],
)
def test_invalid_ip_addresses_rejected(invalid_ip: str):
    """Verify that malformed IP addresses are strictly rejected without network probing."""
    with pytest.raises(ConfigValidationError):
        validate_ip_address(invalid_ip, "test_field")


# Test 9: Valid IPv4 and IPv6 values are handled according to the schema
@pytest.mark.unit
@pytest.mark.parametrize(
    "valid_ip",
    [
        "1.1.1.1",
        "192.168.1.1",
        "8.8.8.8",
        "127.0.0.1",
        "2001:4860:4860::8888",
        "2606:4700:4700::1111",
        "::1",
    ],
)
def test_valid_ipv4_and_ipv6_accepted(valid_ip: str):
    """Verify valid IPv4 and IPv6 addresses are accepted and preserved."""
    result = validate_ip_address(valid_ip, "test_field")
    assert result == valid_ip.strip()


# Test 10: Malformed hostnames are rejected
@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_host",
    [
        "bad host with spaces.com",
        "injection; rm -rf /",
        "host&calc.exe",
        "host|ls",
        "-leading-hyphen.com",
        "trailing-hyphen-.com",
        "host..double-dot.com",
        "a" * 254 + ".com",
        "http://url-not-hostname.com",
    ],
)
def test_malformed_hostnames_rejected(bad_host: str):
    """Verify hostnames containing illegal characters or command injection syntax are rejected."""
    with pytest.raises(ConfigValidationError):
        validate_hostname(bad_host, "test_host")


# Test 11: Missing optional values remain missing rather than becoming zero
@pytest.mark.unit
def test_missing_optional_values_remain_missing():
    """Verify that omitted optional settings remain None and do not silently convert to 0 or defaults."""
    data = {
        "schema_version": "1.0.0",
        "gateway": {
            "auto_discover": True,
            # manual_override_ip is omitted entirely
            "probe_count": 10,
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "external_targets": {
            "targets": [
                {"id": "t1", "label": "T1", "host": "1.1.1.1", "type": "ICMP", "enabled": True},
                {"id": "t2", "label": "T2", "host": "8.8.8.8", "type": "ICMP", "enabled": True},
            ],
            "probe_count": 10,
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "dns_resolvers": {
            "resolvers": [{"id": "r1", "label": "R1", "host": "1.1.1.1", "port": 53, "enabled": True}],
            "test_domains": ["example.com"],
            "timeout_ms": 2000,
        },
        "throughput": {
            # live_endpoint_url and lab_endpoint_url omitted
            "max_duration_seconds": 10,
            "max_transfer_bytes": 10000000,
        },
        "loaded_latency": {"enabled": True, "traffic_type": "download", "sample_interval_ms": 100, "max_duration_seconds": 10},
    }

    config = ConfigValidator.validate_targets(data)
    assert config.gateway.manual_override_ip is None
    assert config.throughput.live_endpoint_url is None
    assert config.throughput.lab_endpoint_url is None
    # Crucially ensure not converted to 0
    assert config.gateway.manual_override_ip != 0
    assert config.gateway.manual_override_ip != "0"


# Test 12: Invalid traffic limits and unbounded settings are rejected
@pytest.mark.unit
@pytest.mark.parametrize(
    "bytes_val,expected_err",
    [
        (0, "below minimum allowed value of 1024"),
        (1023, "below minimum allowed value of 1024"),
        (104857601, "exceeds maximum allowed value of 104857600"),  # > 100MB ceiling
        (-1, "below minimum allowed value of 1024"),
    ],
)
def test_uncontrolled_throughput_bytes_rejected(bytes_val: int, expected_err: str):
    """Verify that dangerous unbounded or excessive throughput limits are rejected."""
    base_data = {
        "schema_version": "1.0.0",
        "gateway": {"auto_discover": True, "probe_count": 10, "probe_timeout_ms": 1000, "probe_interval_ms": 250},
        "external_targets": {
            "targets": [
                {"id": "t1", "label": "T1", "host": "1.1.1.1", "type": "ICMP", "enabled": True},
                {"id": "t2", "label": "T2", "host": "8.8.8.8", "type": "ICMP", "enabled": True},
            ],
            "probe_count": 10,
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "dns_resolvers": {
            "resolvers": [{"id": "r1", "label": "R1", "host": "1.1.1.1", "port": 53, "enabled": True}],
            "test_domains": ["example.com"],
            "timeout_ms": 2000,
        },
        "throughput": {"max_duration_seconds": 10, "max_transfer_bytes": bytes_val},
        "loaded_latency": {"enabled": True, "traffic_type": "download", "sample_interval_ms": 100, "max_duration_seconds": 10},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(base_data)
    assert expected_err in str(exc.value)


# Test 13: Validation does not dispatch network traffic
@pytest.mark.unit
def test_validation_does_not_dispatch_network_traffic(monkeypatch):
    """Verify that loading and validating configuration makes ZERO network or subprocess calls."""
    def forbidden_network_call(*args, **kwargs):
        raise AssertionError("Network traffic or external probe dispatched during validation!")

    monkeypatch.setattr(socket, "socket", forbidden_network_call)
    monkeypatch.setattr(socket, "gethostbyname", forbidden_network_call)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network_call)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network_call)
    monkeypatch.setattr(subprocess, "run", forbidden_network_call)

    # Load and validate complete config under full network isolation
    app_config = load_app_config()
    assert app_config is not None
    assert app_config.targets.schema_version == "1.0.0"


# Test 14: Sensitive values are not exposed in log output or error messages
@pytest.mark.unit
def test_sensitive_values_redacted_in_logging():
    """Verify MAC addresses, public WAN IPs, and credentials are redacted in log records."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    formatter = RedactingFormatter(fmt="%(message)s", redact=True)
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("ticnet.test.privacy")
    test_logger.handlers = [handler]
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    # 1. MAC Address Masking
    test_logger.info("Discovered interface MAC: 00:1A:2B:3C:4D:5E on adapter")
    output = stream.getvalue()
    assert "00:1A:2B:**:**:**" in output
    assert "3C:4D:5E" not in output

    # 2. Public IP Masking vs Local IP Preservation
    stream.seek(0)
    stream.truncate(0)
    test_logger.info("Public gateway WAN IP is 203.0.113.42, local LAN is 192.168.1.1")
    output = stream.getvalue()
    assert "203.0.113.***" in output
    assert "203.0.113.42" not in output
    assert "192.168.1.1" in output  # LAN IP preserved for diagnosis

    # 3. Secret and Credential Masking
    stream.seek(0)
    stream.truncate(0)
    test_logger.info("Connecting with password=SuperSecretToken123! and api_key=sk-secretKey999")
    output = stream.getvalue()
    assert "password=[REDACTED]" in output
    assert "api_key=[REDACTED]" in output
    assert "SuperSecretToken123!" not in output
    assert "sk-secretKey999" not in output

    # 4. JSON Formatted Redaction
    json_formatter = RedactingFormatter(json_output=True, redact=True)
    handler.setFormatter(json_formatter)
    stream.seek(0)
    stream.truncate(0)
    test_logger.info("Host AA:BB:CC:DD:EE:FF connected to 198.51.100.99 with token: Secret456")
    json_output = stream.getvalue()
    assert "AA:BB:CC:**:**:**" in json_output
    assert "198.51.100.***" in json_output
    assert "token: [REDACTED]" in json_output
    assert "DD:EE:FF" not in json_output
    assert "Secret456" not in json_output


# Test 15: Repeated logging initialization does not duplicate handlers
@pytest.mark.unit
def test_repeated_logging_initialization_does_not_duplicate_handlers():
    """Verify that calling setup_logging multiple times does not add redundant handlers."""
    # Reset logger first
    logger = setup_logging(level=logging.INFO, force=True)
    initial_handler_count = len(logger.handlers)
    assert initial_handler_count >= 1

    # Call setup_logging multiple times without force
    for _ in range(5):
        setup_logging(level=logging.INFO, force=False)

    assert len(logger.handlers) == initial_handler_count, (
        f"Handler count grew from {initial_handler_count} to {len(logger.handlers)}"
    )


# Test 16: Existing Phase 1 persistence tests continue to pass
@pytest.mark.unit
def test_persistence_integration_baseline(tmp_path: Path):
    """Verify Phase 1 persistence models and database manager remain intact and importable."""
    from ticnet.models.enums import MeasurementStatus, RunStatus, StageStatus
    from ticnet.persistence.database import DatabaseManager

    db = DatabaseManager(tmp_path / "test_baseline.sqlite3")
    applied = db.run_migrations()
    assert "V001" in applied
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM schema_version WHERE version = 'V001'")
        assert cursor.fetchone()[0] == 1

    assert RunStatus.RUNNING.value == "RUNNING"
    assert MeasurementStatus.SUCCESS.value == "SUCCESS"
    assert StageStatus.PENDING.value == "PENDING"


# Additional Boundary, Loader, and Invariant Verification Tests
@pytest.mark.unit
def test_find_config_dir_with_env_var(tmp_path: Path, monkeypatch):
    """Verify find_config_dir respects TICNET_CONFIG_DIR environment variable."""
    fake_config = tmp_path / "custom_config"
    fake_config.mkdir()
    monkeypatch.setenv("TICNET_CONFIG_DIR", str(fake_config))
    resolved = find_config_dir()
    assert resolved == fake_config


@pytest.mark.unit
def test_duplicate_target_id_rejected():
    """Verify that duplicate target IDs within external_targets are rejected."""
    data = {
        "schema_version": "1.0.0",
        "gateway": {"auto_discover": True, "probe_count": 10, "probe_timeout_ms": 1000, "probe_interval_ms": 250},
        "external_targets": {
            "targets": [
                {"id": "duplicate-id", "label": "T1", "host": "1.1.1.1", "type": "ICMP", "enabled": True},
                {"id": "duplicate-id", "label": "T2", "host": "8.8.8.8", "type": "ICMP", "enabled": True},
            ],
            "probe_count": 10,
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "dns_resolvers": {
            "resolvers": [{"id": "r1", "label": "R1", "host": "1.1.1.1", "port": 53, "enabled": True}],
            "test_domains": ["example.com"],
            "timeout_ms": 2000,
        },
        "throughput": {"max_duration_seconds": 10, "max_transfer_bytes": 10000000},
        "loaded_latency": {"enabled": True, "traffic_type": "download", "sample_interval_ms": 100, "max_duration_seconds": 10},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(data)
    assert "Duplicate target id 'duplicate-id'" in str(exc.value)


@pytest.mark.unit
def test_insufficient_enabled_targets_rejected():
    """Verify that fewer than 2 enabled external targets is rejected (bias prevention)."""
    data = {
        "schema_version": "1.0.0",
        "gateway": {"auto_discover": True, "probe_count": 10, "probe_timeout_ms": 1000, "probe_interval_ms": 250},
        "external_targets": {
            "targets": [
                {"id": "t1", "label": "T1", "host": "1.1.1.1", "type": "ICMP", "enabled": True},
                {"id": "t2", "label": "T2", "host": "8.8.8.8", "type": "ICMP", "enabled": False},
            ],
            "probe_count": 10,
            "probe_timeout_ms": 1000,
            "probe_interval_ms": 250,
        },
        "dns_resolvers": {
            "resolvers": [{"id": "r1", "label": "R1", "host": "1.1.1.1", "port": 53, "enabled": True}],
            "test_domains": ["example.com"],
            "timeout_ms": 2000,
        },
        "throughput": {"max_duration_seconds": 10, "max_transfer_bytes": 10000000},
        "loaded_latency": {"enabled": True, "traffic_type": "download", "sample_interval_ms": 100, "max_duration_seconds": 10},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_targets(data)
    assert "must have at least 2 enabled independent targets" in str(exc.value)


@pytest.mark.unit
def test_threshold_sample_and_warning_invariants_enforced():
    """Verify statistical sample hierarchy and warning <= critical invariants."""
    # 1. median samples > gateway samples
    bad_thresh = {
        "schema_version": "1.0.0",
        "sample_requirements": {
            "min_gateway_samples": 3,
            "min_external_samples": 10,
            "min_dns_samples": 3,
            "min_loaded_samples": 5,
            "min_samples_for_median": 5,  # Inconsistent: 5 > 3
            "min_samples_for_p95": 10,
        },
        "gateway_thresholds": {
            "packet_loss_critical_pct": 100.0,
            "packet_loss_warning_pct": 10.0,
            "jitter_warning_ms": 25.0,
            "rtt_spike_warning_ms": 100.0,
        },
        "external_thresholds": {"packet_loss_warning_pct": 15.0, "jitter_warning_ms": 30.0, "min_degraded_targets": 2},
        "dns_thresholds": {"failure_rate_warning_pct": 80.0, "slow_lookup_ms": 300.0},
        "loaded_thresholds": {"bufferbloat_rtt_delta_ms": 50.0, "bufferbloat_ratio": 2.5},
        "retention": {"max_historical_runs": 100, "max_retention_days": 30, "auto_purge_on_limit": True},
    }
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_thresholds(bad_thresh)
    assert "cannot be greater than min_gateway_samples" in str(exc.value)

    # 2. loss warning > loss critical
    bad_thresh["sample_requirements"]["min_samples_for_median"] = 3
    bad_thresh["gateway_thresholds"]["packet_loss_warning_pct"] = 100.0
    bad_thresh["gateway_thresholds"]["packet_loss_critical_pct"] = 50.0
    with pytest.raises(ConfigValidationError) as exc:
        ConfigValidator.validate_thresholds(bad_thresh)
    assert "cannot be greater than packet_loss_critical_pct" in str(exc.value)


@pytest.mark.unit
def test_logging_to_file_and_exception_scrubbing(tmp_path: Path):
    """Verify setup_logging writes to file and scrubs exceptions."""
    log_file = tmp_path / "app_test.log"
    logger = setup_logging(level=logging.DEBUG, log_file=log_file, force=True)

    try:
        raise ValueError("Failed connecting to 203.0.113.199 with token=SecretKey777")
    except ValueError as e:
        logger.error("Operation failed", exc_info=e)

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "203.0.113.***" in content
    assert "203.0.113.199" not in content
    assert "token=[REDACTED]" in content
    assert "SecretKey777" not in content
