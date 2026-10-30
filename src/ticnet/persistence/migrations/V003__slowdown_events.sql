-- Migration: V003__slowdown_events.sql
-- Description: Schema for persisting detected network slowdowns, baselines, and recovery events
-- Compliant with TicNet persistence conventions (06_DATA_MODEL.md)

CREATE TABLE IF NOT EXISTS slowdown_events (
    event_id TEXT PRIMARY KEY,
    monitoring_run_id TEXT,
    metric_name TEXT NOT NULL CHECK (metric_name IN ('latency', 'jitter', 'packet_loss', 'download_speed', 'overall')),
    detection_status TEXT NOT NULL CHECK (detection_status IN ('NORMAL', 'SUSPECTED_SLOWDOWN', 'CONFIRMED_SLOWDOWN', 'CONFIRMED_RECOVERY', 'INSUFFICIENT_DATA')),
    baseline_value REAL,
    observed_value REAL,
    deviation_pct REAL,
    threshold_applied TEXT NOT NULL,
    time_window TEXT NOT NULL CHECK (time_window IN ('24H', '7D', '30D')),
    consecutive_breaches INTEGER NOT NULL DEFAULT 0,
    explanation TEXT NOT NULL,
    detected_at_utc TEXT NOT NULL,
    FOREIGN KEY (monitoring_run_id) REFERENCES monitoring_runs(monitoring_run_id) ON DELETE SET NULL
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_slowdown_events_detected_at ON slowdown_events(detected_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_slowdown_events_metric_status ON slowdown_events(metric_name, detection_status);

