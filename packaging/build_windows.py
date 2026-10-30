"""Automated Windows Packaging and Verification Script for TicNet.

Executes PyInstaller build using packaging/ticnet.spec, verifies distribution integrity,
checks for essential Qt plugins and runtime data files, and confirms that development
artifacts (databases, secrets, tests) are excluded.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_build() -> int:
    """Build the standalone Windows executable using PyInstaller."""
    project_root = Path(__file__).resolve().parent.parent
    spec_file = project_root / "packaging" / "ticnet.spec"
    dist_dir = project_root / "dist" / "TicNet"
    build_dir = project_root / "build"

    print("=" * 70)
    print("TicNet Phase 10 — Windows Distribution Build (PyInstaller)")
    print("=" * 70)
    print(f"Project Root : {project_root}")
    print(f"Spec File    : {spec_file}")
    print(f"Output Dist  : {dist_dir}")

    # Clean previous build artifacts
    if dist_dir.exists():
        print(f"Cleaning previous dist directory: {dist_dir}")
        shutil.rmtree(dist_dir, ignore_errors=True)
    if build_dir.exists():
        print(f"Cleaning previous build cache: {build_dir}")
        shutil.rmtree(build_dir, ignore_errors=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec_file),
    ]

    print("\nExecuting PyInstaller...")
    res = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True)
    if res.returncode != 0:
        print("\nERROR: PyInstaller build failed!")
        print("STDOUT:\n", res.stdout[-2000:] if len(res.stdout) > 2000 else res.stdout)
        print("STDERR:\n", res.stderr[-2000:] if len(res.stderr) > 2000 else res.stderr)
        return res.returncode

    print("\nPyInstaller build completed successfully.")
    print("\nRunning distribution verification audit...")
    return verify_distribution(dist_dir)


def verify_distribution(dist_dir: Path) -> int:
    """Verify integrity, security, and asset presence of the built package."""
    checks = []

    # 1. Main executable exists
    exe_file = dist_dir / "TicNet.exe"
    exe_ok = exe_file.is_file() and exe_file.stat().st_size > 0
    checks.append(("TicNet.exe binary exists", exe_ok, f"Size: {exe_file.stat().st_size if exe_ok else 0} bytes"))

    # In PyInstaller 6+, bundled files and libraries are placed inside _internal
    internal_dir = dist_dir / "_internal"
    internal_ok = internal_dir.is_dir()
    checks.append(("_internal runtime directory exists", internal_ok, str(internal_dir)))

    # 2. Config targets and thresholds
    targets_file = internal_dir / "config" / "targets.default.toml"
    targets_ok = targets_file.is_file()
    checks.append(("targets.default.toml bundled", targets_ok, str(targets_file)))

    thresholds_file = internal_dir / "config" / "thresholds.default.toml"
    thresholds_ok = thresholds_file.is_file()
    checks.append(("thresholds.default.toml bundled", thresholds_ok, str(thresholds_file)))

    # 3. Database migrations
    migrations_dir = internal_dir / "ticnet" / "persistence" / "migrations"
    v001_ok = (migrations_dir / "V001__initial_schema.sql").is_file()
    v002_ok = (migrations_dir / "V002__monitoring_schedule.sql").is_file()
    v003_ok = (migrations_dir / "V003__slowdown_events.sql").is_file()
    v004_ok = (migrations_dir / "V004__incident_notifications.sql").is_file()
    v005_ok = (migrations_dir / "V005__timezone_migration_to_ist.sql").is_file()
    v006_ok = (migrations_dir / "V006__simulation_provenance.sql").is_file()
    all_migrations_ok = v001_ok and v002_ok and v003_ok and v004_ok and v005_ok and v006_ok
    checks.append(("SQL migrations (V001-V006) bundled", all_migrations_ok, str(migrations_dir)))

    # 4. Qt Platform Plugin (qwindows.dll)
    qwindows_plugin = internal_dir / "PySide6" / "plugins" / "platforms" / "qwindows.dll"
    if not qwindows_plugin.is_file():
        # Fallback search
        found_qwindows = list(internal_dir.glob("**/qwindows.dll"))
        qwindows_ok = len(found_qwindows) > 0
        qwindows_path = str(found_qwindows[0]) if qwindows_ok else "Not found"
    else:
        qwindows_ok = True
        qwindows_path = str(qwindows_plugin)
    checks.append(("Qt qwindows.dll platform plugin present", qwindows_ok, qwindows_path))

    # 5. QtCharts DLL
    qtcharts_dll = internal_dir / "PySide6" / "Qt6Charts.dll"
    if not qtcharts_dll.is_file():
        found_charts = list(internal_dir.glob("**/Qt6Charts.dll"))
        charts_ok = len(found_charts) > 0
        charts_path = str(found_charts[0]) if charts_ok else "Not found"
    else:
        charts_ok = True
        charts_path = str(qtcharts_dll)
    checks.append(("Qt6Charts.dll library present", charts_ok, charts_path))

    # 6. Absence of dev databases
    bundled_dbs = list(dist_dir.glob("**/*.sqlite3")) + list(dist_dir.glob("**/*.db"))
    no_dev_dbs = len(bundled_dbs) == 0
    checks.append(("No development databases bundled", no_dev_dbs, f"Found {len(bundled_dbs)} files"))

    # 7. Absence of pytest or test files
    bundled_tests = list(dist_dir.glob("**/test_*.py"))
    no_tests = len(bundled_tests) == 0
    checks.append(("No test scripts bundled", no_tests, f"Found {len(bundled_tests)} files"))

    # 8. Python base_library.zip runtime archive (prevents "Failed to start embedded python interpreter!")
    base_lib = internal_dir / "base_library.zip"
    base_lib_ok = base_lib.is_file() and base_lib.stat().st_size > 100000
    checks.append(("base_library.zip interpreter archive present", base_lib_ok, f"Size: {base_lib.stat().st_size if base_lib_ok else 0} bytes"))

    # Print Report
    print("\n" + "=" * 70)
    print("DISTRIBUTION VERIFICATION AUDIT")
    print("=" * 70)
    all_passed = True
    for name, status, detail in checks:
        status_str = "[PASS]" if status else "[FAIL]"
        if not status:
            all_passed = False
        print(f"  {status_str} {name:40s} : {detail}")
    print("=" * 70)

    if all_passed:
        print("\nAll packaging verification checks passed successfully.")
        return 0
    else:
        print("\nPackaging verification FAILED. See details above.")
        return 1


if __name__ == "__main__":
    sys.exit(run_build())
