-- ==============================================================================
-- Migration: V005__timezone_migration_to_ist.sql
-- Description: Establishes timezone migration tracking for Indian Standard Time (IST - Asia/Kolkata).
-- ==============================================================================

-- 1. Create timezone migration audit table
CREATE TABLE IF NOT EXISTS timezone_migration_history (
    migration_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    records_converted INTEGER NOT NULL DEFAULT 0,
    applied_at_ist TEXT NOT NULL
);

-- 2. Create index on migration history
CREATE INDEX IF NOT EXISTS idx_tz_mig_history_table ON timezone_migration_history(table_name, column_name);

