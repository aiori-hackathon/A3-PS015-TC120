"""Predictable, secure TOML configuration loader for TicNet.

Loads and validates targets and thresholds configurations without relying on process cwd
and without exposing private file paths or credentials in exceptions.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ticnet.configuration.validator import (
    AppConfig,
    ConfigError,
    ConfigNotFoundError,
    ConfigSyntaxError,
    ConfigValidationError,
    ConfigValidator,
    TargetsConfig,
    ThresholdsConfig,
)


def find_config_dir() -> Path:
    """Resolve the directory containing configuration files predictably.
    
    Order of precedence:
    1. TICNET_CONFIG_DIR environment variable (if set and existing).
    2. Ascending parent directories of this source file containing a 'config/targets.default.toml' file.
    3. 'config' directory under current working directory (if existing).
    4. Fallback to 'config' relative path.
    """
    env_dir = os.environ.get("TICNET_CONFIG_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        if p.is_dir():
            return p

    # Check PyInstaller frozen package bundle
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        frozen_config = Path(getattr(sys, "_MEIPASS")) / "config"
        if frozen_config.is_dir() and (frozen_config / "targets.default.toml").is_file():
            return frozen_config

    # Ascend from __file__
    current = Path(__file__).resolve().parent
    for _ in range(5):
        candidate = current / "config"
        if candidate.is_dir() and (candidate / "targets.default.toml").is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent

    # Try cwd / "config"
    cwd_candidate = Path.cwd().resolve() / "config"
    if cwd_candidate.is_dir():
        return cwd_candidate

    return Path("config")


def load_toml_file(file_path: Union[str, Path]) -> Dict[str, Any]:
    """Read a TOML file securely in read-only binary mode.
    
    Raises:
        ConfigNotFoundError: If the target file does not exist or is not a file.
        ConfigSyntaxError: If the file contains invalid TOML syntax.
    """
    path = Path(file_path)
    if not path.is_file():
        # Avoid leaking full host directory hierarchy in exception
        raise ConfigNotFoundError(f"Configuration file not found: '{path.name}'")

    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as err:
        raise ConfigSyntaxError(f"Malformed TOML syntax in '{path.name}': {err}") from err
    except (OSError, PermissionError) as err:
        raise ConfigError(f"Unable to read configuration file '{path.name}': {err}") from err


def load_targets_config(
    file_path: Optional[Union[str, Path]] = None,
) -> TargetsConfig:
    """Load and validate network targets configuration.
    
    If file_path is None, loads 'targets.default.toml' from find_config_dir().
    
    Returns:
        Validated, immutable TargetsConfig dataclass instance.
    
    Raises:
        ConfigNotFoundError: If configuration file is missing.
        ConfigSyntaxError: If TOML syntax is malformed.
        ConfigValidationError: If schema, types, bounds, or security ceilings are violated.
    """
    if file_path is None:
        file_path = find_config_dir() / "targets.default.toml"

    data = load_toml_file(file_path)
    return ConfigValidator.validate_targets(data)


def load_thresholds_config(
    file_path: Optional[Union[str, Path]] = None,
) -> ThresholdsConfig:
    """Load and validate diagnostic rule thresholds configuration.
    
    If file_path is None, loads 'thresholds.default.toml' from find_config_dir().
    
    Returns:
        Validated, immutable ThresholdsConfig dataclass instance.
    
    Raises:
        ConfigNotFoundError: If configuration file is missing.
        ConfigSyntaxError: If TOML syntax is malformed.
        ConfigValidationError: If schema, types, bounds, or security ceilings are violated.
    """
    if file_path is None:
        file_path = find_config_dir() / "thresholds.default.toml"

    data = load_toml_file(file_path)
    return ConfigValidator.validate_thresholds(data)


def load_app_config(
    config_dir: Optional[Union[str, Path]] = None,
) -> AppConfig:
    """Load and validate both targets and thresholds configurations.
    
    Returns:
        Combined AppConfig dataclass instance containing targets and thresholds.
    """
    base_dir = Path(config_dir).resolve() if config_dir else find_config_dir()
    targets = load_targets_config(base_dir / "targets.default.toml")
    thresholds = load_thresholds_config(base_dir / "thresholds.default.toml")
    return AppConfig(targets=targets, thresholds=thresholds)
