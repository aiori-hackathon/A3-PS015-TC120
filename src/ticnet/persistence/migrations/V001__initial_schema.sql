-- Migration: V001__initial_schema.sql
-- Description: Baseline schema for TicNet persistence layer
-- Compliant with 06_DATA_MODEL.md

-- 1. Schema Version Table
CREATE TABLE IF NOT EXISTS schema_version (
    version TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

-- 2. Test Runs Table
CREATE TABLE IF NOT EXISTS test_runs (
    run_id TEXT PRIMARY KEY,
    start_time_utc TEXT NOT NULL,
    end_time_utc TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED')),
    run_mode TEXT NOT NULL DEFAULT 'OPERATIONAL' CHECK (run_mode IN ('OPERATIONAL', 'LAB')),
    app_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    interface_name TEXT,
    interface_type TEXT,
    gateway_ip TEXT,
    overall_error TEXT
);

-- 3. Test Stages Table
CREATE TABLE IF NOT EXISTS test_stages (
    stage_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL CHECK (stage_order >= 1),
    start_time_utc TEXT NOT NULL,
    end_time_utc TEXT,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'TIMEOUT', 'SKIPPED', 'CANCELLED')),
    skip_reason TEXT,
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE
);

-- 4. Targets Table
CREATE TABLE IF NOT EXISTS targets (
    target_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('GATEWAY', 'EXTERNAL_IP', 'DNS_RESOLVER', 'THROUGHPUT_ENDPOINT')),
    hostname TEXT,
    ip_address TEXT,
    port INTEGER CHECK (port IS NULL OR (port >= 1 AND port <= 65535)),
    provider_label TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- 5. Raw Observations Table
CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_id TEXT,
    target_id TEXT,
    sample_index INTEGER NOT NULL CHECK (sample_index >= 1),
    timestamp_utc TEXT NOT NULL,
    duration_ms REAL NOT NULL CHECK (duration_ms >= 0),
    metric_name TEXT NOT NULL,
    value_numeric REAL, -- MUST BE NULL WHEN status != 'SUCCESS'
    unit TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED', 'TIMEOUT', 'BLOCKED', 'UNSUPPORTED')),
    error_message TEXT,
    method_metadata TEXT,
    CHECK (status = 'SUCCESS' OR value_numeric IS NULL),
    FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (stage_id) REFERENCES test_stages(stage_id) ON DELETE SET NULL,
    FOREIGN KEY (target_id) REFERENCES targets(target_id) ON DELETE SET NULL
);

-- 6. Statistical Summaries Table
CREATE TABLE IF NOT EXISTS summaries (
    summary_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    target_id TEXT,
    metric_name TEXT NOT NULL,
    statistic_name TEXT NOT NULL,
    value_numeric REAL, -- Nullable if insufficient samples
    unit TEXT NOT NULL,
    sample_count_total INTEGER NOT NULL CHECK (sample_count_total >= 0),
    sample_count_valid INTEGER NOT NULL CHECK (sample_count_valid >= 0),
    calculation_method TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    CHECK (sample_count_valid <= sample_count_total),
    CHECK (sample_count_valid > 0 OR value_numeric IS NULL),
    FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES targets(target_id) ON DELETE SET NULL
);

-- 7. Diagnostic Findings Table
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('LOCAL_NETWORK', 'DNS_SUBSYSTEM', 'EXTERNAL_PATH', 'BUFFERBLOAT', 'INCONCLUSIVE')),
    priority TEXT NOT NULL CHECK (priority IN ('CRITICAL', 'WARNING', 'INFO')),
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUPPORTED', 'CONTRADICTED', 'INSUFFICIENT_EVIDENCE')),
    evidence_strength TEXT NOT NULL CHECK (evidence_strength IN ('STRONG', 'MODERATE', 'LIMITED', 'INCONCLUSIVE')),
    plain_summary TEXT NOT NULL,
    limitations TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE
);

-- 8. Finding Evidence Junction Table
CREATE TABLE IF NOT EXISTS finding_evidence (
    evidence_id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL,
    observation_id TEXT,
    summary_id TEXT,
    relationship_type TEXT NOT NULL CHECK (relationship_type IN ('SUPPORTING', 'CONFLICTING', 'REQUIRED_MISSING')),
    CHECK (observation_id IS NOT NULL OR summary_id IS NOT NULL),
    FOREIGN KEY (finding_id) REFERENCES findings(finding_id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id) REFERENCES observations(observation_id) ON DELETE CASCADE,
    FOREIGN KEY (summary_id) REFERENCES summaries(summary_id) ON DELETE CASCADE
);

-- 9. Lab Trials Table (Evaluator Only - QUARANTINED FROM DIAGNOSIS ENGINE)
CREATE TABLE IF NOT EXISTS lab_trials (
    trial_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    scenario_id TEXT NOT NULL CHECK (scenario_id IN ('SCENARIO_A_GW', 'SCENARIO_B_DNS', 'SCENARIO_C_LOSS', 'SCENARIO_D_LOAD')),
    ground_truth_label TEXT NOT NULL,
    injection_mechanism TEXT NOT NULL,
    injection_parameters TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('MATCH', 'MISMATCH', 'INCONCLUSIVE', 'INVALID_TRIAL')),
    cleanup_status TEXT NOT NULL CHECK (cleanup_status IN ('CLEANED_VERIFIED', 'CLEANUP_FAILED')),
    operator_notes TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE
);

-- 10. Report Exports Table
CREATE TABLE IF NOT EXISTS report_exports (
    export_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    export_format TEXT NOT NULL CHECK (export_format IN ('JSON', 'CSV', 'MARKDOWN')),
    export_timestamp_utc TEXT NOT NULL,
    file_path_redacted TEXT NOT NULL,
    file_size_bytes INTEGER CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    export_status TEXT NOT NULL CHECK (export_status IN ('SUCCESS', 'FAILED')),
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE
);

-- Indexes for query and join performance
CREATE INDEX IF NOT EXISTS idx_stages_run_order ON test_stages(run_id, stage_order);
CREATE INDEX IF NOT EXISTS idx_obs_run_id ON observations(run_id);
CREATE INDEX IF NOT EXISTS idx_obs_stage_id ON observations(stage_id);
CREATE INDEX IF NOT EXISTS idx_obs_target_id ON observations(target_id);
CREATE INDEX IF NOT EXISTS idx_sum_run_id ON summaries(run_id);
CREATE INDEX IF NOT EXISTS idx_find_run_id ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_finding_id ON finding_evidence(finding_id);
CREATE INDEX IF NOT EXISTS idx_exports_run_id ON report_exports(run_id);
