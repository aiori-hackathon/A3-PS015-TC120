"""Unit tests for TicNet SQLite persistence layer and domain entities.

Covers:
- DatabaseManager connection, WAL mode, foreign keys, and atomic migrations.
- Invariant enforcement: Non-zero NULL semantics for missing/timed-out measurements.
- Repositories: RunRepository, ObservationRepository, SummaryRepository, FindingRepository, LabTrialRepository.
- Transaction rollback behavior on errors.
- Persistence across closing and reopening.
- Concurrency under multi-threaded operations.
"""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from pathlib import Path

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
    LabScenario,
    TrialVerificationStatus,
    TrialCleanupStatus,
    Status,
)
from ticnet.models.entities import (
    TestRun,
    TestStage,
    Target,
    Observation,
    Summary,
    Finding,
    FindingEvidence,
    LabTrial,
    now_utc_iso,
    new_id,
)
from ticnet.persistence.database import DatabaseManager, MigrationError
from ticnet.persistence.repositories import (
    RunRepository,
    ObservationRepository,
    SummaryRepository,
    FindingRepository,
    LabTrialRepository,
)


@pytest.fixture
def db_path(tmp_path) -> Path:
    """Return a path to an isolated temporary SQLite database."""
    return tmp_path / "test_ticnet.sqlite3"


@pytest.fixture
def db_manager(db_path) -> DatabaseManager:
    """Provide an initialized DatabaseManager with migrations applied."""
    mgr = DatabaseManager(db_path=db_path)
    mgr.run_migrations()
    return mgr


# ============================================================================
# 1. Schema and Migration Tests
# ============================================================================

@pytest.mark.unit
def test_fresh_database_initialization_and_migration(db_manager):
    """Verify fresh database initializes and applies migrations."""
    applied = db_manager.get_applied_migrations()
    assert len(applied) >= 1
    versions = [a["version"] for a in applied]
    assert "V001" in versions
    assert "initial schema" in applied[0]["description"].lower()


@pytest.mark.unit
def test_migration_idempotent(db_manager):
    """Verify running migrations a second time does not reapply or fail."""
    second_run = db_manager.run_migrations()
    assert second_run == []
    applied = db_manager.get_applied_migrations()
    assert len(applied) >= 1


@pytest.mark.unit
def test_tables_and_indexes_created(db_manager):
    """Verify all required tables and indexes exist in SQLite schema."""
    expected_tables = {
        "schema_version",
        "test_runs",
        "test_stages",
        "targets",
        "observations",
        "summaries",
        "findings",
        "finding_evidence",
        "lab_trials",
        "report_exports",
    }
    with db_manager.connection() as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row["name"] for row in cursor.fetchall()}
        assert expected_tables.issubset(tables)

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indexes = {row["name"] for row in cursor.fetchall()}
        assert "idx_obs_run_id" in indexes
        assert "idx_sum_run_id" in indexes
        assert "idx_find_run_id" in indexes


@pytest.mark.unit
def test_foreign_keys_pragma_enabled(db_manager):
    """Verify foreign key enforcement is active on all connections."""
    with db_manager.connection() as conn:
        cursor = conn.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1


@pytest.mark.unit
def test_wal_mode_enabled(db_manager):
    """Verify Write-Ahead Logging (WAL) is enabled for file databases."""
    with db_manager.connection() as conn:
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0].lower()
        assert mode == "wal"


@pytest.mark.unit
def test_migration_failure_rollback(tmp_path):
    """Verify that a failing migration rolls back cleanly without committing."""
    # Create a custom migrations dir with valid V001 and failing V002
    mig_dir = tmp_path / "custom_migrations"
    mig_dir.mkdir()

    v1_src = Path(__file__).resolve().parent.parent.parent / "src" / "ticnet" / "persistence" / "migrations" / "V001__initial_schema.sql"
    (mig_dir / "V001__initial_schema.sql").write_text(v1_src.read_text(encoding="utf-8"), encoding="utf-8")
    
    # Intentionally broken SQL in V002
    (mig_dir / "V002__broken.sql").write_text("CREATE TABLE broken (id INT; INVALID SYNTAX HERE);", encoding="utf-8")

    db_file = tmp_path / "fail_test.sqlite3"
    mgr = DatabaseManager(db_path=db_file, migrations_dir=mig_dir)

    with pytest.raises(MigrationError) as exc_info:
        mgr.run_migrations()

    assert "V002" in str(exc_info.value)

    # Assert V001 was committed, but V002 was not
    applied = mgr.get_applied_migrations()
    assert len(applied) == 1
    assert applied[0]["version"] == "V001"


