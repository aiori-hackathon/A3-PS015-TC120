-- Migration: V002__monitoring_schedule.sql
-- Description: Schema for scheduled network monitoring and periodic test history
-- Compliant with TicNet persistence conventions (06_DATA_MODEL.md)

-- 1. Monitoring Schedule Configuration Table
CREATE TABLE IF NOT EXISTS monitoring_schedule (
    schedule_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    interval_type TEXT NOT NULL CHECK (interval_type IN ('30M', '1H', '2H', 'DAILY')),
    daily_time_utc TEXT, -- HH:MM formatted UTC time, e.g., '09:00'
    status TEXT NOT NULL CHECK (status IN ('ENABLED', 'PAUSED', 'NOT_CONFIGURED')),
    next_run_due_utc TEXT,
    last_run_at_utc TEXT,
    last_run_id TEXT,
    updated_at_utc TEXT NOT NULL
);

-- 2. Monitoring Runs History Table
CREATE TABLE IF NOT EXISTS monitoring_runs (
    monitoring_run_id TEXT PRIMARY KEY,
    run_id TEXT,
    schedule_id TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('SCHEDULED', 'MANUAL')),
    scheduled_for_utc TEXT NOT NULL,
    started_at_utc TEXT,
    completed_at_utc TEXT,
    status TEXT NOT NULL CHECK (status IN ('SCHEDULED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'SKIPPED')),
    skip_reason TEXT,
    error_message TEXT,
    download_mbps REAL,
    upload_mbps REAL,
    latency_ms REAL,
    jitter_ms REAL,
    packet_loss_pct REAL,
    FOREIGN KEY (schedule_id) REFERENCES monitoring_schedule(schedule_id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_schedule ON monitoring_runs(schedule_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_scheduled_for ON monitoring_runs(scheduled_for_utc DESC);
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_status ON monitoring_runs(status);
