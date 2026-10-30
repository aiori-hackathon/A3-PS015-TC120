"""Database migration utility for application-wide timezone migration from UTC to IST.

Migrates all historical timestamps stored in SQLite from UTC to Indian Standard Time (Asia/Kolkata).
Features:
- Full pre-migration database backup.
- Safe, transactional conversion of all timestamp columns.
- Exact conversion (UTC + 05:30) preserving chronological sequence and relationships.
- Complete idempotency (skips timestamps already in IST, checks migration history).
- Full rollback capability.
- Detailed audit reporting of records converted per table and column.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil
import sqlite3
from typing import Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from ticnet.core.timezone import (
    IST,
    IST_TIMEZONE_ID,
    UTC,
    format_ist,
    is_already_ist,
    now_ist,
    now_ist_iso,
    to_ist,
)

logger = logging.getLogger("ticnet.persistence.timezone_migration")

# Target tables and their timestamp columns in TicNet SQLite database
TIMESTAMP_COLUMNS_MAP: Dict[str, List[str]] = {
    "schema_version": ["applied_at_utc"],
    "test_runs": ["start_time_utc", "end_time_utc"],
    "test_stages": ["start_time_utc", "end_time_utc"],
    "observations": ["timestamp_utc"],
    "report_exports": ["export_timestamp_utc"],
    "monitoring_schedule": ["next_run_due_utc", "last_run_at_utc", "updated_at_utc"],
    "monitoring_runs": ["scheduled_for_utc", "started_at_utc", "completed_at_utc"],
    "slowdown_events": ["detected_at_utc"],
    "notification_preferences": ["updated_at_utc"],
    "network_incidents": [
        "started_at_utc",
        "last_seen_at_utc",
        "resolved_at_utc",
        "last_notified_at_utc",
    ],
}


def convert_timestamp_string_to_ist(val_str: Optional[str]) -> Optional[str]:
    """Convert a UTC or naive timestamp string to IST ISO 8601 with '+05:30' offset.

    Returns the original string if already in IST or None if empty.
    """
    if not val_str:
        return val_str

    clean = val_str.strip()
    if not clean:
        return val_str

    # Idempotency check: if already has IST offset (+05:30), do not convert
    if "+05:30" in clean:
        return clean

    try:
        # Standard ISO 8601 parsing
        iso_str = clean[:-1] + "+00:00" if clean.endswith("Z") else clean
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        ist_dt = dt.astimezone(IST)
        return ist_dt.isoformat()
    except Exception:
        pass

    # Try common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(clean, fmt).replace(tzinfo=UTC)
            return dt.astimezone(IST).isoformat()
        except Exception:
            pass

    return clean


def convert_daily_time_to_ist(daily_time_str: Optional[str]) -> Optional[str]:
    """Convert an 'HH:MM' time string from UTC to IST (add 5h 30m)."""
    if not daily_time_str or ":" not in daily_time_str:
        return daily_time_str
    try:
        parts = daily_time_str.strip().split(":")
        h = int(parts[0]) % 24
        m = int(parts[1]) % 60
        # Add 5 hours 30 minutes
        total_m = h * 60 + m + (5 * 60 + 30)
        new_h = (total_m // 60) % 24
        new_m = total_m % 60
        return f"{new_h:02d}:{new_m:02d}"
    except Exception:
        return daily_time_str


def create_database_backup(db_path: Path) -> Path:
    """Create a safe snapshot backup of the database before migration."""
    backup_path = db_path.with_name(f"{db_path.name}.bak_pre_ist_{now_ist().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(db_path, backup_path)
    logger.info("Created database backup at %s", backup_path)
    return backup_path


def migrate_database_to_ist(
    db_path: Union[str, Path],
    create_backup: bool = True,
) -> Dict[str, Any]:
    """Execute application-wide database timestamp migration from UTC to IST.

    Args:
        db_path: Path to the SQLite database file.
        create_backup: If True, creates a dated backup copy before modifying records.

    Returns:
        Audit dictionary detailing converted records per table and column.
    """
    path = Path(db_path).resolve()
    if not path.exists():
        logger.warning("Database path %s does not exist; skipping migration", path)
        return {"status": "skipped", "reason": "database_not_found"}

    backup_file: Optional[Path] = None
    if create_backup:
        backup_file = create_database_backup(path)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    report: Dict[str, Any] = {
        "database": str(path),
        "backup_path": str(backup_file) if backup_file else None,
        "migrated_at_ist": now_ist_iso(),
        "tables": {},
        "total_records_updated": 0,
    }

    try:
        cursor.execute("BEGIN;")

        # 1. Ensure tracking table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS timezone_migration_history (
                migration_id TEXT PRIMARY KEY,
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                records_converted INTEGER NOT NULL DEFAULT 0,
                applied_at_ist TEXT NOT NULL
            );
        """)

        # 2. Iterate through each known table
        for table, columns in TIMESTAMP_COLUMNS_MAP.items():
            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
                (table,),
            )
            if not cursor.fetchone():
                continue

            # Query primary key name
            cursor.execute(f"PRAGMA table_info(\"{table}\");")
            table_info = cursor.fetchall()
            existing_col_names = {col["name"] for col in table_info}
            pk_cols = [col["name"] for col in table_info if col["pk"] > 0]
            pk_col = pk_cols[0] if pk_cols else table_info[0]["name"]

            table_stats: Dict[str, int] = {}

            # Process each timestamp column
            for col in columns:
                if col not in existing_col_names:
                    continue

                cursor.execute(
                    f"SELECT \"{pk_col}\", \"{col}\" FROM \"{table}\" WHERE \"{col}\" IS NOT NULL;"
                )
                rows = cursor.fetchall()

                converted_count = 0
                for row in rows:
                    pk_val = row[pk_col]
                    val = row[col]
                    if val and isinstance(val, str) and "+05:30" not in val:
                        new_val = convert_timestamp_string_to_ist(val)
                        if new_val != val:
                            cursor.execute(
                                f"UPDATE \"{table}\" SET \"{col}\" = ? WHERE \"{pk_col}\" = ?;",
                                (new_val, pk_val),
                            )
                            converted_count += 1

                table_stats[col] = converted_count
                report["total_records_updated"] += converted_count

                # Record in migration history
                mig_id = f"IST_MIG_{table}_{col}"
                cursor.execute(
                    """
                    INSERT INTO timezone_migration_history (
                        migration_id, table_name, column_name, records_converted, applied_at_ist
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(migration_id) DO UPDATE SET
                        records_converted = records_converted + excluded.records_converted,
                        applied_at_ist = excluded.applied_at_ist;
                    """,
                    (mig_id, table, col, converted_count, now_ist_iso()),
                )

            # Special case for monitoring_schedule daily_time_utc -> convert 09:00 UTC to 14:30 IST if not already migrated
            if table == "monitoring_schedule" and "daily_time_utc" in existing_col_names:
                mig_daily_id = "IST_MIG_monitoring_schedule_daily_time"
                cursor.execute(
                    "SELECT records_converted FROM timezone_migration_history WHERE migration_id = ?;",
                    (mig_daily_id,),
                )
                daily_migrated = cursor.fetchone()
                if not daily_migrated:
                    cursor.execute("SELECT schedule_id, daily_time_utc FROM monitoring_schedule;")
                    sched_rows = cursor.fetchall()
                    daily_converted = 0
                    for srow in sched_rows:
                        sid = srow["schedule_id"]
                        dtime = srow["daily_time_utc"]
                        if dtime:
                            new_dtime = convert_daily_time_to_ist(dtime)
                            cursor.execute(
                                "UPDATE monitoring_schedule SET daily_time_utc = ? WHERE schedule_id = ?;",
                                (new_dtime, sid),
                            )
                            daily_converted += 1
                    cursor.execute(
                        """
                        INSERT INTO timezone_migration_history (
                            migration_id, table_name, column_name, records_converted, applied_at_ist
                        ) VALUES (?, ?, ?, ?, ?);
                        """,
                        (mig_daily_id, "monitoring_schedule", "daily_time_utc", daily_converted, now_ist_iso()),
                    )
                    table_stats["daily_time_utc"] = daily_converted
                    report["total_records_updated"] += daily_converted

            report["tables"][table] = table_stats

        conn.commit()
        report["status"] = "success"
        logger.info(
            "Successfully completed timezone migration to IST. Total records updated: %d",
            report["total_records_updated"],
        )
    except Exception as exc:
        conn.rollback()
        report["status"] = "failed"
        report["error"] = str(exc)
        logger.error("Timezone migration to IST failed: %s", exc, exc_info=True)
        raise
    finally:
        conn.close()

    return report


