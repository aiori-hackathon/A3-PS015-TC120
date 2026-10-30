-- Migration: V006__simulation_provenance.sql
-- Description: Schema additions for simulation provenance tracking and isolation
-- Compliant with TicNet persistence conventions (06_DATA_MODEL.md)

-- 1. Simulation Datasets Registry Table
CREATE TABLE IF NOT EXISTS simulation_datasets (
    dataset_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    random_seed INTEGER NOT NULL,
    start_date_ist TEXT NOT NULL,
    end_date_ist TEXT NOT NULL,
    total_runs INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL
);

-- 2. Add provenance tracking columns to test_runs
ALTER TABLE test_runs ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1));
ALTER TABLE test_runs ADD COLUMN simulation_dataset_id TEXT;

-- 3. Add provenance tracking columns to monitoring_runs
ALTER TABLE monitoring_runs ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1));
ALTER TABLE monitoring_runs ADD COLUMN simulation_dataset_id TEXT;

-- 4. Add provenance tracking columns to slowdown_events
ALTER TABLE slowdown_events ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1));
ALTER TABLE slowdown_events ADD COLUMN simulation_dataset_id TEXT;

-- 5. Add provenance tracking columns to network_incidents
ALTER TABLE network_incidents ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1));
ALTER TABLE network_incidents ADD COLUMN simulation_dataset_id TEXT;

-- 6. Indexes for performance and isolation queries
CREATE INDEX IF NOT EXISTS idx_test_runs_simulated ON test_runs(is_simulated, simulation_dataset_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_simulated ON monitoring_runs(is_simulated, simulation_dataset_id);
CREATE INDEX IF NOT EXISTS idx_slowdown_events_simulated ON slowdown_events(is_simulated, simulation_dataset_id);
CREATE INDEX IF NOT EXISTS idx_network_incidents_simulated ON network_incidents(is_simulated, simulation_dataset_id);