# ============================================================================
# 2. RunRepository Tests
# ============================================================================

@pytest.mark.unit
def test_run_crud_operations(db_manager):
    """Verify creating, retrieving, updating, and listing runs."""
    repo = RunRepository(db_manager)
    run_id = new_id()
    start_time = now_utc_iso()

    run = TestRun(
        run_id=run_id,
        start_time_utc=start_time,
        status=RunStatus.RUNNING,
        app_version="0.1.0",
        config_version="1.0.0",
        interface_name="Wi-Fi",
        gateway_ip="192.168.1.1",
    )
    repo.create_run(run)

    retrieved = repo.get_run(run_id)
    assert retrieved is not None
    assert retrieved.run_id == run_id
    assert retrieved.status == RunStatus.RUNNING
    assert retrieved.interface_name == "Wi-Fi"
    assert retrieved.gateway_ip == "192.168.1.1"
    assert retrieved.end_time_utc is None

    # Update run
    end_time = now_utc_iso()
    retrieved.status = RunStatus.COMPLETED
    retrieved.end_time_utc = end_time
    repo.update_run(retrieved)

    updated = repo.get_run(run_id)
    assert updated.status == RunStatus.COMPLETED
    assert updated.end_time_utc == end_time


@pytest.mark.unit
def test_list_runs_order(db_manager):
    """Verify runs are listed in reverse chronological order."""
    repo = RunRepository(db_manager)

    times = [
        "2026-10-21T10:00:00Z",
        "2026-10-21T11:00:00Z",
        "2026-10-21T12:00:00Z",
    ]
    for idx, t in enumerate(times):
        repo.create_run(
            TestRun(
                run_id=f"run-{idx}",
                start_time_utc=t,
                status=RunStatus.COMPLETED,
                app_version="0.1.0",
                config_version="1.0.0",
            )
        )

    runs = repo.list_runs(limit=10)
    assert len(runs) == 3
    assert runs[0].run_id == "run-2"
    assert runs[1].run_id == "run-1"
    assert runs[2].run_id == "run-0"


@pytest.mark.unit
def test_stage_crud_operations(db_manager):
    """Verify creating, retrieving, and ordering stages."""
    repo = RunRepository(db_manager)
    run_id = new_id()
    repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    s1 = TestStage(
        stage_id=new_id(),
        run_id=run_id,
        stage_name="Gateway Probe",
        stage_order=1,
        start_time_utc=now_utc_iso(),
        status=StageStatus.SUCCESS,
    )
    s2 = TestStage(
        stage_id=new_id(),
        run_id=run_id,
        stage_name="External Probe",
        stage_order=2,
        start_time_utc=now_utc_iso(),
        status=StageStatus.RUNNING,
    )

    repo.create_stage(s2)
    repo.create_stage(s1)

    stages = repo.get_stages_for_run(run_id)
    assert len(stages) == 2
    assert stages[0].stage_name == "Gateway Probe"
    assert stages[1].stage_name == "External Probe"


