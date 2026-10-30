"""Filesystem path resolution for TicNet desktop application.

Resolves per-user writable application directories (%LOCALAPPDATA% on Windows)
ensuring that packaged executables installed in read-only system locations (e.g.,
Program Files) do not fail when initializing databases, logging, or saving settings.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def is_packaged() -> bool:
    """Return True if running as a PyInstaller packaged frozen executable."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_app_dir() -> Path:
    """Return the application installation or package directory (read-only in production)."""
    if is_packaged():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    # Repository root is two levels up from src/ticnet/desktop/paths.py
    return Path(__file__).resolve().parent.parent.parent.parent


def get_user_data_dir() -> Path:
    """Return the writable per-user application data directory.

    On Windows: %LOCALAPPDATA%\\TicNet (e.g. C:\\Users\\<user>\\AppData\\Local\\TicNet)
    On Linux/macOS: ~/.local/share/ticnet
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data)
    else:
        base_dir = Path.home() / ".local" / "share"

    data_dir = base_dir / "TicNet"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_default_database_path() -> Path:
    """Return the default SQLite database path.

    In packaged production mode: %LOCALAPPDATA%\\TicNet\\ticnet.sqlite3
    In development mode: repo root / ticnet.sqlite3
    """
    if is_packaged():
        return get_user_data_dir() / "ticnet.sqlite3"
    # In development mode, keep database in project working directory
    return Path.cwd().resolve() / "ticnet.sqlite3"


def get_user_config_dir() -> Path:
    """Return the writable user configuration directory."""
    config_dir = get_user_data_dir() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_user_reports_dir() -> Path:
    """Return the default export directory for diagnostic reports."""
    reports_dir = get_user_data_dir() / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def get_user_logs_dir() -> Path:
    """Return the directory for rotating application log files."""
    logs_dir = get_user_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


# Aliases for consistent naming
get_default_log_dir = get_user_logs_dir
get_default_reports_dir = get_user_reports_dir
