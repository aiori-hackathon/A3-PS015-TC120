"""Settings service adapter for TicNet desktop presentation layer.

Coordinates configuration loading, schema and security validation, and atomic
TOML persistence without compromising safety ceilings or privacy safeguards.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
import uuid

import tomllib
import tomli_w

from ticnet.configuration.loader import (
    find_config_dir,
    load_app_config,
    load_targets_config,
    load_thresholds_config,
    load_toml_file,
)
from ticnet.configuration.validator import (
    AppConfig,
    ConfigError,
    ConfigNotFoundError,
    ConfigValidationError,
    ConfigValidator,
    TargetsConfig,
    ThresholdsConfig,
)
from ticnet.desktop.paths import get_user_config_dir
from ticnet.utils.logging import get_logger

logger = get_logger("desktop.service.settings")


def _sanitize_for_toml(data: Any) -> Any:
    """Recursively replace None values with empty strings for TOML serialization."""
    if isinstance(data, dict):
        return {k: _sanitize_for_toml(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_for_toml(v) for v in data]
    elif data is None:
        return ""
    return data


class SettingsService:
    """Manages loading, validation, atomic saving, and reset of desktop settings."""

    def __init__(
        self,
        config_dir: Optional[Union[str, Path]] = None,
        initial_config: Optional[AppConfig] = None,
    ) -> None:
        """Initialize SettingsService with configuration directory.

        Args:
            config_dir: Directory where user configuration overrides are stored.
            initial_config: Optional pre-loaded AppConfig instance.
        """
        self.user_config_dir = (
            Path(config_dir).resolve() if config_dir else get_user_config_dir()
        )
        self.user_config_dir.mkdir(parents=True, exist_ok=True)

        self._active_config: AppConfig = initial_config or self.load_settings()

    @property
    def active_config(self) -> AppConfig:
        """Return the current validated in-memory AppConfig."""
        return self._active_config

    @property
    def user_targets_path(self) -> Path:
        """Path to user targets configuration file."""
        return self.user_config_dir / "targets.user.toml"

    @property
    def user_thresholds_path(self) -> Path:
        """Path to user thresholds configuration file."""
        return self.user_config_dir / "thresholds.user.toml"

    def load_settings(self) -> AppConfig:
        """Load settings from user overrides if existing, falling back to defaults.

        Returns:
            Validated AppConfig instance.
        """
        # 1. Load Targets
        if self.user_targets_path.is_file():
            try:
                logger.info("Loading user targets override: %s", self.user_targets_path)
                data = load_toml_file(self.user_targets_path)
                targets = ConfigValidator.validate_targets(data)
            except Exception as exc:
                logger.warning(
                    "User targets config invalid or corrupt (%s); falling back to factory defaults: %s",
                    self.user_targets_path.name,
                    exc,
                )
                targets = load_targets_config()
        else:
            targets = load_targets_config()

        # 2. Load Thresholds
        if self.user_thresholds_path.is_file():
            try:
                logger.info("Loading user thresholds override: %s", self.user_thresholds_path)
                data = load_toml_file(self.user_thresholds_path)
                thresholds = ConfigValidator.validate_thresholds(data)
            except Exception as exc:
                logger.warning(
                    "User thresholds config invalid or corrupt (%s); falling back to factory defaults: %s",
                    self.user_thresholds_path.name,
                    exc,
                )
                thresholds = load_thresholds_config()
        else:
            thresholds = load_thresholds_config()

        self._active_config = AppConfig(targets=targets, thresholds=thresholds)
        return self._active_config

    def save_settings(
        self,
        targets_dict: Dict[str, Any],
        thresholds_dict: Dict[str, Any],
    ) -> AppConfig:
        """Validate and atomically persist user settings overrides.

        Invariants:
        - Validates all values through ConfigValidator before writing to disk.
        - Atomic two-phase write (.tmp -> destination with .bak rollback).
        - Previous configuration remains intact if save fails.
        - Privacy, security ceilings, and anti-bias rules cannot be disabled.

        Args:
            targets_dict: Raw dictionary matching TargetsConfig schema.
            thresholds_dict: Raw dictionary matching ThresholdsConfig schema.

        Returns:
            Validated, active AppConfig instance.

        Raises:
            ConfigValidationError: If parameter values violate safety bounds.
            ConfigError: If disk write or serialization fails.
        """
        # 1. Strict Validation
        clean_targets_dict = _sanitize_for_toml(targets_dict)
        clean_thresholds_dict = _sanitize_for_toml(thresholds_dict)

        validated_targets = ConfigValidator.validate_targets(clean_targets_dict)
        validated_thresholds = ConfigValidator.validate_thresholds(clean_thresholds_dict)

        # 2. Atomic Persistence
        self._atomic_write_toml(self.user_targets_path, clean_targets_dict)
        self._atomic_write_toml(self.user_thresholds_path, clean_thresholds_dict)

        # 3. Update In-Memory Active Configuration
        self._active_config = AppConfig(targets=validated_targets, thresholds=validated_thresholds)
        logger.info("User configuration successfully saved and validated.")
        return self._active_config

    def reset_to_defaults(self) -> AppConfig:
        """Remove user override files and restore factory default configuration."""
        for path in (self.user_targets_path, self.user_thresholds_path):
            if path.exists():
                try:
                    path.unlink()
                    logger.info("Removed user override config file: %s", path)
                except Exception as exc:
                    logger.warning("Failed to delete user config file %s: %s", path, exc)

        factory_config = load_app_config()
        self._active_config = factory_config
        logger.info("Configuration reset to factory defaults.")
        return self._active_config

    @staticmethod
    def _atomic_write_toml(target_path: Path, data: Dict[str, Any]) -> None:
        """Serialize data to TOML and write atomically to target_path with .bak rollback."""
        content = tomli_w.dumps(data)
        parent_dir = target_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        temp_path = parent_dir / f".tmp_{uuid.uuid4().hex}_{target_path.name}"
        backup_path: Optional[Path] = None
        target_existed = target_path.exists()

        try:
            # Write to temp file
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (AttributeError, OSError):
                    pass

            # Backup existing if present
            if target_existed:
                backup_path = parent_dir / f".bak_{uuid.uuid4().hex}_{target_path.name}"
                os.replace(target_path, backup_path)

            # Atomic replace
            os.replace(temp_path, target_path)

            # Clean backup
            if backup_path and backup_path.exists():
                try:
                    backup_path.unlink()
                except Exception:
                    pass

        except Exception as exc:
            # Clean temp
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

            # Restore backup
            if backup_path and backup_path.exists():
                try:
                    os.replace(backup_path, target_path)
                except Exception:
                    pass

            raise ConfigError(f"Failed to atomically persist configuration to '{target_path.name}': {exc}") from exc