@pytest.mark.unit
def test_foreign_key_violation_for_stage(db_manager):
    """Verify that creating a stage with a non-existent run_id raises IntegrityError."""
    repo = RunRepository(db_manager)
    bad_stage = TestStage(
        stage_id=new_id(),
        run_id="non-existent-run",
        stage_name="Ghost Stage",
        stage_order=1,
        start_time_utc=now_utc_iso(),
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_stage(bad_stage)


# ============================================================================
# 3. ObservationRepository Tests & Data Correctness (Missing != 0)
# ============================================================================

@pytest.mark.unit
def test_target_crud_operations(db_manager):
    """Verify target persistence and listing."""
    repo = ObservationRepository(db_manager)
    target = Target(
        target_id="gw-1",
        target_type=TargetType.GATEWAY,
        ip_address="192.168.1.1",
        provider_label="Local Router",
    )
    repo.create_target(target)

    retrieved = repo.get_target("gw-1")
    assert retrieved is not None
    assert retrieved.target_type == TargetType.GATEWAY
    assert retrieved.ip_address == "192.168.1.1"

    targets = repo.list_targets()
    assert len(targets) >= 1


@pytest.mark.unit
def test_repeated_target_upsert_preserves_observation_foreign_key_integrity(db_manager):
    """Verify that re-registering an existing target preserves child observations and summaries.
    
    Ensures that UPSERT does not trigger ON DELETE SET NULL on observations or summaries.
    """
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)
    sum_repo = SummaryRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="1.0.0",
            config_version="1.0.0",
        )
    )

    target_id = "target-upsert-check"
    initial_target = Target(
        target_id=target_id,
        target_type=TargetType.GATEWAY,
        ip_address="192.168.1.1",
        provider_label="Initial Router",
        is_active=True,
    )
    obs_repo.create_target(initial_target)

    # Attach observation
    obs_id = new_id()
    obs_repo.create_observation(
        Observation(
            observation_id=obs_id,
            run_id=run_id,
            target_id=target_id,
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=4.2,
            metric_name="rtt",
            value_numeric=4.2,
            unit="ms",
            status=MeasurementStatus.SUCCESS,
        )
    )

    # Attach summary
    sum_id = new_id()
    sum_repo.create_summary(
        Summary(
            summary_id=sum_id,
            run_id=run_id,
            target_id=target_id,
            metric_name="rtt",
            statistic_name="median",
            value_numeric=4.2,
            unit="ms",
            sample_count_total=5,
            sample_count_valid=5,
            calculation_method="median_p50",
            calculation_version="1.0.0",
        )
    )

    # Pre-condition check
    obs_before = obs_repo.get_observation(obs_id)
    sum_before = sum_repo.get_summary(sum_id)
    assert obs_before.target_id == target_id
    assert sum_before.target_id == target_id

    # Re-insert target with updated label via create_target (UPSERT)
    updated_target = Target(
        target_id=target_id,
        target_type=TargetType.GATEWAY,
        ip_address="192.168.1.1",
        provider_label="Updated Router Label",
        is_active=True,
    )
    obs_repo.create_target(updated_target)

    # Verify target was updated in place
    refreshed_target = obs_repo.get_target(target_id)
    assert refreshed_target is not None
    assert refreshed_target.provider_label == "Updated Router Label"

    # CRITICAL INVARIANT: observations and summaries must retain target_id (NOT set to NULL)
    obs_after = obs_repo.get_observation(obs_id)
    sum_after = sum_repo.get_summary(sum_id)
    assert obs_after is not None
    assert obs_after.target_id == target_id, "Observation target_id was wiped by target re-insert!"
    assert sum_after is not None
    assert sum_after.target_id == target_id, "Summary target_id was wiped by target re-insert!"

    # Verify foreign key check passes with 0 violations
    with db_manager.connection() as conn:
        fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
        assert len(fk_violations) == 0


@pytest.mark.unit
def test_observation_persistence_success_and_null_semantics(db_manager):
    """Verify that successful measurements preserve values and timed-out probes store NULL."""
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    target_id = "target-cloudflare"
    obs_repo.create_target(
        Target(target_id=target_id, target_type=TargetType.EXTERNAL_IP, ip_address="1.1.1.1")
    )

    # 1. Successful probe with positive latency
    obs_success = Observation(
        observation_id=new_id(),
        run_id=run_id,
        target_id=target_id,
        sample_index=1,
        timestamp_utc=now_utc_iso(),
        duration_ms=12.4,
        metric_name="rtt",
        unit="ms",
        status=MeasurementStatus.SUCCESS,
        value_numeric=12.4,
    )
    obs_repo.create_observation(obs_success)

    # 2. Genuine zero latency (e.g. localhost or high-precision probe returning 0.0)
    obs_zero = Observation(
        observation_id=new_id(),
        run_id=run_id,
        target_id=target_id,
        sample_index=2,
        timestamp_utc=now_utc_iso(),
        duration_ms=0.0,
        metric_name="rtt",
        unit="ms",
        status=MeasurementStatus.SUCCESS,
        value_numeric=0.0,
    )
    obs_repo.create_observation(obs_zero)

    # 3. Timed out probe (MUST BE None, NEVER 0.0)
    obs_timeout = Observation(
        observation_id=new_id(),
        run_id=run_id,
        target_id=target_id,
        sample_index=3,
        timestamp_utc=now_utc_iso(),
        duration_ms=1000.0,
        metric_name="rtt",
        unit="ms",
        status=MeasurementStatus.TIMEOUT,
        value_numeric=None,
        error_message="Request timed out after 1000ms",
    )
    obs_repo.create_observation(obs_timeout)

    observations = obs_repo.get_observations_for_run(run_id)
    assert len(observations) == 3

    assert observations[0].sample_index == 1
    assert observations[0].status == MeasurementStatus.SUCCESS
    assert observations[0].value_numeric == 12.4

    assert observations[1].sample_index == 2
    assert observations[1].status == MeasurementStatus.SUCCESS
    assert observations[1].value_numeric == 0.0  # Genuine zero is preserved

    assert observations[2].sample_index == 3
    assert observations[2].status == MeasurementStatus.TIMEOUT
    assert observations[2].value_numeric is None  # Missing remains None, not 0.0


