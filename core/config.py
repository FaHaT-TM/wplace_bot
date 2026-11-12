"""Configuration management, runtime path resolution, and setting validation for Place Bot."""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, ClassVar

from .exceptions import ConfigError

logger = logging.getLogger(__name__)

MIN_PIXEL_LIMIT: int = 1
DEFAULT_PIXEL_LIMIT: int = 50
MAX_PIXEL_LIMIT: int = 5000

DEFAULT_LOGS_DIR: str = "logs"
DEFAULT_DEBUG_DIR: str = "debug"


def is_frozen_executable() -> bool:
    """Return ``True`` when running inside a PyInstaller frozen executable."""
    return bool(getattr(sys, "frozen", False))


def get_app_dir() -> Path:
    """Return the directory of the frozen executable, or current working directory otherwise."""
    if is_frozen_executable():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def resolve_resource_path(filename: str) -> str:
    """Resolve a resource/config file path for both source runs and frozen ``app.exe``.

    When frozen via PyInstaller, external files next to ``app.exe`` take precedence so
    users can edit ``colors.json`` or ``config.json`` directly; if absent externally,
    bundled ``sys._MEIPASS`` data is used as a safe fallback.
    """
    candidate = Path(filename)
    if candidate.is_absolute() or not is_frozen_executable():
        return filename

    external_path = get_app_dir() / candidate
    if external_path.exists():
        return str(external_path)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled_path = Path(str(meipass)) / candidate
        if bundled_path.exists():
            return str(bundled_path)

    return str(external_path)


def resolve_writable_path(filename: str) -> str:
    """Resolve a writable runtime file path next to ``app.exe`` when frozen."""
    candidate = Path(filename)
    if candidate.is_absolute() or not is_frozen_executable():
        return filename
    return str(get_app_dir() / candidate)


def validate_pixel_limit(
    value: Any,
    min_limit: int = MIN_PIXEL_LIMIT,
    max_limit: int = MAX_PIXEL_LIMIT,
) -> int:
    """Validate and parse the pixel painting limit.

    Args:
        value: Raw integer or string value entered by the user or loaded from config.
        min_limit: Minimum allowed number of pixels (inclusive).
        max_limit: Maximum allowed number of pixels (inclusive).

    Returns:
        Validated integer pixel limit within ``[min_limit, max_limit]``.

    Raises:
        ConfigError: If ``value`` is empty, not an integer, or outside the allowed range.
    """
    if isinstance(value, bool) or value is None:
        raise ConfigError(
            f"Pixel limit must be an integer between {min_limit} and {max_limit}."
        )

    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ConfigError(
                f"Pixel limit cannot be empty (allowed range: {min_limit}-{max_limit})."
            )
        is_signed_digits = stripped.isdigit() or (
            stripped.startswith("-") and stripped[1:].isdigit()
        )
        if not is_signed_digits:
            raise ConfigError(
                f"Pixel limit must contain only digits ({min_limit}-{max_limit})."
            )
        parsed = int(stripped)
    else:
        raise ConfigError(
            f"Pixel limit must be an integer between {min_limit} and {max_limit}."
        )

    if not (min_limit <= parsed <= max_limit):
        raise ConfigError(
            f"Pixel limit must be between {min_limit} and {max_limit} (got {parsed})."
        )

    return parsed


def clamp_pixel_limit(
    value: Any,
    default: int = DEFAULT_PIXEL_LIMIT,
    min_limit: int = MIN_PIXEL_LIMIT,
    max_limit: int = MAX_PIXEL_LIMIT,
) -> int:
    """Safely normalize a stored pixel limit to the valid range.

    Args:
        value: Raw stored value from user preferences.
        default: Fallback value if ``value`` cannot be parsed as an integer.
        min_limit: Minimum allowed pixel limit.
        max_limit: Maximum allowed pixel limit.

    Returns:
        Integer clamped to ``[min_limit, max_limit]``.
    """
    if isinstance(value, bool) or value is None:
        return default
    if not isinstance(value, (int, str)):
        return default
    try:
        parsed = int(value.strip()) if isinstance(value, str) else int(value)
    except ValueError:
        return default
    return max(min_limit, min(max_limit, parsed))


class Config:
    """Application configuration manager."""

    DEFAULT_CONFIG: ClassVar[dict[str, Any]] = {
        "ui": {
            "theme": "clam",
            "window_size": "800x800",
            "remember_position": True,
            "auto_save_interval": 30,  # seconds
        },
        "bot": {
            "default_pixel_limit": DEFAULT_PIXEL_LIMIT,
            "default_tolerance": 5,
            "default_delay": 50,
            "max_pixel_limit": MAX_PIXEL_LIMIT,
            "safety_checks": True,
        },
        "logging": {
            "level": "INFO",
            "max_log_files": 7,
            "log_to_file": True,
            "log_to_console": True,
        },
        "analysis": {
            "auto_refresh_interval": 0,  # 0 = disabled
            "save_debug_images": True,
            "image_quality": "high",
        },
    }

    def __init__(self, config_file: str = "config.json") -> None:
        """Initialize configuration and merge values from ``config_file`` if present."""
        self._raw_config_file = config_file
        self.config_file = resolve_resource_path(config_file)
        self.config: dict[str, Any] = copy.deepcopy(self.DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        """Load configuration from file."""
        self.config_file = resolve_resource_path(self._raw_config_file)
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                    if isinstance(loaded_config, dict):
                        self._merge_config(self.config, loaded_config)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                logger.exception("Error loading config from %s", self.config_file)

    def save(self) -> None:
        """Save configuration to writable path next to executable or working directory."""
        target_path = resolve_writable_path(self._raw_config_file)
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            self.config_file = target_path
        except (OSError, TypeError, ValueError):
            logger.exception("Error saving config to %s", target_path)

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., ``'ui.theme'``)."""
        keys = key_path.split(".")
        value: Any = self.config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def set(self, key_path: str, value: Any) -> None:
        """Set configuration value using dot notation."""
        keys = key_path.split(".")
        config = self.config

        for key in keys[:-1]:
            if key not in config or not isinstance(config[key], dict):
                config[key] = {}
            config = config[key]

        config[keys[-1]] = value

    def _merge_config(self, base: dict[str, Any], update: dict[str, Any]) -> None:
        """Recursively merge configuration dictionaries."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value


_config_instance: Config | None = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
