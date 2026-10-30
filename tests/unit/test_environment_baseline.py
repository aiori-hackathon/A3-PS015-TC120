"""Phase 0 Environment and Baseline Verification Tests."""

import sys
import pytest
from pathlib import Path


@pytest.mark.unit
def test_python_version_supported():
    """Verify host Python version meets minimum requirement of Python 3.11+."""
    assert sys.version_info >= (3, 11), f"Python 3.11+ required, got {sys.version}"


@pytest.mark.unit
def test_ticnet_package_importable():
    """Verify ticnet root package is discoverable and importable."""
    import ticnet
    assert hasattr(ticnet, "__version__")
    assert ticnet.__version__ == "0.1.0"


@pytest.mark.unit
def test_default_config_files_exist():
    """Verify default configuration files exist in the config directory."""
    config_dir = Path(__file__).resolve().parent.parent.parent / "config"
    targets_config = config_dir / "targets.default.toml"
    thresholds_config = config_dir / "thresholds.default.toml"

    assert targets_config.exists(), "targets.default.toml must exist"
    assert thresholds_config.exists(), "thresholds.default.toml must exist"