@pytest.mark.unit
def test_observation_entity_invariant_rejects_numeric_on_timeout():
    """Verify that Observation dataclass raises ValueError if value_numeric is set when status != SUCCESS."""
    with pytest.raises(ValueError) as exc_info:
        Observation(
            observation_id=new_id(),
            run_id="run-1",
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=1000.0,
            metric_name="rtt",
            unit="ms",
            status=MeasurementStatus.TIMEOUT,
            value_numeric=0.0,  # ILLEGAL: Coercing timeout to 0.0
        )
    assert "value_numeric must be None" in str(exc_info.value)


@pytest.mark.unit
def test_database_check_constraint_rejects_numeric_on_timeout(db_manager):
    """Verify that SQLite CHECK constraint prevents storing value_numeric on non-success."""
    run_repo = RunRepository(db_manager)
    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    sql = """
        INSERT INTO observations (
            observation_id, run_id, sample_index, timestamp_utc, duration_ms,
            metric_name, value_numeric, unit, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    with pytest.raises(sqlite3.IntegrityError):
        with db_manager.transaction() as conn:
            conn.execute(
                sql,
                (
                    new_id(),
                    run_id,
                    1,
                    now_utc_iso(),
                    1000.0,
                    "rtt",
                    0.0,  # Violates CHECK (status = 'SUCCESS' OR value_numeric IS NULL)
                    "ms",
                    "TIMEOUT",
                ),
            )


# ============================================================================
# 4. SummaryRepository Tests
# ============================================================================

@pytest.mark.unit
def test_summary_crud_and_null_when_insufficient_samples(db_manager):
    """Verify statistical summary persistence and null value when valid sample count is 0."""
    run_repo = RunRepository(db_manager)
    sum_repo = SummaryRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    # 1. Valid summary with samples
    s1 = Summary(
        summary_id=new_id(),
        run_id=run_id,
        metric_name="rtt",
        statistic_name="median",
        unit="ms",
        sample_count_total=10,
        sample_count_valid=10,
        calculation_method="median",
        calculation_version="1.0",
        value_numeric=15.2,
    )
    sum_repo.create_summary(s1)

    # 2. Summary with 0 valid samples (All probes timed out)
    s2 = Summary(
        summary_id=new_id(),
        run_id=run_id,
        metric_name="rtt",
        statistic_name="p95",
        unit="ms",
        sample_count_total=10,
        sample_count_valid=0,
        calculation_method="p95_nearest_rank",
        calculation_version="1.0",
        value_numeric=None,  # MUST BE None when valid samples == 0
    )
    sum_repo.create_summary(s2)

    summaries = sum_repo.get_summaries_for_run(run_id)
    assert len(summaries) == 2

    median_sum = [s for s in summaries if s.statistic_name == "median"][0]
    assert median_sum.value_numeric == 15.2
    assert median_sum.sample_count_valid == 10

    p95_sum = [s for s in summaries if s.statistic_name == "p95"][0]
    assert p95_sum.value_numeric is None
    assert p95_sum.sample_count_valid == 0


@pytest.mark.unit
def test_summary_check_constraint_rejects_numeric_when_no_valid_samples(db_manager):
    """Verify SQLite CHECK constraint rejects value_numeric when sample_count_valid is 0."""
    run_repo = RunRepository(db_manager)
    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    sql = """
        INSERT INTO summaries (
            summary_id, run_id, metric_name, statistic_name, value_numeric,
            unit, sample_count_total, sample_count_valid, calculation_method, calculation_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    with pytest.raises(sqlite3.IntegrityError):
        with db_manager.transaction() as conn:
            conn.execute(
                sql,
                (
                    new_id(),
                    run_id,
                    "rtt",
                    "median",
                    10.5,  # ILLEGAL when sample_count_valid = 0
                    "ms",
                    10,
                    0,
                    "median",
                    "1.0",
                ),
            )


# ============================================================================
# 5. FindingRepository and Evidence Linking Tests
# ============================================================================

@pytest.mark.unit
def test_finding_and_evidence_linking(db_manager):
    """Verify creating findings and linking observation/summary evidence."""
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)
    sum_repo = SummaryRepository(db_manager)
    find_repo = FindingRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    # Create supporting observation
    obs_id = new_id()
    obs_repo.create_observation(
        Observation(
            observation_id=obs_id,
            run_id=run_id,
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=1000.0,
            metric_name="rtt",
            unit="ms",
            status=MeasurementStatus.TIMEOUT,
        )
    )

    # Create finding
    finding_id = new_id()
    finding = Finding(
        finding_id=finding_id,
        run_id=run_id,
        category=FindingCategory.LOCAL_NETWORK,
        priority=FindingPriority.CRITICAL,
        rule_id="R-GW-01",
        rule_version="1.0.0",
        status=FindingStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.STRONG,
        plain_summary="Gateway is unreachable.",
        limitations="ICMP may be blocked by router configuration.",
        recommended_action="Check router Ethernet cable.",
    )
    find_repo.create_finding(finding)

    # Link evidence
    ev = FindingEvidence(
        evidence_id=new_id(),
        finding_id=finding_id,
        relationship_type=EvidenceRelationship.SUPPORTING,
        observation_id=obs_id,
    )
    find_repo.link_evidence(ev)

    findings = find_repo.get_findings_for_run(run_id)
    assert len(findings) == 1
    assert findings[0].rule_id == "R-GW-01"

    evidence_list = find_repo.get_evidence_for_finding(finding_id)
    assert len(evidence_list) == 1
    assert evidence_list[0].observation_id == obs_id
    assert evidence_list[0].relationship_type == EvidenceRelationship.SUPPORTING