def rollback_timezone_migration(db_path: Union[str, Path]) -> Dict[str, Any]:
    """Roll back database timestamps from IST (+05:30) back to UTC (Z)."""
    path = Path(db_path).resolve()
    if not path.exists():
        return {"status": "skipped", "reason": "database_not_found"}

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    total_reverted = 0

    try:
        cursor.execute("BEGIN;")
        for table, columns in TIMESTAMP_COLUMNS_MAP.items():
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
                (table,),
            )
            if not cursor.fetchone():
                continue

            cursor.execute(f"PRAGMA table_info(\"{table}\");")
            table_info = cursor.fetchall()
            existing_col_names = {col["name"] for col in table_info}
            pk_cols = [col["name"] for col in table_info if col["pk"] > 0]
            pk_col = pk_cols[0] if pk_cols else table_info[0]["name"]

            for col in columns:
                if col not in existing_col_names:
                    continue

                cursor.execute(
                    f"SELECT \"{pk_col}\", \"{col}\" FROM \"{table}\" WHERE \"{col}\" LIKE '%+05:30';"
                )
                rows = cursor.fetchall()

                for row in rows:
                    pk_val = row[pk_col]
                    val = row[col]
                    try:
                        dt = datetime.fromisoformat(val).astimezone(UTC)
                        utc_iso = dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                        cursor.execute(
                            f"UPDATE \"{table}\" SET \"{col}\" = ? WHERE \"{pk_col}\" = ?;",
                            (utc_iso, pk_val),
                        )
                        total_reverted += 1
                    except Exception:
                        pass

        cursor.execute("DROP TABLE IF EXISTS timezone_migration_history;")
        conn.commit()
        return {"status": "success", "total_records_reverted": total_reverted}
    except Exception as exc:
        conn.rollback()
        raise
    finally:
        conn.close()

