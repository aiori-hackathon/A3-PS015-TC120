"""Global pytest fixtures and testing hooks for TicNet."""

import os
import sys
import tempfile
from pathlib import Path
import pytest

# Ensure LOCALAPPDATA points to a writable temporary directory in test environments
if "LOCALAPPDATA" not in os.environ or "Temp" not in os.environ.get("LOCALAPPDATA", ""):
    test_appdata = Path(tempfile.gettempdir()) / "ticnet_test_appdata"
    test_appdata.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(test_appdata)

# Add src to sys.path so tests can import ticnet modules cleanly
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def sample_config_path() -> Path:
    """Return path to default configuration directory."""
    return Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(scope="session")
def qapp():
    """Ensure a persistent QApplication instance exists across GUI tests."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app