# ============================================================================
# 6. LabTrialRepository Tests (Ground Truth Segregation)
# ============================================================================

@pytest.mark.unit
def test_lab_trial_persistence(db_manager):
    """Verify lab trial ground truth persistence."""
    run_repo = RunRepository(db_manager)
    lab_repo = LabTrialRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            run_mode=RunMode.LAB,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    trial = LabTrial(
        trial_id=new_id(),
        run_id=run_id,
        scenario_id=LabScenario.SCENARIO_C_LOSS,
        ground_truth_label="15% NetEm Induced Packet Loss",
        injection_mechanism="tc qdisc add dev veth0 root netem loss 15%",
        injection_parameters='{"loss_pct": 15}',
        verification_status=TrialVerificationStatus.MATCH,
        cleanup_status=TrialCleanupStatus.CLEANED_VERIFIED,
        operator_notes="NetEm qdisc successfully applied and cleaned.",
    )
    lab_repo.create_trial(trial)

    retrieved = lab_repo.get_trial_for_run(run_id)
    assert retrieved is not None
    assert retrieved.scenario_id == LabScenario.SCENARIO_C_LOSS
    assert retrieved.verification_status == TrialVerificationStatus.MATCH


# ============================================================================
# 7. Cascades and Integrity Verification
# ============================================================================

@pytest.mark.unit
def test_cascade_delete_run_cleans_dependent_records(db_manager):
    """Verify deleting a test_run cascades to stages, observations, and findings."""
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)
    find_repo = FindingRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    obs_repo.create_observation(
        Observation(
            observation_id=new_id(),
            run_id=run_id,
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=10.0,
            metric_name="rtt",
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            value_numeric=10.0,
        )
    )

    find_repo.create_finding(
        Finding(
            finding_id=new_id(),
            run_id=run_id,
            category=FindingCategory.LOCAL_NETWORK,
            priority=FindingPriority.INFO,
            rule_id="R-TEST",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.STRONG,
            plain_summary="Test summary",
            limitations="None",
            recommended_action="None",
        )
    )

    # Delete the run
    deleted = run_repo.delete_run(run_id)
    assert deleted is True

    # Assert cascading deletion occurred
    assert obs_repo.get_observations_for_run(run_id) == []
    assert find_repo.get_findings_for_run(run_id) == []


