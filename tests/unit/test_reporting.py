"""Unit tests for TicNet report serialization, privacy masking, and export repositories.

Implements and verifies:
- TC-UNIT-10: Unit tests for JSON, CSV, Markdown serializers, privacy masking, non-zero nulls.
- SRS-F-012: Serialized diagnostic export.
- SRS-F-018: Privacy safeguards, MAC masking, public IP masking, credential redaction.
- AC-PERS-03: Deterministic JSON serialization.
- AC-PERS-04: Tabular CSV serialization preserving sample indices, metric units, null representations.
- AC-PERS-05: Configurable privacy filtering.
- Formula-injection mitigation for CSV cells starting with =, +, -, @, preserving negative numbers.
"""

import csv
import json
from pathlib import Path
import pytest
from typing import Dict, List

from ticnet.models.entities import (
    TestRun,
    TestStage,
    Target,
    Observation,
    Summary,
    Finding,
    FindingEvidence,
    ReportExport,
    new_id,
    now_utc_iso,
)
from ticnet.models.enums import (
    RunStatus,
    StageStatus,
    RunMode,
    TargetType,
    MeasurementStatus,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    EvidenceStrength,
    EvidenceRelationship,
    ExportFormat,
    ExportStatus,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.export_repo import ExportRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.reporting.privacy import (
    PrivacyFilter,
    DEFAULT_PRIVACY_FILTER,
    mask_mac_address,
    mask_public_ip_address,
    mask_credentials,
    is_local_address,
)
from ticnet.reporting.serializers import (
    DiagnosticReportData,
    serialize_to_json,
    serialize_to_csv,
    serialize_report_to_csv,
    format_report_filename,
    serialize_summaries_to_csv,
    serialize_to_markdown,
    sanitize_csv_cell,
    CSV_OBSERVATION_COLUMNS,
    CSV_SUMMARY_COLUMNS,
)
from ticnet.reporting.generator import (
    ReportGenerator,
    ReportExportError,
    redact_filesystem_path,
)


# ============================================================================
# 1. Privacy Masking & Redaction Tests (SRS-F-018, AC-PERS-05)
# ============================================================================

@pytest.mark.unit
def test_mac_address_masking():
    """Verify hardware MAC addresses are masked to AA:BB:CC:**:**:** (preserving OUI)."""
    # Colon separated
    assert mask_mac_address("Interface eth0 hwaddr 00:1A:2B:3C:4D:5E") == "Interface eth0 hwaddr 00:1A:2B:**:**:**"
    # Hyphen separated
    assert mask_mac_address("Wi-Fi adapter MAC: AA-BB-CC-11-22-33") == "Wi-Fi adapter MAC: AA-BB-CC-**-**-**"
    # Multiple MACs in text
    mixed = "BSSID 12:34:56:78:9A:BC and Client 98:76:54:32:10:FE"
    assert mask_mac_address(mixed) == "BSSID 12:34:56:**:**:** and Client 98:76:54:**:**:**"
    # String without MAC
    assert mask_mac_address("192.168.1.1 is the gateway") == "192.168.1.1 is the gateway"


@pytest.mark.unit
def test_public_ip_masking_preserves_local_and_masks_wan():
    """Verify public WAN IPs are masked to X.Y.Z.*** while RFC 1918 and loopback are preserved."""
    # RFC 1918 Private IPv4 preserved
    assert mask_public_ip_address("192.168.1.1") == "192.168.1.1"
    assert mask_public_ip_address("10.0.0.1") == "10.0.0.1"
    assert mask_public_ip_address("172.16.5.20") == "172.16.5.20"

    # Loopback and link-local preserved
    assert mask_public_ip_address("127.0.0.1") == "127.0.0.1"
    assert mask_public_ip_address("169.254.10.20") == "169.254.10.20"

    # Public WAN IPv4 masked
    assert mask_public_ip_address("8.8.8.8") == "8.8.8.***"
    assert mask_public_ip_address("1.1.1.1") == "1.1.1.***"
    assert mask_public_ip_address("142.250.190.46") == "142.250.190.***"

    # Text with both local and public
    text = "Gateway 192.168.1.1 routed to WAN 203.0.113.55 successfully."
    assert mask_public_ip_address(text) == "Gateway 192.168.1.1 routed to WAN 203.0.113.*** successfully."


@pytest.mark.unit
def test_credential_masking():
    """Verify secrets, passwords, and tokens are redacted."""
    raw = "Connecting with token=secret_token_12345 and password='MyPassword!'"
    redacted = mask_credentials(raw)
    assert "secret_token_12345" not in redacted
    assert "MyPassword!" not in redacted
    assert "[REDACTED]" in redacted


@pytest.mark.unit
def test_privacy_filter_class_and_recursive_object_redaction():
    """Verify PrivacyFilter applies selective masking across complex nested objects."""
    pf = PrivacyFilter(mask_mac_addresses=True, mask_public_ips=True, mask_credentials=True)

    nested = {
        "gateway": "192.168.1.1",
        "external_ip": "8.8.4.4",
        "mac": "AA:BB:CC:DD:EE:FF",
        "numeric_val": 42.5,
        "is_valid": True,
        "none_val": None,
        "items": [
            "Normal text",
            "Public IP: 1.0.0.1",
            {"nested_mac": "11:22:33:44:55:66"},
        ],
    }

    sanitized = pf.apply_to_obj(nested)

    assert sanitized["gateway"] == "192.168.1.1"  # preserved
    assert sanitized["external_ip"] == "8.8.4.***"  # masked
    assert sanitized["mac"] == "AA:BB:CC:**:**:**"  # masked
    assert sanitized["numeric_val"] == 42.5  # intact
    assert sanitized["is_valid"] is True  # intact
    assert sanitized["none_val"] is None  # intact
    assert sanitized["items"][1] == "Public IP: 1.0.0.***"
    assert sanitized["items"][2]["nested_mac"] == "11:22:33:**:**:**"


@pytest.mark.unit
def test_privacy_filter_selective_toggles():
    """Verify toggling individual privacy filter flags works as intended."""
    # Only MAC masking enabled, IP masking disabled
    pf_mac_only = PrivacyFilter(mask_mac_addresses=True, mask_public_ips=False, mask_credentials=False)
    text = "MAC 00:11:22:33:44:55 on 8.8.8.8"
    assert pf_mac_only.apply_to_text(text) == "MAC 00:11:22:**:**:** on 8.8.8.8"

    # Only IP masking enabled, MAC masking disabled
    pf_ip_only = PrivacyFilter(mask_mac_addresses=False, mask_public_ips=True, mask_credentials=False)
    assert pf_ip_only.apply_to_text(text) == "MAC 00:11:22:33:44:55 on 8.8.8.***"


# ============================================================================
# 2. CSV Formula Injection Defense Tests
# ============================================================================

@pytest.mark.unit
def test_csv_formula_injection_mitigation():
    """Verify cells starting with formula prefixes are escaped, while negative numbers remain intact."""
    # Check 1 explicit requirements:
    # 1. Formula expressions and malicious text neutralized with single quote (')
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert sanitize_csv_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert sanitize_csv_cell("-2+3*cmd") == "'-2+3*cmd"
    assert sanitize_csv_cell("@SUM(1,2)") == "'@SUM(1,2)"

    # Whitespace-prefixed formulas
    assert sanitize_csv_cell(" =1+1") == "' =1+1"
    assert sanitize_csv_cell("  @SUM(A1:A2)") == "'  @SUM(A1:A2)"
    assert sanitize_csv_cell("   @SUM(A1:A2)") == "'   @SUM(A1:A2)"
    assert sanitize_csv_cell("   -2+3*cmd") == "'   -2+3*cmd"

    # Tab-prefixed and carriage-return-prefixed formula payloads
    assert sanitize_csv_cell("\t=123") == "'\t=123"
    assert sanitize_csv_cell("\r+456") == "'\r+456"
    assert sanitize_csv_cell("\t-cmd") == "'\t-cmd"
    assert sanitize_csv_cell("\r@SUM(1,2)") == "'\r@SUM(1,2)"
    assert sanitize_csv_cell(" \t=1+1") == "' \t=1+1"
    assert sanitize_csv_cell(" \r+1") == "' \r+1"

    # Unit-bearing text starting with minus (neutralized to prevent spreadsheet formula execution)
    assert sanitize_csv_cell("-12.5 dBm") == "'-12.5 dBm"
    assert sanitize_csv_cell(" -12.5 dBm") == "' -12.5 dBm"
    assert sanitize_csv_cell("-85 dBm") == "'-85 dBm"
    assert sanitize_csv_cell(" -85 dBm") == "' -85 dBm"

    # Safe strings remain unchanged
    assert sanitize_csv_cell("Normal string") == "Normal string"
    assert sanitize_csv_cell("192.168.1.1") == "192.168.1.1"

    # Genuine positive and negative numeric measurements MUST NOT be corrupted
    assert sanitize_csv_cell(-12.5) == -12.5
    assert sanitize_csv_cell(12.5) == 12.5
    assert sanitize_csv_cell(-100) == -100
    assert sanitize_csv_cell(42) == 42
    assert sanitize_csv_cell(0) == 0
    assert sanitize_csv_cell(0.0) == 0.0
    assert sanitize_csv_cell("-12.5") == "-12.5"      # String representation of float
    assert sanitize_csv_cell("-12.500") == "-12.500"  # Preserves precision formatting
    assert sanitize_csv_cell("-42") == "-42"          # String representation of int
    assert sanitize_csv_cell("+10.0") == "+10.0"      # String representation of positive float
    assert sanitize_csv_cell("-0.05") == "-0.05"

    # Non-finite numbers (NaN, Infinity, -Infinity) must normalize to empty string in CSV
    assert sanitize_csv_cell(float("nan")) == ""
    assert sanitize_csv_cell(float("inf")) == ""
    assert sanitize_csv_cell(float("-inf")) == ""
    assert sanitize_csv_cell("NaN") == ""
    assert sanitize_csv_cell("Infinity") == ""
    assert sanitize_csv_cell("-Infinity") == ""
    assert sanitize_csv_cell("+Infinity") == ""
    assert sanitize_csv_cell("nan") == ""
    assert sanitize_csv_cell("inf") == ""
    assert sanitize_csv_cell("-inf") == ""

    # Null / None handling
    assert sanitize_csv_cell(None) == ""
    assert sanitize_csv_cell("") == ""


@pytest.mark.unit
def test_ipv6_address_masking():
    """Verify public WAN IPv6 addresses are masked while loopback, link-local, and ULA are preserved."""
    # Public WAN IPv6 addresses must be masked
    assert mask_public_ip_address("2001:4860:4860::8888") == "2001:4860:4860:****:****:****:****:****"
    assert mask_public_ip_address("2607:f8b0:4005:805::200e") == "2607:f8b0:4005:****:****:****:****:****"

    # Local IPv6 loopback preserved
    assert mask_public_ip_address("::1") == "::1"

    # Link-local IPv6 preserved
    assert mask_public_ip_address("fe80::1") == "fe80::1"

    # RFC 4193 Unique Local IPv6 (private LAN) preserved
    assert mask_public_ip_address("fd12:3456:789a::1") == "fd12:3456:789a::1"

    # Embedded in free text
    mixed_text = "Probed DNS 2001:4860:4860::8888 via local gateway fe80::1"
    masked = mask_public_ip_address(mixed_text)
    assert "2001:4860:4860:****:****:****:****:****" in masked
    assert "fe80::1" in masked


# ============================================================================
# 3. Serializer Determinism & Non-Zero Null Tests (TC-UNIT-10, AC-PERS-03, AC-PERS-04)
# ============================================================================

@pytest.fixture
def sample_diagnostic_data() -> DiagnosticReportData:
    """Provide a deterministic synthetic DiagnosticReportData fixture."""
    run_id = "test-run-phase9-001"
    run = TestRun(
        run_id=run_id,
        start_time_utc="2026-10-23T12:00:00+00:00",
        end_time_utc="2026-10-23T12:05:00+00:00",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        run_mode=RunMode.OPERATIONAL,
        interface_name="eth0",
        interface_type="ETHERNET",
        gateway_ip="192.168.1.1",
        overall_error=None,
    )

    stage = TestStage(
        stage_id=f"stage-{run_id}-01",
        run_id=run_id,
        stage_name="GATEWAY_CONNECTIVITY",
        stage_order=1,
        start_time_utc="2026-10-23T12:00:01+00:00",
        end_time_utc="2026-10-23T12:01:00+00:00",
        status=StageStatus.SUCCESS,
    )

    gw_target = Target(
        target_id="tgt-gw",
        target_type=TargetType.GATEWAY,
        ip_address="192.168.1.1",
        hostname="router.lan",
    )
    wan_target = Target(
        target_id="tgt-wan-google",
        target_type=TargetType.EXTERNAL_IP,
        ip_address="8.8.8.8",
        hostname="dns.google",
    )
    targets = {gw_target.target_id: gw_target, wan_target.target_id: wan_target}

    obs1 = Observation(
        observation_id="obs-001",
        run_id=run_id,
        stage_id=stage.stage_id,
        target_id=gw_target.target_id,
        sample_index=1,
        timestamp_utc="2026-10-23T12:00:02+00:00",
        duration_ms=5.2,
        metric_name="latency_ms",
        value_numeric=2.4,
        unit="ms",
        status=MeasurementStatus.SUCCESS,
    )
    # Measured 0.0 value (e.g. 0.0 ms or 0.0 packet loss)
    obs2 = Observation(
        observation_id="obs-002",
        run_id=run_id,
        stage_id=stage.stage_id,
        target_id=gw_target.target_id,
        sample_index=2,
        timestamp_utc="2026-10-23T12:00:03+00:00",
        duration_ms=4.1,
        metric_name="packet_loss_pct",
        value_numeric=0.0,
        unit="%",
        status=MeasurementStatus.SUCCESS,
    )
    # Timed-out observation with value_numeric=None (MUST NOT become 0.0)
    obs3 = Observation(
        observation_id="obs-003",
        run_id=run_id,
        stage_id=stage.stage_id,
        target_id=wan_target.target_id,
        sample_index=3,
        timestamp_utc="2026-10-23T12:00:04+00:00",
        duration_ms=1000.0,
        metric_name="latency_ms",
        value_numeric=None,
        unit="ms",
        status=MeasurementStatus.TIMEOUT,
        error_message="Probe request timed out after 1000ms",
    )

    summary1 = Summary(
        summary_id="sum-001",
        run_id=run_id,
        target_id=gw_target.target_id,
        metric_name="latency_ms",
        statistic_name="mean",
        value_numeric=2.4,
        unit="ms",
        sample_count_total=1,
        sample_count_valid=1,
        calculation_method="trimmed_mean",
        calculation_version="1.0.0",
    )
    summary2 = Summary(
        summary_id="sum-002",
        run_id=run_id,
        target_id=wan_target.target_id,
        metric_name="latency_ms",
        statistic_name="mean",
        value_numeric=None,
        unit="ms",
        sample_count_total=1,
        sample_count_valid=0,
        calculation_method="trimmed_mean",
        calculation_version="1.0.0",
    )

    finding = Finding(
        finding_id="find-001",
        run_id=run_id,
        category=FindingCategory.INCONCLUSIVE,
        priority=FindingPriority.WARNING,
        rule_id="R-INCONCLUSIVE-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.INCONCLUSIVE,
        plain_summary="External host 8.8.8.8 timed out but other targets were not reachable to corroborate outage.",
        limitations="Cannot confirm ISP outage from a single failed destination.",
        recommended_action="Test additional external destinations.",
    )

    evidence = FindingEvidence(
        evidence_id="ev-001",
        finding_id=finding.finding_id,
        relationship_type=EvidenceRelationship.SUPPORTING,
        observation_id=obs3.observation_id,
    )

    return DiagnosticReportData(
        run=run,
        stages=[stage],
        observations=[obs1, obs2, obs3],
        summaries=[summary1, summary2],
        findings=[finding],
        evidence=[evidence],
        targets=targets,
        schema_version="1.0.0",
        generated_at_utc="2026-10-23T12:06:00+00:00",
    )


@pytest.mark.unit
def test_json_serialization_validity_and_roundtrip(sample_diagnostic_data):
    """Verify JSON export serializes cleanly, parses back, and preserves types and missingness."""
    json_str = serialize_to_json(sample_diagnostic_data, privacy_filter=None)
    parsed = json.loads(json_str)

    assert parsed["schema_version"] == "1.0.0"
    assert parsed["report_metadata"]["run_id"] == "test-run-phase9-001"
    assert parsed["report_metadata"]["status"] == "COMPLETED"

    # Targets
    assert len(parsed["targets"]) == 2

    # Stages
    assert len(parsed["stages"]) == 1
    assert parsed["stages"][0]["stage_name"] == "GATEWAY_CONNECTIVITY"

    # Observations: Invariant check on missing values vs zero
    obs_list = parsed["observations"]
    assert len(obs_list) == 3

    # obs1: normal float
    assert obs_list[0]["value_numeric"] == 2.4
    # obs2: legitimate measured 0.0 (MUST be 0.0, not null)
    assert obs_list[1]["value_numeric"] == 0.0
    # obs3: timed out (MUST be null, NOT 0.0)
    assert obs_list[2]["value_numeric"] is None

    # Summaries: Invariant check on null vs value
    sum_list = parsed["summaries"]
    assert len(sum_list) == 2
    assert sum_list[0]["value_numeric"] == 2.4
    assert sum_list[1]["value_numeric"] is None  # valid samples = 0

    # Findings: Inconclusive status preserved
    findings = parsed["findings"]
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "R-INCONCLUSIVE-01"
    assert findings[0]["category"] == "INCONCLUSIVE"
    assert len(findings[0]["evidence"]) == 1
    assert findings[0]["evidence"][0]["observation_id"] == "obs-003"


@pytest.mark.unit
def test_json_serialization_with_privacy_filter(sample_diagnostic_data):
    """Verify JSON export redacts public WAN IPs and MACs when privacy filter is enabled."""
    pf = PrivacyFilter(mask_public_ips=True, mask_mac_addresses=True)
    json_str = serialize_to_json(sample_diagnostic_data, privacy_filter=pf)
    parsed = json.loads(json_str)

    # Local gateway preserved
    assert parsed["report_metadata"]["gateway_ip"] == "192.168.1.1"

    # Target 8.8.8.8 must be masked to 8.8.8.***
    wan_target = next(t for t in parsed["targets"] if t["target_id"] == "tgt-wan-google")
    assert wan_target["ip_address"] == "8.8.8.***"

    # Finding plain summary mentioning 8.8.8.8 must be masked
    finding = parsed["findings"][0]
    assert "8.8.8.8" not in finding["plain_summary"]
    assert "8.8.8.***" in finding["plain_summary"]


@pytest.mark.unit
def test_json_serialization_does_not_mutate_source(sample_diagnostic_data):
    """Verify JSON serialization leaves source data objects completely unmutated."""
    orig_gateway = sample_diagnostic_data.run.gateway_ip
    orig_val3 = sample_diagnostic_data.observations[2].value_numeric

    pf = PrivacyFilter(mask_public_ips=True)
    _ = serialize_to_json(sample_diagnostic_data, privacy_filter=pf)

    assert sample_diagnostic_data.run.gateway_ip == orig_gateway
    assert sample_diagnostic_data.observations[2].value_numeric == orig_val3


@pytest.mark.unit
def test_csv_serialization_columns_and_null_preservation(sample_diagnostic_data):
    """Verify CSV export maintains deterministic columns, escapes formulas, and preserves nulls."""
    csv_str = serialize_to_csv(
        sample_diagnostic_data.observations,
        targets=sample_diagnostic_data.targets,
        privacy_filter=None,
    )

    reader = list(csv.reader(csv_str.splitlines()))
    header = reader[0]
    assert header == CSV_OBSERVATION_COLUMNS

    rows = reader[1:]
    assert len(rows) == 3

    # Row 1: obs1 (value 2.4)
    assert rows[0][0] == "obs-001"
    assert rows[0][3] == "1"  # sample_index
    assert rows[0][9] == "2.4"

    # Row 2: obs2 (measured 0.0) -> MUST be "0.0"
    assert rows[1][0] == "obs-002"
    assert rows[1][9] == "0.0"

    # Row 3: obs3 (timed out, None) -> MUST be empty string "", NOT "0.0"
    assert rows[2][0] == "obs-003"
    assert rows[2][9] == ""
    assert rows[2][11] == "TIMEOUT"


@pytest.mark.unit
def test_csv_serialization_privacy_masking(sample_diagnostic_data):
    """Verify CSV export applies privacy masking to target endpoints and error messages."""
    pf = PrivacyFilter(mask_public_ips=True)
    csv_str = serialize_to_csv(
        sample_diagnostic_data.observations,
        targets=sample_diagnostic_data.targets,
        privacy_filter=pf,
    )

    reader = list(csv.reader(csv_str.splitlines()))
    rows = reader[1:]

    # Row 1 target endpoint (192.168.1.1) is preserved
    assert rows[0][7] == "192.168.1.1"

    # Row 3 target endpoint (8.8.8.8) is masked to 8.8.8.***
    assert rows[2][7] == "8.8.8.***"


@pytest.mark.unit
def test_markdown_serialization(sample_diagnostic_data):
    """Verify human-readable markdown contains required sections, findings, and disclaimers."""
    md_str = serialize_to_markdown(sample_diagnostic_data, privacy_filter=None)

    # Core headers
    assert "# TicNet Diagnostic Report & Technician Summary" in md_str
    assert "## 1. Diagnostic Execution Overview" in md_str
    assert "## 2. Diagnostic Findings & Rule Outcomes" in md_str
    assert "## 3. Key Telemetry & Statistical Summaries" in md_str
    assert "## 4. Methodological Limitations and Guardrails" in md_str

    # Inconclusive finding details
    assert "R-INCONCLUSIVE-01" in md_str
    assert "Single-Target Guardrail" in md_str

    # Telemetry rows
    assert "latency_ms" in md_str
    assert "2.40 ms" in md_str
    assert "NULL (No Valid Samples)" in md_str


# ============================================================================
# 4. Export Repository Tests
# ============================================================================

@pytest.fixture
def db_manager(tmp_path) -> DatabaseManager:
    """Provide an initialized SQLite database manager with migrations applied."""
    db_path = tmp_path / "test_reporting.sqlite3"
    mgr = DatabaseManager(db_path=db_path)
    mgr.run_migrations()
    return mgr


@pytest.mark.unit
def test_export_repository_crud(db_manager, sample_diagnostic_data):
    """Verify ExportRepository persists and retrieves ReportExport audit records."""
    run_repo = RunRepository(db_manager)
    run_repo.create_run(sample_diagnostic_data.run)

    export_repo = ExportRepository(db_manager)
    export_record = ReportExport(
        export_id="exp-001",
        run_id=sample_diagnostic_data.run.run_id,
        export_format=ExportFormat.JSON,
        export_timestamp_utc=now_utc_iso(),
        file_path_redacted="reports/test_report.json",
        file_size_bytes=1024,
        export_status=ExportStatus.SUCCESS,
        error_message=None,
    )

    created = export_repo.create_export(export_record)
    assert created.export_id == "exp-001"

    retrieved = export_repo.get_export("exp-001")
    assert retrieved is not None
    assert retrieved.export_id == "exp-001"
    assert retrieved.export_format == ExportFormat.JSON
    assert retrieved.file_size_bytes == 1024

    all_exports = export_repo.get_exports_for_run(sample_diagnostic_data.run.run_id)
    assert len(all_exports) == 1
    assert all_exports[0].export_id == "exp-001"


@pytest.mark.unit
def test_redact_filesystem_path():
    """Verify redact_filesystem_path replaces user home directory with ~."""
    home = str(Path.home())
    test_path = str(Path.home() / "Documents" / "report.json")
    redacted = redact_filesystem_path(test_path)
    assert home not in redacted
    assert "~" in redacted


@pytest.mark.unit
def test_serialize_summaries_to_csv(sample_diagnostic_data):
    """Verify serialize_summaries_to_csv writes valid CSV with formula sanitization."""
    csv_str = serialize_summaries_to_csv(
        sample_diagnostic_data.summaries,
        privacy_filter=None,
    )
    reader = list(csv.reader(csv_str.splitlines()))
    assert reader[0] == CSV_SUMMARY_COLUMNS
    assert len(reader) == 3
    # First row has value 2.4
    assert reader[1][5] == "2.4"
    # Second row has null value -> ""
    assert reader[2][5] == ""


@pytest.mark.unit
def test_markdown_serialization_empty_findings_and_summaries():
    """Verify Markdown report handles empty findings and empty summaries cleanly."""
    run = TestRun(
        run_id="run-empty-01",
        start_time_utc="2026-10-23T12:00:00+00:00",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        overall_error="Non-critical transient issue",
    )
    empty_data = DiagnosticReportData(run=run)
    md_str = serialize_to_markdown(empty_data)

    assert "*No diagnostic findings recorded for this run.*" in md_str
    assert "*No statistical metric summaries computed.*" in md_str
    assert "Non-critical transient issue" in md_str


@pytest.mark.unit
def test_privacy_filter_edge_cases():
    """Verify PrivacyFilter handles tuples, custom replacements, and is_local_address."""
    import ipaddress

    assert is_local_address(ipaddress.ip_address("127.0.0.1")) is True
    assert is_local_address(ipaddress.ip_address("192.168.0.1")) is True
    assert is_local_address(ipaddress.ip_address("8.8.8.8")) is False

    pf = PrivacyFilter(
        custom_replacements={"SECRET_ORG": "[REDACTED_ORG]"}
    )
    tuple_data = ("Public IP: 8.8.8.8", "Affiliation: SECRET_ORG")
    res = pf.apply_to_obj(tuple_data)
    assert isinstance(res, tuple)
    assert res[0] == "Public IP: 8.8.8.***"
    assert res[1] == "Affiliation: [REDACTED_ORG]"


@pytest.mark.unit
def test_generator_default_output_dir_and_unsupported_format(db_manager, sample_diagnostic_data, tmp_path):
    """Verify ReportGenerator uses default directory when output_path=None and handles unsupported format."""
    run_repo = RunRepository(db_manager)
    run_repo.create_run(sample_diagnostic_data.run)

    generator = ReportGenerator(db=db_manager, default_export_dir=tmp_path / "default_reports")
    audit = generator.export_report(
        run_id=sample_diagnostic_data.run.run_id,
        export_format=ExportFormat.JSON,
        output_path=None,
    )
    assert audit.export_status == ExportStatus.SUCCESS
    expected_file = tmp_path / "default_reports" / f"ticnet_report_{sample_diagnostic_data.run.run_id}_json.json"
    assert expected_file.exists()

    # Unsupported format
    with pytest.raises(ReportExportError, match="Unsupported export format"):
        generator.export_to_string(sample_diagnostic_data.run.run_id, export_format="UNSUPPORTED_XYZ")


@pytest.mark.unit
def test_privacy_filter_dataclass_recursion():
    """Verify PrivacyFilter handles dataclass instances recursively."""
    target = Target(
        target_id="tgt-dc",
        target_type=TargetType.EXTERNAL_IP,
        ip_address="8.8.8.8",
        hostname="dns.google",
    )
    pf = PrivacyFilter(mask_public_ips=True)
    res = pf.apply_to_obj(target)
    assert isinstance(res, dict)
    assert res["ip_address"] == "8.8.8.***"
    assert res["target_id"] == "tgt-dc"


@pytest.mark.unit
def test_json_and_csv_non_finite_float_handling(sample_diagnostic_data):
    """Verify non-finite float values (NaN, Inf) serialize strictly to null in JSON and empty in CSV."""
    import math
    # Set non-finite values
    sample_diagnostic_data.observations[0].value_numeric = float("nan")
    sample_diagnostic_data.observations[1].value_numeric = float("inf")
    sample_diagnostic_data.summaries[0].value_numeric = float("-inf")

    # JSON export
    json_str = serialize_to_json(sample_diagnostic_data, privacy_filter=None)
    parsed = json.loads(json_str)

    # In JSON, non-finite values must be null (never NaN or Infinity)
    assert parsed["observations"][0]["value_numeric"] is None
    assert parsed["observations"][1]["value_numeric"] is None
    assert parsed["summaries"][0]["value_numeric"] is None

    # CSV export
    csv_str = serialize_to_csv(
        sample_diagnostic_data.observations,
        targets=sample_diagnostic_data.targets,
    )
    reader = list(csv.reader(csv_str.splitlines()))
    # In CSV, non-finite values must be empty string "" (never 'nan' or 'inf')
    assert reader[1][9] == ""
    assert reader[2][9] == ""


@pytest.mark.unit
def test_format_report_filename_standard_conformance():
    """Verify generated filename matches TicNet_Diagnostic_Report_<RUN_ID>_<YYYY-MM-DD_HH-MM-SS>.<ext>."""
    fn_csv = format_report_filename("TCN-20261025-001", ExportFormat.CSV, timestamp_utc="2026-10-25T14:30:45+00:00")
    assert fn_csv == "TicNet_Diagnostic_Report_TCN-20261025-001_2026-10-25_14-30-45.csv"

    fn_json = format_report_filename("TCN-20261025-002", ExportFormat.JSON, timestamp_utc="2026-10-25T14:30:45+00:00")
    assert fn_json == "TicNet_Diagnostic_Report_TCN-20261025-002_2026-10-25_14-30-45.json"

    fn_md = format_report_filename("TCN-20261025-003", ExportFormat.MARKDOWN, timestamp_utc="2026-10-25T14:30:45+00:00")
    assert fn_md == "TicNet_Diagnostic_Report_TCN-20261025-003_2026-10-25_14-30-45.md"


@pytest.mark.unit
def test_enhanced_json_structure_and_types(sample_diagnostic_data):
    """Verify JSON export contains all 11 required logical objects, correct data types, and human readability."""
    json_str = serialize_to_json(sample_diagnostic_data, indent=2)
    parsed = json.loads(json_str)

    # Check top-level required logical objects
    required_keys = [
        "report_metadata",
        "diagnostic_run",
        "network_environment",
        "gateway_discovery",
        "gateway_reachability",
        "multi_target_ping",
        "dns_diagnostics",
        "performance_analysis",
        "diagnostic_findings",
        "evidence",
        "warnings",
        "limitations",
    ]
    for key in required_keys:
        assert key in parsed, f"Missing required top-level object: {key}"

    # Verify report_metadata
    meta = parsed["report_metadata"]
    assert meta["product_name"] == "TicNet"
    assert meta["product_version"] == "1.0.0"
    assert meta["run_id"] == sample_diagnostic_data.run.run_id

    # Verify types are preserved (numbers are numbers, booleans are booleans, arrays are arrays)
    assert isinstance(meta["product_name"], str)
    assert isinstance(parsed["diagnostic_run"]["stages_total"], int)
    assert isinstance(parsed["multi_target_ping"]["targets"], list)
    assert isinstance(parsed["diagnostic_findings"], list)
    assert isinstance(parsed["evidence"], list)
    assert isinstance(parsed["warnings"], list)
    assert isinstance(parsed["limitations"], list)

    # Verify targets in multi_target_ping are separate identifiable records
    multi_targets = parsed["multi_target_ping"]["targets"]
    assert len(multi_targets) >= 1
    for tgt in multi_targets:
        assert "target_id" in tgt
        assert "endpoint" in tgt
        assert "packets_transmitted" in tgt
        assert "packets_received" in tgt
        assert "packet_loss_pct" in tgt

    # Verify legacy top-level keys remain present for backwards-compatibility
    assert "targets" in parsed
    assert "stages" in parsed
    assert "observations" in parsed
    assert "summaries" in parsed
    assert "findings" in parsed


@pytest.mark.unit
def test_enhanced_csv_ten_sections_structure(sample_diagnostic_data):
    """Verify CSV export contains all 10 logical sections with human-readable headers and formulas sanitized."""
    # Test dual-dispatch serialize_to_csv as well as serialize_report_to_csv
    csv_str = serialize_to_csv(sample_diagnostic_data)
    assert csv_str == serialize_report_to_csv(sample_diagnostic_data)

    lines = csv_str.splitlines()
    assert len(lines) > 20

    # Verify all 10 section headers are present in order
    expected_sections = [
        "SECTION 1: REPORT OVERVIEW",
        "SECTION 2: DIAGNOSTIC RUN SUMMARY",
        "SECTION 3: GATEWAY DISCOVERY",
        "SECTION 4: GATEWAY REACHABILITY",
        "SECTION 5: MULTI-TARGET EXTERNAL PING ANALYSIS",
        "SECTION 6: DNS DIAGNOSTICS",
        "SECTION 7: THROUGHPUT AND PERFORMANCE ANALYSIS",
        "SECTION 8: DIAGNOSTIC FINDINGS AND RULE EVALUATIONS",
        "SECTION 9: EMPIRICAL EVIDENCE AND RAW PROBE MEASUREMENTS",
        "SECTION 10: WARNINGS, LIMITATIONS, AND METHODOLOGICAL GUARDRAILS",
    ]
    for section_header in expected_sections:
        assert any(section_header in line for line in lines), f"Missing section header: {section_header}"

    # Check for human-readable column labels
    csv_text = "\n".join(lines)
    assert "Average RTT (ms)" in csv_text or "Average Latency (ms)" in csv_text
    assert "Packet Loss (%)" in csv_text
    assert "Finding Title / Summary" in csv_text or "Finding ID" in csv_text
    assert "Human Label" in csv_text


@pytest.mark.unit
def test_enhanced_export_privacy_masking(sample_diagnostic_data):
    """Verify privacy masking is correctly applied across enhanced JSON and CSV exports."""
    pf = PrivacyFilter(mask_public_ips=True, mask_mac_addresses=True)
    
    # JSON with privacy filter
    json_str = serialize_to_json(sample_diagnostic_data, privacy_filter=pf)
    assert "8.8.8.***" in json_str
    assert "8.8.8.8" not in json_str
    # Private gateway IP must NOT be masked
    assert "192.168.1.1" in json_str

    # CSV with privacy filter
    csv_str = serialize_report_to_csv(sample_diagnostic_data, privacy_filter=pf)
    assert "8.8.8.***" in csv_str
    assert "8.8.8.8" not in csv_str
    assert "192.168.1.1" in csv_str

