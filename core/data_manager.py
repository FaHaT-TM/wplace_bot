"""Data management for color palette, profiles, user preferences, and session progress."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .exceptions import RegionError
from .screen_capture import validate_region
from .session_progress import (
    DEFAULT_PROGRESS_FILENAME,
    PROGRESS_SCHEMA_VERSION,
    SessionProgress,
    clear_session_progress,
    compute_template_fingerprint,
    load_session_progress,
    save_session_progress,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

DEFAULT_COLORS_FILE: str = "colors.json"
DEFAULT_USER_SETTINGS_FILE: str = "user_settings.json"
DEFAULT_PROFILE_NAME: str = "Default"

DEFAULT_PREF_COLOR_TOLERANCE: int = 1
DEFAULT_PREF_CLICK_DELAY_MS: int = 15
DEFAULT_PREF_PIXEL_LIMIT: int = 1000
DEFAULT_PREF_AUTO_SAVE_REGIONS: bool = True
DEFAULT_PREF_REANALYZE_BEFORE_START: bool = True
DEFAULT_PREF_FONT_SCALE: float = 1.25


def _normalize_region_tuple(region: Any) -> tuple[int, int, int, int] | None:
    """Return normalized 4-int tuple if ``region`` is a valid region, otherwise ``None``."""
    try:
        return validate_region(region, allow_negative_origin=True)
    except RegionError:
        return None


class DataManager:
    """Manages color palette, profile configurations, user settings, and runtime session state."""

    def __init__(
        self,
        colors_file: str = DEFAULT_COLORS_FILE,
        settings_file: str = DEFAULT_USER_SETTINGS_FILE,
        progress_file: str | None = None,
    ) -> None:
        """Initialize the data manager and load palette, user settings, and matching progress.

        Args:
            colors_file: Path to the JSON file defining the Wplace color palette.
            settings_file: Path to the JSON file storing user settings and profiles.
            progress_file: Optional path to the JSON file storing session progress.
        """
        self.colors_file = colors_file
        self.settings_file = settings_file
        self.progress_file: str = (
            progress_file
            if progress_file is not None
            else str(Path(settings_file).with_name(DEFAULT_PROGRESS_FILENAME))
        )
        self._progress_lock = threading.Lock()

        self.color_palette: list[dict[str, Any]] = self._load_color_palette()
        self.user_settings: dict[str, Any] = self._load_user_settings()

        # Analysis & session progress state
        self.canvas_region: Any = self.user_settings.get("preferences", {}).get(
            "last_canvas_region"
        )
        self.palette_region: Any = self.user_settings.get("preferences", {}).get(
            "last_palette_region"
        )
        self.pixel_map: dict[tuple[int, int], dict[str, Any]] | None = None
        self.color_position_map: dict[tuple[int, int, int], tuple[int, int]] | None = (
            None
        )
        self.pixel_size: int | None = None
        self.canvas_scale: tuple[float, float] = (1.0, 1.0)
        self.canvas_image_rgb: Any = None
        self.painted_positions: set[tuple[int, int]] = set()
        self.template_fingerprint: str = ""

        self._restore_session_progress_if_matching()

    def _restore_session_progress_if_matching(self) -> None:
        """Restore persisted session progress if saved regions match current preferences."""
        saved_progress = load_session_progress(self.progress_file)
        if saved_progress is None:
            return

        current_canvas = _normalize_region_tuple(self.canvas_region)
        current_palette = _normalize_region_tuple(self.palette_region)
        if (
            current_canvas is None
            or current_palette is None
            or saved_progress.canvas_region != current_canvas
            or saved_progress.palette_region != current_palette
        ):
            logger.info(
                "Ignoring saved session progress because current regions do not match."
            )
            return

        self.canvas_region = saved_progress.canvas_region
        self.palette_region = saved_progress.palette_region
        self.pixel_size = saved_progress.pixel_size
        self.canvas_scale = saved_progress.canvas_scale
        self.pixel_map = {
            pos: dict(colors) for pos, colors in saved_progress.pixel_map.items()
        }
        self.color_position_map = dict(saved_progress.color_position_map)
        self.painted_positions = set(saved_progress.painted_positions)
        self.template_fingerprint = saved_progress.template_fingerprint
        logger.info(
            "Restored session progress: %d painted pixels across %d grid cells.",
            len(self.painted_positions),
            len(self.pixel_map),
        )

    def _load_color_palette(self) -> list[dict[str, Any]]:
        """Load color palette from JSON file, excluding ignored colors."""
        try:
            with open(self.colors_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                filtered_palette = [
                    color
                    for color in data["color_palette"]
                    if not color.get("ignore", False)
                ]
                logger.debug(
                    "Loaded %d colors (filtered out ignored colors)",
                    len(filtered_palette),
                )
                return filtered_palette
        except FileNotFoundError:
            logger.error("Color palette file not found: %s", self.colors_file)
            return []
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            logger.exception("Failed to load %s", self.colors_file)
            return []

    def _build_default_profile_colors(self) -> dict[str, dict[str, bool]]:
        """Build default color configuration with all colors enabled and premium unbought."""
        profile_colors: dict[str, dict[str, bool]] = {}
        for color in self.color_palette:
            color_id = str(color["id"])
            if color.get("premium", False):
                profile_colors[color_id] = {"enabled": True, "bought": False}
            else:
                profile_colors[color_id] = {"enabled": True}
        return profile_colors

    def _build_default_settings(self) -> dict[str, Any]:
        """Build default user settings structure."""
        return {
            "color_profiles": {
                DEFAULT_PROFILE_NAME: {
                    "colors": self._build_default_profile_colors(),
                }
            },
            "active_color_profile": DEFAULT_PROFILE_NAME,
            "preferences": {
                "color_tolerance": DEFAULT_PREF_COLOR_TOLERANCE,
                "click_delay": DEFAULT_PREF_CLICK_DELAY_MS,
                "pixel_limit": DEFAULT_PREF_PIXEL_LIMIT,
                "auto_save_regions": DEFAULT_PREF_AUTO_SAVE_REGIONS,
                "reanalyze_before_start": DEFAULT_PREF_REANALYZE_BEFORE_START,
                "font_scale": DEFAULT_PREF_FONT_SCALE,
            },
        }

    def _load_user_settings(self) -> dict[str, Any]:
        """Load user settings from JSON file or return clean defaults."""
        default_settings = self._build_default_settings()

        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)

                if (
                    "bought_colors" in settings
                    or "enabled_colors" in settings
                    or "colors" in settings
                ):
                    settings = self._migrate_old_format(settings)

                for key, default_val in default_settings.items():
                    if key not in settings:
                        settings[key] = copy.deepcopy(default_val)

                if isinstance(settings.get("preferences"), dict):
                    for pref_key, pref_val in default_settings["preferences"].items():
                        settings["preferences"].setdefault(pref_key, pref_val)
                else:
                    settings["preferences"] = copy.deepcopy(
                        default_settings["preferences"]
                    )

                return settings
            return default_settings
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.exception("Failed to load user settings from %s", self.settings_file)
            return default_settings

    def _migrate_old_format(self, old_settings: dict[str, Any]) -> dict[str, Any]:
        """Convert legacy settings format to the profile-based format."""
        new_settings: dict[str, Any] = {
            "color_profiles": {DEFAULT_PROFILE_NAME: {"colors": {}}},
            "active_color_profile": DEFAULT_PROFILE_NAME,
            "preferences": old_settings.get("preferences", {}),
        }

        colors_data: dict[str, dict[str, Any]] = {}
        if "colors" in old_settings and isinstance(old_settings["colors"], dict):
            colors_data = old_settings["colors"]
        else:
            all_color_ids: set[str] = set()
            if "bought_colors" in old_settings:
                all_color_ids.update(old_settings["bought_colors"].keys())
            if "enabled_colors" in old_settings:
                all_color_ids.update(old_settings["enabled_colors"].keys())

            for color_id in all_color_ids:
                color_data: dict[str, Any] = {}
                if color_id in old_settings.get("enabled_colors", {}):
                    color_data["enabled"] = old_settings["enabled_colors"][color_id]
                if color_id in old_settings.get("bought_colors", {}):
                    color_data["bought"] = old_settings["bought_colors"][color_id]
                colors_data[color_id] = color_data

        new_settings["color_profiles"][DEFAULT_PROFILE_NAME]["colors"] = colors_data
        return new_settings

    def save_user_settings(self) -> None:
        """Save user settings to JSON file."""
        try:
            if self.canvas_region:
                self.user_settings["preferences"]["last_canvas_region"] = (
                    self.canvas_region
                )
            if self.palette_region:
                self.user_settings["preferences"]["last_palette_region"] = (
                    self.palette_region
                )

            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.user_settings, f, indent=2)

        except (OSError, TypeError, ValueError):
            logger.exception("Failed to save user settings to %s", self.settings_file)

    def update_preference(self, key: str, value: Any) -> None:
        """Update a preference setting and persist."""
        self.user_settings["preferences"][key] = value
        self.save_user_settings()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a preference setting."""
        return self.user_settings["preferences"].get(key, default)

    def set_canvas_region(self, region: Any) -> None:
        """Set canvas region, invalidate stale progress if changed, and persist."""
        prev_norm = _normalize_region_tuple(self.canvas_region)
        new_norm = _normalize_region_tuple(region)
        if prev_norm is not None and new_norm != prev_norm:
            self.clear_progress(keep_analysis=False)
        self.canvas_region = new_norm if new_norm is not None else region
        self.save_user_settings()

    def set_palette_region(self, region: Any) -> None:
        """Set palette region, invalidate stale progress if changed, and persist."""
        prev_norm = _normalize_region_tuple(self.palette_region)
        new_norm = _normalize_region_tuple(region)
        if prev_norm is not None and new_norm != prev_norm:
            self.clear_progress(keep_analysis=False)
        self.palette_region = new_norm if new_norm is not None else region
        self.save_user_settings()

    def get_color_setting(
        self, color_id: str | int, key: str, default: Any = None
    ) -> Any:
        """Get a color-specific setting from active profile."""
        active_profile = self.user_settings["active_color_profile"]
        profile_colors = (
            self.user_settings["color_profiles"]
            .get(active_profile, {})
            .get("colors", {})
        )
        return profile_colors.get(str(color_id), {}).get(key, default)

    def set_color_setting(self, color_id: str | int, key: str, value: Any) -> None:
        """Set a color-specific setting in active profile and persist."""
        color_key = str(color_id)
        active_profile = self.user_settings["active_color_profile"]

        if active_profile not in self.user_settings["color_profiles"]:
            self.user_settings["color_profiles"][active_profile] = {"colors": {}}

        profile_colors = self.user_settings["color_profiles"][active_profile]["colors"]
        if color_key not in profile_colors:
            profile_colors[color_key] = {}

        profile_colors[color_key][key] = value
        self.save_user_settings()

    def get_active_profile(self) -> str:
        """Get active color profile name."""
        return str(self.user_settings["active_color_profile"])

    def get_profile_names(self) -> list[str]:
        """Get list of all profile names."""
        return list(self.user_settings["color_profiles"].keys())

    def create_profile(self, name: str) -> bool:
        """Create new color profile initialized with clean defaults."""
        if name not in self.user_settings["color_profiles"]:
            self.user_settings["color_profiles"][name] = {
                "colors": self._build_default_profile_colors()
            }
            self.save_user_settings()
            return True
        return False

    def switch_profile(self, name: str) -> bool:
        """Switch to a different color profile."""
        if name in self.user_settings["color_profiles"]:
            self.user_settings["active_color_profile"] = name
            self.save_user_settings()
            return True
        return False

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        """Rename an existing color profile."""
        if (
            old_name in self.user_settings["color_profiles"]
            and new_name not in self.user_settings["color_profiles"]
        ):
            self.user_settings["color_profiles"][new_name] = self.user_settings[
                "color_profiles"
            ].pop(old_name)
            if self.user_settings["active_color_profile"] == old_name:
                self.user_settings["active_color_profile"] = new_name
            self.save_user_settings()
            return True
        return False

    def delete_profile(self, name: str) -> bool:
        """Delete a color profile (cannot delete Default)."""
        if (
            name != DEFAULT_PROFILE_NAME
            and name in self.user_settings["color_profiles"]
        ):
            del self.user_settings["color_profiles"][name]
            if self.user_settings["active_color_profile"] == name:
                self.user_settings["active_color_profile"] = DEFAULT_PROFILE_NAME
            self.save_user_settings()
            return True
        return False

    def copy_profile(self, source_name: str, new_name: str) -> bool:
        """Copy an existing profile into a new profile."""
        if (
            source_name in self.user_settings["color_profiles"]
            and new_name not in self.user_settings["color_profiles"]
        ):
            self.user_settings["color_profiles"][new_name] = copy.deepcopy(
                self.user_settings["color_profiles"][source_name]
            )
            self.save_user_settings()
            return True
        return False

    def is_color_bought(self, color: dict[str, Any]) -> bool:
        """Check whether a color is available to use (free or bought premium)."""
        if not color.get("premium", False):
            return True
        return bool(self.get_color_setting(color["id"], "bought", False))

    def get_enabled_colors(self) -> list[dict[str, Any]]:
        """Get list of enabled colors in the active profile."""
        enabled_colors: list[dict[str, Any]] = []
        for color in self.color_palette:
            color_id = str(color["id"])
            is_enabled = self.get_color_setting(color_id, "enabled", True)
            if is_enabled:
                enabled_colors.append(color)
        return enabled_colors

    def get_paintable_colors(self) -> list[dict[str, Any]]:
        """Get list of colors that are both enabled and available (free or bought)."""
        return [
            color
            for color in self.get_enabled_colors()
            if self.is_color_bought(color)
        ]

    def set_analysis_results(
        self,
        pixel_size: int,
        pixel_map: dict[tuple[int, int], dict[str, Any]],
        color_position_map: dict[tuple[int, int, int], tuple[int, int]],
        canvas_scale: tuple[float, float] = (1.0, 1.0),
        canvas_image_rgb: Any = None,
    ) -> None:
        """Store fresh runtime analysis results and reset painted-session tracking."""
        with self._progress_lock:
            self.pixel_size = pixel_size
            self.pixel_map = {pos: dict(colors) for pos, colors in pixel_map.items()}
            self.color_position_map = dict(color_position_map)
            self.canvas_scale = canvas_scale
            self.canvas_image_rgb = canvas_image_rgb
            self.painted_positions = set()
            self.template_fingerprint = compute_template_fingerprint(self.pixel_map)

        clear_session_progress(self.progress_file)

    def record_painted_pixel(
        self,
        pos: tuple[int, int],
        painted_bgr: tuple[int, int, int],
    ) -> None:
        """Record a painted canvas pixel coordinate in memory for immediate resume safety."""
        with self._progress_lock:
            self.painted_positions.add((int(pos[0]), int(pos[1])))
            if self.pixel_map is not None and pos in self.pixel_map:
                self.pixel_map[pos]["pixel_color"] = (
                    int(painted_bgr[0]),
                    int(painted_bgr[1]),
                    int(painted_bgr[2]),
                )

    def save_progress_checkpoint(self, status: str = "in_progress") -> bool:
        """Atomically persist current session progress to disk if valid analysis data exists."""
        with self._progress_lock:
            canvas_norm = _normalize_region_tuple(self.canvas_region)
            palette_norm = _normalize_region_tuple(self.palette_region)
            if (
                canvas_norm is None
                or palette_norm is None
                or self.pixel_size is None
                or self.pixel_size <= 0
                or self.pixel_map is None
                or not self.color_position_map
                or not self.painted_positions
            ):
                return False

            if not self.template_fingerprint:
                self.template_fingerprint = compute_template_fingerprint(self.pixel_map)

            snapshot = SessionProgress(
                version=PROGRESS_SCHEMA_VERSION,
                canvas_region=canvas_norm,
                palette_region=palette_norm,
                pixel_size=int(self.pixel_size),
                canvas_scale=(
                    float(self.canvas_scale[0]),
                    float(self.canvas_scale[1]),
                ),
                template_fingerprint=self.template_fingerprint,
                painted_positions=set(self.painted_positions),
                pixel_map={
                    pos: {
                        "preview_color": tuple(vals["preview_color"]),  # type: ignore[misc]
                        "pixel_color": tuple(vals["pixel_color"]),  # type: ignore[misc]
                    }
                    for pos, vals in self.pixel_map.items()
                },
                color_position_map=dict(self.color_position_map),
                status=status,
                updated_at=utc_now_iso(),
            )

        return save_session_progress(self.progress_file, snapshot)

    def clear_progress(self, keep_analysis: bool = True) -> None:
        """Clear painted session tracking and delete any persisted progress file."""
        with self._progress_lock:
            self.painted_positions = set()
            if not keep_analysis:
                self.pixel_map = None
                self.color_position_map = None
                self.pixel_size = None
                self.template_fingerprint = ""
        clear_session_progress(self.progress_file)

    def has_resumable_progress(self) -> bool:
        """Return ``True`` if there is an active or restored session with painted pixels."""
        with self._progress_lock:
            return (
                self.has_regions()
                and self.has_analysis_data()
                and len(self.painted_positions) > 0
            )

    def get_progress_summary(self) -> dict[str, int]:
        """Return summary counts of painted and total grid pixels."""
        with self._progress_lock:
            return {
                "painted": len(self.painted_positions),
                "total_grid": len(self.pixel_map) if self.pixel_map is not None else 0,
            }

    def has_analysis_data(self) -> bool:
        """Check if analysis data is available."""
        return self.pixel_map is not None and self.color_position_map is not None

    def has_regions(self) -> bool:
        """Check if both canvas and palette regions are selected."""
        return self.canvas_region is not None and self.palette_region is not None