@pytest.mark.unit
def test_target_deletion_sets_null_without_erasing_observations(db_manager):
    """Verify deleting a target sets target_id to NULL on observations rather than deleting them."""
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    target_id = "temp-target"
    obs_repo.create_target(
        Target(target_id=target_id, target_type=TargetType.EXTERNAL_IP, ip_address="8.8.8.8")
    )

    obs_id = new_id()
    obs_repo.create_observation(
        Observation(
            observation_id=obs_id,
            run_id=run_id,
            target_id=target_id,
            sample_index=1,
            timestamp_utc=now_utc_iso(),
            duration_ms=20.0,
            metric_name="rtt",
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            value_numeric=20.0,
        )
    )

    # Delete the target directly from SQL
    with db_manager.transaction() as conn:
        conn.execute("DELETE FROM targets WHERE target_id = ?;", (target_id,))

    # Observation must still exist with target_id = None
    obs = obs_repo.get_observation(obs_id)
    assert obs is not None
    assert obs.target_id is None
    assert obs.value_numeric == 20.0


# ============================================================================
# 8. Persistence Across Reopening & Multi-Threading Concurrency
# ============================================================================

@pytest.mark.unit
def test_records_survive_reopening_database(db_path):
    """Verify that records persist across closing and reopening the SQLite database file."""
    # Write using first manager instance
    mgr1 = DatabaseManager(db_path=db_path)
    mgr1.run_migrations()
    repo1 = RunRepository(mgr1)
    
    run_id = new_id()
    repo1.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.COMPLETED,
            app_version="0.1.0",
            config_version="1.0.0",
            interface_name="Ethernet",
        )
    )
    del repo1
    del mgr1

    # Reopen using fresh second manager instance
    mgr2 = DatabaseManager(db_path=db_path)
    repo2 = RunRepository(mgr2)
    retrieved = repo2.get_run(run_id)

    assert retrieved is not None
    assert retrieved.run_id == run_id
    assert retrieved.interface_name == "Ethernet"


@pytest.mark.unit
def test_multi_threaded_concurrent_writes(db_manager):
    """Verify thread-safe concurrent writes using DatabaseManager in WAL mode."""
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    def write_worker(worker_id: int):
        for i in range(10):
            obs_repo.create_observation(
                Observation(
                    observation_id=new_id(),
                    run_id=run_id,
                    sample_index=worker_id * 100 + i + 1,
                    timestamp_utc=now_utc_iso(),
                    duration_ms=float(i * 5),
                    metric_name="rtt",
                    unit="ms",
                    status=MeasurementStatus.SUCCESS,
                    value_numeric=float(i * 5),
                )
            )

    # 4 concurrent threads writing 10 observations each
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(write_worker, idx) for idx in range(4)]
        for f in futures:
            f.result()

    observations = obs_repo.get_observations_for_run(run_id)
    assert len(observations) == 40


