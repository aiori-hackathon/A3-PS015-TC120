-- Migration: V004__incident_notifications.sql
-- Description: Schema for configurable alert preferences and network incident lifecycle tracking
-- Compliant with TicNet persistence conventions (06_DATA_MODEL.md)

-- 1. Notification Preferences Table
CREATE TABLE IF NOT EXISTS notification_preferences (
    preference_id TEXT PRIMARY KEY DEFAULT 'default',
    desktop_enabled INTEGER NOT NULL DEFAULT 1 CHECK (desktop_enabled IN (0, 1)),
    sound_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sound_enabled IN (0, 1)),
    notify_suspected INTEGER NOT NULL DEFAULT 0 CHECK (notify_suspected IN (0, 1)),
    notify_confirmed INTEGER NOT NULL DEFAULT 1 CHECK (notify_confirmed IN (0, 1)),
    notify_recovery INTEGER NOT NULL DEFAULT 1 CHECK (notify_recovery IN (0, 1)),
    cooldown_minutes INTEGER NOT NULL DEFAULT 30 CHECK (cooldown_minutes >= 0),
    updated_at_utc TEXT NOT NULL
);

-- 2. Network Incidents History Table
CREATE TABLE IF NOT EXISTS network_incidents (
    incident_id TEXT PRIMARY KEY,
    monitoring_run_id TEXT,
    metric_name TEXT NOT NULL CHECK (metric_name IN ('latency', 'jitter', 'packet_loss', 'download_speed', 'overall')),
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    status TEXT NOT NULL CHECK (status IN ('SUSPECTED', 'CONFIRMED', 'RESOLVED')),
    baseline_value REAL,
    observed_value REAL,
    deviation_pct REAL,
    headline TEXT NOT NULL,
    description TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    resolved_at_utc TEXT,
    last_notified_at_utc TEXT,
    notification_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (monitoring_run_id) REFERENCES monitoring_runs(monitoring_run_id) ON DELETE SET NULL
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_incidents_status_metric ON network_incidents(status, metric_name);
CREATE INDEX IF NOT EXISTS idx_incidents_started_at ON network_incidents(started_at_utc DESC);

