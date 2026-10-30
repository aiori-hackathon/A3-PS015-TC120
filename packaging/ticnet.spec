# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for TicNet Windows Desktop Application.

Builds a standalone onedir Windows distribution bundling:
- PySide6 Qt6 GUI framework and QtCharts
- Default target and threshold configuration files
- Database schema migration scripts (V001__initial_schema.sql)
- Redacting logger and validation rules

Safe Execution Boundaries:
- Runs as standard user (uac_admin=False).
- Application data and logs are placed in %LOCALAPPDATA%\\TicNet at runtime,
  ensuring no write operations are attempted in Program Files.
"""

from pathlib import Path
import sys

# Workspace root
project_root = Path.cwd().resolve()
src_dir = project_root / "src"

block_cipher = None

added_datas = [
    (str(project_root / "config" / "targets.default.toml"), "config"),
    (str(project_root / "config" / "thresholds.default.toml"), "config"),
    (str(project_root / "src" / "ticnet" / "persistence" / "migrations" / "V001__initial_schema.sql"), "ticnet/persistence/migrations"),
    (str(project_root / "src" / "ticnet" / "persistence" / "migrations" / "V002__monitoring_schedule.sql"), "ticnet/persistence/migrations"),
    (str(project_root / "src" / "ticnet" / "persistence" / "migrations" / "V003__slowdown_events.sql"), "ticnet/persistence/migrations"),
    (str(project_root / "src" / "ticnet" / "persistence" / "migrations" / "V004__incident_notifications.sql"), "ticnet/persistence/migrations"),
    (str(project_root / "src" / "ticnet" / "persistence" / "migrations" / "V005__timezone_migration_to_ist.sql"), "ticnet/persistence/migrations"),
    (str(project_root / "src" / "ticnet" / "persistence" / "migrations" / "V006__simulation_provenance.sql"), "ticnet/persistence/migrations"),
]

hidden_imports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtCharts",
    "tomli_w",
    "tomllib",
    "sqlite3",
    "ipaddress",
    "dataclasses",
    "typing",
    "zoneinfo",
    "tzdata",
    "psutil",
    "speedtest",
    "ticnet.core.timezone",
    "ticnet.persistence.migrate_timezone_to_ist",
]

excluded_modules = [
    "pytest",
    "pytest_cov",
    "mock",
    "_pytest",
    "setuptools",
    "pip",
    "tkinter",
    "matplotlib",
    "numpy",
]

a = Analysis(
    [str(src_dir / "ticnet" / "desktop" / "app.py")],
    pathex=[str(src_dir)],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TicNet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Windowed GUI application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,  # Standard user privileges only
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TicNet",
)
