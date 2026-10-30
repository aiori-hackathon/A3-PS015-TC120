"""SQLite database connection manager and migration runner for TicNet.

Provides thread-safe connections with:
- PRAGMA foreign_keys = ON; (enforced referential integrity)
- PRAGMA journal_mode = WAL; (concurrent read/write support)
- PRAGMA busy_timeout = 5000; (prevents database locked errors)
- Atomic migration runner with schema_version tracking.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from ticnet.core.timezone import now_ist_iso
import logging
from pathlib import Path
import sqlite3
import sys
import threading
from typing import Generator, List, Optional, Union

logger = logging.getLogger(__name__)


def _resolve_default_migrations_dir() -> Path:
    """Resolve migrations directory, supporting PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = getattr(sys, "_MEIPASS")
        candidate1 = Path(meipass) / "ticnet" / "persistence" / "migrations"
        if candidate1.is_dir():
            return candidate1
        candidate2 = Path(meipass) / "migrations"
        if candidate2.is_dir():
            return candidate2
    return Path(__file__).resolve().parent / "migrations"


DEFAULT_MIGRATIONS_DIR = _resolve_default_migrations_dir()


class DatabaseError(Exception):
    """Base exception for persistence and database operations."""
    pass


class MigrationError(DatabaseError):
    """Exception raised when a database migration fails."""
    pass


class DatabaseManager:
    """Manages SQLite connections, transactions, and schema migrations.
    
    Ensures safe multi-threaded operation by opening dedicated connections per thread
    with foreign keys enabled and WAL mode active.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = "ticnet.sqlite3",
        busy_timeout_ms: int = 5000,
        migrations_dir: Optional[Path] = None,
    ):
        self.db_path = str(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self.migrations_dir = migrations_dir or _resolve_default_migrations_dir()
        self._local = threading.local()
        self._lock = threading.Lock()

    def get_connection(self) -> sqlite3.Connection:
        """Create and configure a fresh SQLite connection with mandatory PRAGMAs."""
        try:
            conn = sqlite3.connect(
                self.db_path,
                timeout=self.busy_timeout_ms / 1000.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            
            # Enforce foreign keys on every connection
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
            
            # Enable WAL mode for disk-backed databases (not :memory:)
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL;")
                
            return conn
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to connect to SQLite database at {self.db_path}: {e}") from e

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing a safe, configured database connection."""
        conn = self.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager executing operations within an atomic transaction.
        
        Commits upon clean exit; automatically rolls back upon any exception.
        """
        conn = self.get_connection()
        try:
            conn.execute("BEGIN;")
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Transaction rolled back due to error: %s", e)
            raise
        finally:
            conn.close()

    def run_migrations(self) -> List[str]:
        """Apply all pending SQL migrations atomically in version order.
        
        Returns a list of newly applied migration versions.
        """
        applied_now: List[str] = []
        
        with self._lock:
            # Ensure database directory exists if path is specified
            if self.db_path != ":memory:":
                db_file = Path(self.db_path)
                if db_file.parent and not db_file.parent.exists():
                    db_file.parent.mkdir(parents=True, exist_ok=True)

            with self.get_connection() as conn:
                # 1. Ensure schema_version table exists
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version TEXT PRIMARY KEY,
                        description TEXT NOT NULL,
                        applied_at_utc TEXT NOT NULL
                    );
                """)
                conn.commit()

                # 2. Query already applied migrations
                cursor = conn.execute("SELECT version FROM schema_version;")
                applied_versions = {row["version"] for row in cursor.fetchall()}

                # 3. Discover migration files
                if not self.migrations_dir.exists():
                    raise MigrationError(f"Migrations directory does not exist: {self.migrations_dir}")

                migration_files = sorted(self.migrations_dir.glob("V*__*.sql"))
                if not migration_files:
                    logger.warning("No migration files found in %s", self.migrations_dir)
                    return applied_now

                # 4. Execute pending migrations in order
                for migration_file in migration_files:
                    version = migration_file.name.split("__")[0]
                    description = migration_file.name.split("__")[1].replace(".sql", "").replace("_", " ")

                    if version in applied_versions:
                        continue

                    logger.info("Applying migration %s: %s", version, description)
                    try:
                        with open(migration_file, "r", encoding="utf-8") as f:
                            sql_script = f.read()

                        # Execute migration and record version atomically
                        conn.execute("BEGIN;")
                        conn.executescript(sql_script)
                        applied_at = now_ist_iso()
                        conn.execute(
                            "INSERT INTO schema_version (version, description, applied_at_utc) VALUES (?, ?, ?);",
                            (version, description, applied_at),
                        )
                        conn.commit()
                        applied_now.append(version)
                        logger.info("Successfully applied migration %s", version)
                    except Exception as e:
                        conn.rollback()
                        raise MigrationError(
                            f"Migration {version} ({migration_file.name}) failed and was rolled back: {e}"
                        ) from e

        if self.db_path != ":memory:":
            try:
                from ticnet.persistence.migrate_timezone_to_ist import migrate_database_to_ist
                migrate_database_to_ist(self.db_path, create_backup=False)
            except Exception as exc:
                logger.debug("Automatic IST timezone migration check: %s", exc)

        return applied_now

    def get_applied_migrations(self) -> List[dict]:
        """Retrieve list of applied migrations with descriptions and timestamps."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT version, description, applied_at_utc FROM schema_version ORDER BY version ASC;")
            return [dict(row) for row in cursor.fetchall()]
