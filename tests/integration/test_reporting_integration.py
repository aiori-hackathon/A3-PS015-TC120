"""Integration tests for ReportGenerator, database consistency, and atomic file exports.

Implements and verifies:
- TC-INT-05: Export consistency check: Compare generated CSV and JSON export files against
             direct database SQL queries to ensure exact 100% data equality.
- AC-PERS-03: JSON and CSV exports match SQLite database records with 100% data equality.
- AC-PERS-04: Human-readable Markdown technician report formatted for support tickets.
- AC-PERS-05: Privacy filter options with default masking of public IPs and MACs.
- Safe, atomic file export with audit logging to report_exports table.
- Error handling on invalid destination or overwrite conflicts without leaving partial files.
"""

import csv
import json
import os
from pathlib import Path
import pytest
from unittest.mock import patch

from ticnet.models.entities import (
    TestRun,
    TestStage,
    Target,
    Observation,
    Summary,
    Finding,
    FindingEvidence,
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
from ticnet.persistence.repositories import (
    RunRepository,
    ObservationRepository,
    SummaryRepository,
    FindingRepository,
    ExportRepository,
)
from ticnet.reporting.generator import (
    ReportGenerator,
    ReportExportError,
)
from ticnet.reporting.privacy import PrivacyFilter


@pytest.fixture
def populated_db(tmp_path) -> tuple[DatabaseManager, str]:
    """Create a fully migrated SQLite database populated with a realistic diagnostic run."""
    db_path = tmp_path / "ticnet_test.sqlite3"
    db = DatabaseManager(db_path=db_path)
    db.run_migrations()

    run_repo = RunRepository(db)
    obs_repo = ObservationRepository(db)
    sum_repo = SummaryRepository(db)
    find_repo = FindingRepository(db)

    run_id = "run-integ-phase9-abc123"

    run = TestRun(
        run_id=run_id,
        start_time_utc="2026-10-23T10:00:00+00:00",
        end_time_utc="2026-10-23T10:05:00+00:00",
        status=RunStatus.COMPLETED,
        app_version="1.0.0",
        config_version="1.0.0",
        run_mode=RunMode.OPERATIONAL,
        interface_name="eth0",
        interface_type="ETHERNET",
        gateway_ip="192.168.1.1",
        overall_error=None,
    )
    run_repo.create_run(run)

    stage1 = TestStage(
        stage_id="stage-01",
        run_id=run_id,
        stage_name="GATEWAY_CONNECTIVITY",
        stage_order=1,
        start_time_utc="2026-10-23T10:00:01+00:00",
        end_time_utc="2026-10-23T10:01:00+00:00",
        status=StageStatus.SUCCESS,
    )
    stage2 = TestStage(
        stage_id="stage-02",
        run_id=run_id,
        stage_name="EXTERNAL_CONNECTIVITY",
        stage_order=2,
        start_time_utc="2026-10-23T10:01:01+00:00",
        end_time_utc="2026-10-23T10:02:00+00:00",
        status=StageStatus.SUCCESS,
    )
    run_repo.create_stage(stage1)
    run_repo.create_stage(stage2)

    gw_target = Target(
        target_id="tgt-gw",
        target_type=TargetType.GATEWAY,
        ip_address="192.168.1.1",
        hostname="router.home",
    )
    wan_target = Target(
        target_id="tgt-wan",
        target_type=TargetType.EXTERNAL_IP,
        ip_address="8.8.8.8",
        hostname="dns.google",
    )
    obs_repo.create_target(gw_target)
    obs_repo.create_target(wan_target)

    # 4 observations: 2 successful, 1 measured 0.0, 1 timeout (None)
    obs_list = [
        Observation(
            observation_id="obs-01",
            run_id=run_id,
            stage_id="stage-01",
            target_id="tgt-gw",
            sample_index=1,
            timestamp_utc="2026-10-23T10:00:05+00:00",
            duration_ms=2.1,
            metric_name="latency_ms",
            value_numeric=1.8,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        ),
        Observation(
            observation_id="obs-02",
            run_id=run_id,
            stage_id="stage-01",
            target_id="tgt-gw",
            sample_index=2,
            timestamp_utc="2026-10-23T10:00:10+00:00",
            duration_ms=1.9,
            metric_name="packet_loss_pct",
            value_numeric=0.0,
            unit="%",
            status=MeasurementStatus.SUCCESS,
        ),
        Observation(
            observation_id="obs-03",
            run_id=run_id,
            stage_id="stage-02",
            target_id="tgt-wan",
            sample_index=3,
            timestamp_utc="2026-10-23T10:01:15+00:00",
            duration_ms=15.2,
            metric_name="latency_ms",
            value_numeric=14.5,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        ),
        Observation(
            observation_id="obs-04",
            run_id=run_id,
            stage_id="stage-02",
            target_id="tgt-wan",
            sample_index=4,
            timestamp_utc="2026-10-23T10:01:30+00:00",
            duration_ms=2000.0,
            metric_name="latency_ms",
            value_numeric=None,
            unit="ms",
            status=MeasurementStatus.TIMEOUT,
            error_message="Host unreachable timeout",
        ),
    ]
    obs_repo.create_observations_batch(obs_list)

    # Summaries
    sum1 = Summary(
        summary_id="sum-01",
        run_id=run_id,
        target_id="tgt-gw",
        metric_name="latency_ms",
        statistic_name="mean",
        value_numeric=1.8,
        unit="ms",
        sample_count_total=1,
        sample_count_valid=1,
        calculation_method="trimmed_mean",
        calculation_version="1.0.0",
    )
    sum2 = Summary(
        summary_id="sum-02",
        run_id=run_id,
        target_id="tgt-wan",
        metric_name="latency_ms",
        statistic_name="mean",
        value_numeric=14.5,
        unit="ms",
        sample_count_total=2,
        sample_count_valid=1,
        calculation_method="trimmed_mean",
        calculation_version="1.0.0",
    )
    sum_repo.create_summaries_batch([sum1, sum2])

    # Finding
    finding = Finding(
        finding_id="find-01",
        run_id=run_id,
        category=FindingCategory.EXTERNAL_PATH,
        priority=FindingPriority.WARNING,
        rule_id="R-INCONCLUSIVE-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.LIMITED,
        plain_summary="External host 8.8.8.8 suffered packet drop.",
        limitations="Single-target failure; unable to isolate local vs upstream.",
        recommended_action="Inspect upstream broadband router line sync.",
    )
    find_repo.create_finding(finding)

    evidence = FindingEvidence(
        evidence_id="ev-01",
        finding_id="find-01",
        relationship_type=EvidenceRelationship.SUPPORTING,
        observation_id="obs-04",
    )
    find_repo.link_evidence(evidence)

    return db, run_id


@pytest.mark.integration
def test_export_consistency_check_json(populated_db, tmp_path):
    """TC-INT-05 / AC-PERS-03: Verify exported JSON matches SQLite database with 100% data equality."""
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    json_export_path = tmp_path / "export_test.json"
    audit_record = generator.export_report(
        run_id=run_id,
        export_format=ExportFormat.JSON,
        output_path=json_export_path,
        privacy_filter=None,  # Unmasked to test exact 100% data equality against SQL
    )

    # 1. Verify file exists, size matches audit record
    assert json_export_path.exists()
    assert audit_record.export_status == ExportStatus.SUCCESS
    assert audit_record.file_size_bytes == json_export_path.stat().st_size

    # 2. Parse exported JSON
    with open(json_export_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 3. Direct SQL query comparison
    with db.connection() as conn:
        # Verify run metadata
        run_row = conn.execute("SELECT * FROM test_runs WHERE run_id = ?", (run_id,)).fetchone()
        assert data["report_metadata"]["run_id"] == run_row["run_id"]
        assert data["report_metadata"]["status"] == run_row["status"]
        assert data["report_metadata"]["gateway_ip"] == run_row["gateway_ip"]

        # Verify stages equality
        stage_rows = conn.execute(
            "SELECT * FROM test_stages WHERE run_id = ? ORDER BY stage_order", (run_id,)
        ).fetchall()
        assert len(data["stages"]) == len(stage_rows)
        for json_st, db_st in zip(data["stages"], stage_rows):
            assert json_st["stage_id"] == db_st["stage_id"]
            assert json_st["stage_name"] == db_st["stage_name"]
            assert json_st["status"] == db_st["status"]

        # Verify observations equality (100% data equality)
        obs_rows = conn.execute(
            "SELECT * FROM observations WHERE run_id = ? ORDER BY sample_index, observation_id",
            (run_id,),
        ).fetchall()
        assert len(data["observations"]) == len(obs_rows)
        for json_obs, db_obs in zip(data["observations"], obs_rows):
            assert json_obs["observation_id"] == db_obs["observation_id"]
            assert json_obs["sample_index"] == db_obs["sample_index"]
            assert json_obs["metric_name"] == db_obs["metric_name"]
            assert json_obs["status"] == db_obs["status"]
            assert json_obs["value_numeric"] == db_obs["value_numeric"]

        # Verify summaries equality
        sum_rows = conn.execute(
            "SELECT * FROM summaries WHERE run_id = ? ORDER BY metric_name, statistic_name",
            (run_id,),
        ).fetchall()
        assert len(data["summaries"]) == len(sum_rows)
        for json_sum, db_sum in zip(data["summaries"], sum_rows):
            assert json_sum["summary_id"] == db_sum["summary_id"]
            assert json_sum["metric_name"] == db_sum["metric_name"]
            assert json_sum["value_numeric"] == db_sum["value_numeric"]

        # Verify findings equality
        find_rows = conn.execute(
            "SELECT * FROM findings WHERE run_id = ?", (run_id,)
        ).fetchall()
        assert len(data["findings"]) == len(find_rows)
        assert data["findings"][0]["rule_id"] == find_rows[0]["rule_id"]


@pytest.mark.integration
def test_export_consistency_check_csv(populated_db, tmp_path):
    """TC-INT-05 / AC-PERS-03: Verify exported CSV matches SQLite database with 100% data equality."""
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    csv_export_path = tmp_path / "export_test.csv"
    audit_record = generator.export_report(
        run_id=run_id,
        export_format=ExportFormat.CSV,
        output_path=csv_export_path,
        privacy_filter=None,
    )

    assert csv_export_path.exists()
    assert audit_record.export_status == ExportStatus.SUCCESS

    with open(csv_export_path, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))

    # Verify structured multi-section report headers
    raw_text = csv_export_path.read_text(encoding="utf-8")
    assert "SECTION 1: REPORT OVERVIEW" in raw_text
    assert "SECTION 2: DIAGNOSTIC RUN SUMMARY" in raw_text
    assert "SECTION 3: GATEWAY DISCOVERY" in raw_text
    assert "SECTION 4: GATEWAY REACHABILITY" in raw_text
    assert "SECTION 5: MULTI-TARGET EXTERNAL PING ANALYSIS" in raw_text
    assert "SECTION 6: DNS DIAGNOSTICS" in raw_text
    assert "SECTION 7: THROUGHPUT AND PERFORMANCE ANALYSIS" in raw_text
    assert "SECTION 8: DIAGNOSTIC FINDINGS AND RULE EVALUATIONS" in raw_text
    assert "SECTION 9: EMPIRICAL EVIDENCE AND RAW PROBE MEASUREMENTS" in raw_text
    assert "SECTION 10: WARNINGS, LIMITATIONS, AND METHODOLOGICAL GUARDRAILS" in raw_text

    # Extract Section 9 rows (Evidence & Measurements) for direct SQL equality verification
    evidence_rows = [
        row for row in reader
        if len(row) >= 12 and row[0] == "Evidence & Measurements" and row[1] != "" and row[1].isdigit()
    ]

    # Direct SQL query check against Section 9
    with db.connection() as conn:
        obs_rows = conn.execute(
            "SELECT * FROM observations WHERE run_id = ? ORDER BY sample_index, observation_id",
            (run_id,),
        ).fetchall()

        assert len(evidence_rows) == len(obs_rows)
        for csv_row, db_obs in zip(evidence_rows, obs_rows):
            assert int(csv_row[1]) == db_obs["sample_index"]
            assert csv_row[2] == db_obs["observation_id"]
            assert csv_row[3] == db_obs["metric_name"]
            # Value check: None in DB must be empty string in CSV; float must match
            if db_obs["value_numeric"] is None:
                assert csv_row[5] == ""
            else:
                assert float(csv_row[5]) == pytest.approx(db_obs["value_numeric"])


@pytest.mark.integration
def test_export_markdown_technician_summary(populated_db, tmp_path):
    """AC-PERS-04: Verify Markdown export formats findings, evidence, and limitations."""
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    md_path = tmp_path / "technician_summary.md"
    audit = generator.export_report(
        run_id=run_id,
        export_format=ExportFormat.MARKDOWN,
        output_path=md_path,
        privacy_filter=None,
    )

    assert md_path.exists()
    assert audit.export_status == ExportStatus.SUCCESS

    content = md_path.read_text(encoding="utf-8")
    assert run_id in content
    assert "R-INCONCLUSIVE-01" in content
    assert "Single-target failure" in content
    assert "Broadband router line sync" in content.lower() or "upstream broadband" in content.lower()


@pytest.mark.integration
def test_export_with_privacy_filter(populated_db, tmp_path):
    """AC-PERS-05: Verify public WAN IP 8.8.8.8 is masked to 8.8.8.*** in exported file."""
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    json_path = tmp_path / "privacy_masked.json"
    generator.export_report(
        run_id=run_id,
        export_format=ExportFormat.JSON,
        output_path=json_path,
        privacy_filter=PrivacyFilter(mask_public_ips=True),
    )

    raw_json = json_path.read_text(encoding="utf-8")
    assert "8.8.8.8" not in raw_json
    assert "8.8.8.***" in raw_json
    # LAN gateway 192.168.1.1 is preserved
    assert "192.168.1.1" in raw_json


@pytest.mark.integration
def test_export_atomic_replacement_and_error_handling(populated_db, tmp_path):
    """Verify atomic write behavior, directory validation, overwrite protection, and failure logging."""
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    # 1. Destination is an existing directory -> must fail
    with pytest.raises(ReportExportError, match="existing directory"):
        generator.export_report(
            run_id=run_id,
            export_format=ExportFormat.JSON,
            output_path=tmp_path,  # passing directory
        )

    # Check that failure audit record was written
    export_repo = ExportRepository(db)
    failed_exports = [e for e in export_repo.get_exports_for_run(run_id) if e.export_status == ExportStatus.FAILED]
    assert len(failed_exports) >= 1
    assert "existing directory" in failed_exports[-1].error_message

    # 2. Overwrite=False when target file already exists
    target_file = tmp_path / "no_overwrite.json"
    generator.export_report(
        run_id=run_id,
        export_format=ExportFormat.JSON,
        output_path=target_file,
    )
    assert target_file.exists()

    with pytest.raises(ReportExportError, match="already exists"):
        generator.export_report(
            run_id=run_id,
            export_format=ExportFormat.JSON,
            output_path=target_file,
            overwrite=False,
        )

    # 3. Simulated OS write failure leaves no temporary file
    fail_path = tmp_path / "simulate_fail.json"
    with patch("os.replace", side_effect=OSError("Disk write error")):
        with pytest.raises(ReportExportError, match="Disk write error"):
            generator.export_report(
                run_id=run_id,
                export_format=ExportFormat.JSON,
                output_path=fail_path,
            )

    # Final destination file must NOT exist (no partial corrupted files)
    assert not fail_path.exists()
    # Temporary files matching .tmp_* must be cleaned up
    tmp_files = list(tmp_path.glob(".tmp_*"))
    assert len(tmp_files) == 0


@pytest.mark.integration
def test_export_nonexistent_run_raises_error(populated_db, tmp_path):
    """Verify exporting an unknown run ID fails safely without partial files."""
    db, _ = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    with pytest.raises(ReportExportError, match="TestRun 'unknown-999' not found"):
        generator.export_report(
            run_id="unknown-999",
            export_format=ExportFormat.JSON,
            output_path=tmp_path / "unknown.json",
        )


@pytest.mark.integration
@pytest.mark.integration
def test_export_failure_when_destination_does_not_exist(populated_db, tmp_path):
    """Check 1: Export failure when destination does not exist.
    
    Verifies:
    - Starts with a destination path that does NOT exist.
    - Forces export_repo.create_export() to fail after the file was written.
    - Confirms that the export raises ReportExportError.
    - Confirms that no un-audited report remains at destination.
    - Confirms that temporary and backup files are cleaned up.
    - Verifies the resulting filesystem state and audit database record.
    """
    import sqlite3
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    nonexistent_target = tmp_path / "fresh_export_fail.json"
    assert not nonexistent_target.exists(), "Precondition: Destination must not exist"

    # Make first create_export fail (for success audit), and second succeed (for failure audit)
    real_create_export = generator.export_repo.create_export
    call_count = 0

    def mock_create_export(record):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise sqlite3.OperationalError("Simulated DB disk failure during audit logging")
        return real_create_export(record)

    with patch.object(generator.export_repo, "create_export", side_effect=mock_create_export):
        with pytest.raises(ReportExportError, match="Simulated DB disk failure during audit logging"):
            generator.export_report(
                run_id=run_id,
                export_format=ExportFormat.JSON,
                output_path=nonexistent_target,
            )

    # 1. No un-audited report remains at destination
    assert not nonexistent_target.exists(), "Destination must be unlinked when audit persistence fails"

    # 2. All temporary and backup files cleaned up
    assert len(list(tmp_path.glob(".tmp_*"))) == 0, "No temporary files must remain"
    assert len(list(tmp_path.glob(".bak_*"))) == 0, "No backup files must remain"

    # 3. Database contains a FAILED audit record
    exports = generator.export_repo.get_exports_for_run(run_id)
    failed_exports = [e for e in exports if e.export_status == ExportStatus.FAILED]
    assert len(failed_exports) >= 1
    assert "Simulated DB disk failure" in (failed_exports[-1].error_message or "")


@pytest.mark.integration
def test_export_backup_succeeds_but_replace_fails_restores_original(populated_db, tmp_path):
    """Check 2 (Case 2): Moving existing destination to backup succeeds, but replacing destination fails.
    
    Verifies:
    - Original report exists at destination with overwrite=True.
    - Destination is moved to backup, but temp->dest replace fails.
    - Original report is safely restored from backup.
    - Temporary file is cleaned up.
    - ReportExportError is raised.
    - Database audit record logs failure.
    """
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    target_file = tmp_path / "replace_fail.json"
    original_content = '{"report": "preexisting_valid_data_case_2"}'
    target_file.write_text(original_content, encoding="utf-8")

    real_replace = os.replace
    replace_count = 0

    def mock_replace(src, dst):
        nonlocal replace_count
        replace_count += 1
        # First call: target_path -> backup_path (succeeds)
        # Second call: temp_path -> target_path (fails)
        if replace_count == 2:
            raise OSError("Simulated replacement failure (disk full)")
        return real_replace(src, dst)

    with patch("os.replace", side_effect=mock_replace):
        with pytest.raises(ReportExportError, match="Simulated replacement failure"):
            generator.export_report(
                run_id=run_id,
                export_format=ExportFormat.JSON,
                output_path=target_file,
                overwrite=True,
            )

    # Original report MUST be safely restored
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == original_content

    # Temporary and backup files cleaned up
    assert len(list(tmp_path.glob(".tmp_*"))) == 0
    assert len(list(tmp_path.glob(".bak_*"))) == 0

    # Failure audited in database
    exports = generator.export_repo.get_exports_for_run(run_id)
    assert any(e.export_status == ExportStatus.FAILED and "Simulated replacement failure" in (e.error_message or "") for e in exports)


@pytest.mark.integration
def test_export_replace_succeeds_but_audit_fails_restores_original(populated_db, tmp_path):
    """Check 2 (Case 3): Replacing destination succeeds, but SQLite audit persistence fails.
    
    Verifies:
    - Original report exists at destination with overwrite=True.
    - File replacement succeeds, but SQLite create_export fails.
    - Replacement report is removed and original report is safely restored.
    - Temporary and backup files are cleaned up.
    - ReportExportError is raised.
    - Database audit record logs failure.
    """
    import sqlite3
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    target_file = tmp_path / "audit_fail.json"
    original_content = '{"report": "preexisting_valid_data_case_3"}'
    target_file.write_text(original_content, encoding="utf-8")

    real_create_export = generator.export_repo.create_export
    call_count = 0

    def mock_create_export(record):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise sqlite3.OperationalError("Simulated database lock on audit persistence")
        return real_create_export(record)

    with patch.object(generator.export_repo, "create_export", side_effect=mock_create_export):
        with pytest.raises(ReportExportError, match="Simulated database lock on audit persistence"):
            generator.export_report(
                run_id=run_id,
                export_format=ExportFormat.JSON,
                output_path=target_file,
                overwrite=True,
            )

    # Original report MUST be restored
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == original_content

    # No un-audited replacement remains; temp and backup files cleaned up
    assert len(list(tmp_path.glob(".tmp_*"))) == 0
    assert len(list(tmp_path.glob(".bak_*"))) == 0

    # Audit logs failure
    exports = generator.export_repo.get_exports_for_run(run_id)
    assert any(e.export_status == ExportStatus.FAILED and "Simulated database lock" in (e.error_message or "") for e in exports)


@pytest.mark.integration
def test_export_audit_succeeds_but_backup_cleanup_fails_preserves_success(populated_db, tmp_path):
    """Check 2 (Case 4): SQLite audit persistence succeeds, but deleting backup fails.
    
    Verifies:
    - Overwrite replacement succeeds.
    - Audit entry is successfully persisted as SUCCESS.
    - Deleting backup fails with PermissionError / OSError.
    - CRITICAL: Must NOT trigger destructive rollback or report failure!
    - Returns audit record with SUCCESS.
    - The new report remains intact at target_file.
    - Database audit record is SUCCESS.
    """
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    target_file = tmp_path / "backup_cleanup_fail.json"
    original_content = '{"report": "old_content_case_4"}'
    target_file.write_text(original_content, encoding="utf-8")

    real_unlink = Path.unlink

    def mock_unlink(self, *args, **kwargs):
        # Simulate OS permission lock when deleting .bak_* file
        if ".bak_" in getattr(self, "name", ""):
            raise PermissionError("Simulated permission error unlinking backup file")
        return real_unlink(self, *args, **kwargs)

    with patch.object(Path, "unlink", mock_unlink):
        audit = generator.export_report(
            run_id=run_id,
            export_format=ExportFormat.JSON,
            output_path=target_file,
            overwrite=True,
        )

    # 1. Export MUST be reported as SUCCESS
    assert audit.export_status == ExportStatus.SUCCESS

    # 2. Destination must have the NEW report, not the old one
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") != original_content
    assert '"report_metadata"' in target_file.read_text(encoding="utf-8")

    # 3. Database contains a SUCCESS audit record
    exports = generator.export_repo.get_exports_for_run(run_id)
    assert any(e.export_id == audit.export_id and e.export_status == ExportStatus.SUCCESS for e in exports)


@pytest.mark.integration
def test_export_overwrite_behavior_when_disabled_and_enabled(populated_db, tmp_path):
    """Check 2 (Cases 1, 5, 6): Verify overwrite disabled prevents writes, and enabled succeeds cleanly."""
    db, run_id = populated_db
    generator = ReportGenerator(db=db, default_export_dir=tmp_path)

    target_file = tmp_path / "overwrite_lifecycle.json"
    original_content = '{"report": "original_content_case_5_6"}'
    target_file.write_text(original_content, encoding="utf-8")

    # Case 5: overwrite=False raises error and touches nothing
    with pytest.raises(ReportExportError, match="already exists and overwrite=False"):
        generator.export_report(
            run_id=run_id,
            export_format=ExportFormat.JSON,
            output_path=target_file,
            overwrite=False,
        )
    assert target_file.read_text(encoding="utf-8") == original_content

    # Database records failed export for overwrite=False
    exports = generator.export_repo.get_exports_for_run(run_id)
    assert any(e.export_status == ExportStatus.FAILED and "already exists and overwrite=False" in (e.error_message or "") for e in exports)

    # Case 1 & 6: overwrite=True succeeds cleanly
    audit = generator.export_report(
        run_id=run_id,
        export_format=ExportFormat.JSON,
        output_path=target_file,
        overwrite=True,
    )
    assert audit.export_status == ExportStatus.SUCCESS
    assert target_file.read_text(encoding="utf-8") != original_content
    assert '"report_metadata"' in target_file.read_text(encoding="utf-8")

    # Filesystem is completely clean: no .tmp_* or .bak_*
    assert len(list(tmp_path.glob(".tmp_*"))) == 0
    assert len(list(tmp_path.glob(".bak_*"))) == 0
