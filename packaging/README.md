# TicNet Desktop — Windows Packaging & Distribution Guide

**Phase:** 10 — Native Windows Desktop Application  
**Stage:** 3 — Windows Packaging & Distribution  
**Target Platform:** Windows 10 / Windows 11 (64-bit)  
**Packaging Tool:** PyInstaller 6.22+  
**Distribution Format:** Standalone One-Folder Directory (`dist/TicNet/`)

---

## 1. Overview & Strategy

TicNet is packaged as a native Windows desktop application that runs independently of any installed Python environment.

### Packaging Choice: Standalone Directory (`onedir`) vs Single Executable (`onefile`)
We selected **One-Folder (`onedir`) distribution** (`dist/TicNet/`) for production deployment based on the following measured engineering advantages:
1. **Instantaneous Startup:** Does not unpack hundreds of megabytes of Qt libraries into `%TEMP%` on every launch.
2. **Anti-Virus Compatibility:** Eliminates false-positive detections and execution delays caused by heuristics scanning repeated decompression in temporary folders.
3. **Auditability & Integrity:** Allows direct inspection of bundled DLLs, plugins, and resources (`_internal/`) to verify that no private credentials, development databases, or test fixtures are packaged.
4. **Reliable Plugin Loading:** PySide6 platform plugins (`qwindows.dll`) and QtCharts libraries load directly from predictable local paths without extraction races.

---

## 2. Directory Structure & Runtime Storage Paths

TicNet adheres strictly to Windows application security guidelines:
- **No Program Files Writes:** The executable and bundled assets in `_internal/` are treated as strictly read-only.
- **Per-User AppData:** All dynamic, writable state is stored in the standard per-user local application data directory:
  - Database: `%LOCALAPPDATA%\TicNet\ticnet.sqlite3`
  - Settings Overrides: `%LOCALAPPDATA%\TicNet\targets.toml` and `%LOCALAPPDATA%\TicNet\thresholds.toml`
  - Privacy-Preserving Logs: `%LOCALAPPDATA%\TicNet\logs\ticnet.log`
  - Report Exports: User-selected destinations (defaults to `%USERPROFILE%\Documents\` or `%LOCALAPPDATA%\TicNet\exports\`)

---

## 3. Runtime Asset Inventory

The following non-Python assets are bundled into the distribution (`_internal/`):

| Asset Path in Distribution | Source in Repository | Purpose |
| :--- | :--- | :--- |
| `config/targets.default.toml` | `config/targets.default.toml` | Factory default diagnostic targets (ICMP/DNS/HTTP) |
| `config/thresholds.default.toml` | `config/thresholds.default.toml` | Factory default diagnostic classification thresholds |
| `ticnet/persistence/migrations/V001__initial_schema.sql` | `src/ticnet/persistence/migrations/V001__initial_schema.sql` | Authoritative SQLite schema migration |
| `PySide6/plugins/platforms/qwindows.dll` | PySide6 Windows platform plugin | Enables native Windows Qt GUI rendering |
| `PySide6/Qt6Charts.dll` | PySide6 QtCharts library | Powers native measurement visualization |

### Explicit Exclusions (Verified by Build Audit)
- **No development databases:** No `*.sqlite3` or `*.db` files bundled.
- **No test files:** No `tests/` directory or `test_*.py` files.
- **No test frameworks:** `pytest`, `pytest_cov`, `mock`, `unittest` excluded.
- **No plain-text credentials:** Zero secrets bundled.

---

## 4. Build Instructions

### Prerequisites
- Windows 10/11 x64
- Python 3.12 64-bit
- Virtual environment with dependencies installed:
  ```powershell
  .venv\Scripts\pip install -r requirements.txt
  .venv\Scripts\pip install pyinstaller
  ```

### Executing the Build
Run the automated packaging script from the repository root:
```powershell
.venv\Scripts\python packaging/build_windows.py
```
This script performs:
1. Clean removal of previous `build/` and `dist/TicNet/` directories.
2. Invocation of PyInstaller using `packaging/ticnet.spec`.
3. Distribution verification audit checking binary presence, Qt platform plugins, bundled TOML configurations, migration scripts, and absence of development artifacts.

---

## 5. Development vs Release-Ready Build Distinction

| Dimension | Development Build | Release-Ready Packaged Build |
| :--- | :--- | :--- |
| **Execution Method** | `.venv\Scripts\python -m ticnet.desktop.app` | Double-click `dist\TicNet\TicNet.exe` |
| **Python Runtime** | System/vEnv Python interpreter | Bundled frozen Python 3.12 embedded DLL |
| **Database Path** | `ticnet.sqlite3` in workspace root | `%LOCALAPPDATA%\TicNet\ticnet.sqlite3` |
| **Config Source** | `config/targets.default.toml` in workspace | Embedded in `_internal/config/` |
| **Console Window** | Attached to terminal / PowerShell stdout | `console=False` (native Windows GUI app) |
| **UAC Privilege** | Normal user | Normal user (`uac_admin=False`) |
| **Telemetry / Web** | None | None |

---

## 6. Clean-Environment Verification Protocol

To verify compatibility on a clean Windows machine without Python:
1. Copy the `dist/TicNet/` folder to a target clean Windows 10/11 machine.
2. Double-click `TicNet.exe`.
3. Verify that:
   - The application window opens immediately without missing-DLL errors.
   - First launch creates `%LOCALAPPDATA%\TicNet\ticnet.sqlite3` and applies `V001__initial_schema.sql`.
   - All 7 navigation pages (Overview, Diagnosis, Results, History, Reports, Settings, About) render correctly.
   - Navigating to **Settings** displays default targets and thresholds without error.
   - Performing a diagnosis runs the backend worker and renders findings and measurement charts.
   - Exporting a report generates a valid PDF/JSON/CSV file in the user's selected directory.