@pytest.mark.unit
def test_batch_operations_and_edge_cases(db_manager):
    """Verify batch insert operations and error handling on updates."""
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)
    sum_repo = SummaryRepository(db_manager)
    find_repo = FindingRepository(db_manager)
    lab_repo = LabTrialRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    stage_id = new_id()
    run_repo.create_stage(
        TestStage(
            stage_id=stage_id,
            run_id=run_id,
            stage_name="Batch Stage",
            stage_order=1,
            start_time_utc=now_utc_iso(),
        )
    )

    # 1. Batch observations
    batch_obs = [
        Observation(
            observation_id=new_id(),
            run_id=run_id,
            stage_id=stage_id,
            sample_index=i + 1,
            timestamp_utc=now_utc_iso(),
            duration_ms=float(i * 10),
            metric_name="rtt",
            unit="ms",
            status=MeasurementStatus.SUCCESS,
            value_numeric=float(i * 10),
        )
        for i in range(5)
    ]
    obs_repo.create_observations_batch(batch_obs)
    stage_obs = obs_repo.get_observations_for_stage(stage_id)
    assert len(stage_obs) == 5

    # 2. Batch summaries
    batch_sums = [
        Summary(
            summary_id=new_id(),
            run_id=run_id,
            metric_name=f"metric_{i}",
            statistic_name="mean",
            unit="ms",
            sample_count_total=5,
            sample_count_valid=5,
            calculation_method="mean",
            calculation_version="1.0",
            value_numeric=float(i * 2),
        )
        for i in range(3)
    ]
    sum_repo.create_summaries_batch(batch_sums)
    run_sums = sum_repo.get_summaries_for_run(run_id)
    assert len(run_sums) == 3

    # 3. Batch evidence linking
    finding_id = new_id()
    find_repo.create_finding(
        Finding(
            finding_id=finding_id,
            run_id=run_id,
            category=FindingCategory.EXTERNAL_PATH,
            priority=FindingPriority.WARNING,
            rule_id="R-LOSS-01",
            rule_version="1.0.0",
            status=FindingStatus.SUPPORTED,
            evidence_strength=EvidenceStrength.MODERATE,
            plain_summary="External packet loss detected",
            limitations="Path degradation outside local network",
            recommended_action="Contact ISP",
        )
    )
    batch_ev = [
        FindingEvidence(
            evidence_id=new_id(),
            finding_id=finding_id,
            relationship_type=EvidenceRelationship.SUPPORTING,
            observation_id=batch_obs[0].observation_id,
        ),
        FindingEvidence(
            evidence_id=new_id(),
            finding_id=finding_id,
            relationship_type=EvidenceRelationship.SUPPORTING,
            summary_id=batch_sums[0].summary_id,
        ),
    ]
    find_repo.link_evidence_batch(batch_ev)
    ev_list = find_repo.get_evidence_for_finding(finding_id)
    assert len(ev_list) == 2

    # 4. Lab trials listing
    lab_repo.create_trial(
        LabTrial(
            trial_id=new_id(),
            run_id=run_id,
            scenario_id=LabScenario.SCENARIO_A_GW,
            ground_truth_label="Gateway Down",
            injection_mechanism="ip link set dev veth0 down",
            injection_parameters="{}",
            verification_status=TrialVerificationStatus.MATCH,
            cleanup_status=TrialCleanupStatus.CLEANED_VERIFIED,
        )
    )
    trials = lab_repo.list_trials()
    assert len(trials) == 1

    # 5. Non-existent update raises ValueError
    ghost_run = TestRun(
        run_id="ghost-run-id",
        start_time_utc=now_utc_iso(),
        status=RunStatus.FAILED,
        app_version="0.1.0",
        config_version="1.0.0",
    )
    with pytest.raises(ValueError):
        run_repo.update_run(ghost_run)

    ghost_stage = TestStage(
        stage_id="ghost-stage-id",
        run_id=run_id,
        stage_name="Ghost",
        stage_order=99,
        start_time_utc=now_utc_iso(),
    )
    with pytest.raises(ValueError):
        run_repo.update_stage(ghost_stage)


@pytest.mark.unit
def test_transaction_rollback_preserves_clean_state(db_manager):
    """Verify that a failure within a transaction block completely rolls back all operations."""
    run_repo = RunRepository(db_manager)
    obs_repo = ObservationRepository(db_manager)

    run_id = new_id()
    run_repo.create_run(
        TestRun(
            run_id=run_id,
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="0.1.0",
            config_version="1.0.0",
        )
    )

    try:
        with db_manager.transaction() as conn:
            # 1. Insert a valid observation
            conn.execute(
                """
                INSERT INTO observations (
                    observation_id, run_id, sample_index, timestamp_utc, duration_ms,
                    metric_name, value_numeric, unit, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (new_id(), run_id, 1, now_utc_iso(), 10.0, "rtt", 10.0, "ms", "SUCCESS"),
            )
            # 2. Trigger an intentional failure (violates foreign key)
            conn.execute(
                """
                INSERT INTO observations (
                    observation_id, run_id, sample_index, timestamp_utc, duration_ms,
                    metric_name, value_numeric, unit, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (new_id(), "non-existent-run", 2, now_utc_iso(), 10.0, "rtt", 10.0, "ms", "SUCCESS"),
            )
    except sqlite3.IntegrityError:
        pass

    # The first observation must NOT have been committed
    observations = obs_repo.get_observations_for_run(run_id)
    assert len(observations) == 0
