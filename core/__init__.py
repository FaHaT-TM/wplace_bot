"""Core components for Place Bot.

Uses PEP 562 lazy attribute loading so headless utilities and unit tests can
import configuration, validation, and worker modules without eagerly initializing
GUI/OS display bindings until requested.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AnalysisWorker": (".analysis_worker", "AnalysisWorker"),
    "BotWorker": (".bot_worker", "BotWorker"),
    "CaptureError": (".exceptions", "CaptureError"),
    "ConfigError": (".exceptions", "ConfigError"),
    "DEFAULT_PIXEL_LIMIT": (".config", "DEFAULT_PIXEL_LIMIT"),
    "DataManager": (".data_manager", "DataManager"),
    "MAX_PIXEL_LIMIT": (".config", "MAX_PIXEL_LIMIT"),
    "MIN_PIXEL_LIMIT": (".config", "MIN_PIXEL_LIMIT"),
    "MIN_REGION_DIMENSION": (".screen_capture", "MIN_REGION_DIMENSION"),
    "PaintError": (".exceptions", "PaintError"),
    "PaletteDetectionError": (".exceptions", "PaletteDetectionError"),
    "PlaceBotError": (".exceptions", "PlaceBotError"),
    "RegionError": (".exceptions", "RegionError"),
    "ScreenCapturer": (".screen_capture", "ScreenCapturer"),
    "auto_click_positions": (".automation", "auto_click_positions"),
    "build_pixel_map": (".image_analysis", "build_pixel_map"),
    "clamp_pixel_limit": (".config", "clamp_pixel_limit"),
    "compute_region_scale": (".screen_capture", "compute_region_scale"),
    "detect_palette_colors": (".color_detection", "detect_palette_colors"),
    "estimate_pixel_size": (".image_analysis", "estimate_pixel_size"),
    "find_pixels_to_paint": (".image_analysis", "find_pixels_to_paint"),
    "find_pixels_to_paint_from_map": (
        ".pixel_mapping",
        "find_pixels_to_paint_from_map",
    ),
    "get_config": (".config", "get_config"),
    "get_logger": (".logger", "get_logger"),
    "get_preview_positions_from_estimation": (
        ".image_analysis",
        "get_preview_positions_from_estimation",
    ),
    "get_screen": (".screen_capture", "get_screen"),
    "image_offset_to_screen_point": (
        ".screen_capture",
        "image_offset_to_screen_point",
    ),
    "save_palette_debug_image": (".color_detection", "save_palette_debug_image"),
    "validate_pixel_limit": (".config", "validate_pixel_limit"),
    "validate_point_in_region": (".screen_capture", "validate_point_in_region"),
    "validate_region": (".screen_capture", "validate_region"),
}

__all__ = [
    "DEFAULT_PIXEL_LIMIT",
    "MAX_PIXEL_LIMIT",
    "MIN_PIXEL_LIMIT",
    "MIN_REGION_DIMENSION",
    "AnalysisWorker",
    "BotWorker",
    "CaptureError",
    "ConfigError",
    "DataManager",
    "PaintError",
    "PaletteDetectionError",
    "PlaceBotError",
    "RegionError",
    "ScreenCapturer",
    "auto_click_positions",
    "build_pixel_map",
    "clamp_pixel_limit",
    "compute_region_scale",
    "detect_palette_colors",
    "estimate_pixel_size",
    "find_pixels_to_paint",
    "find_pixels_to_paint_from_map",
    "get_config",
    "get_logger",
    "get_preview_positions_from_estimation",
    "get_screen",
    "image_offset_to_screen_point",
    "save_palette_debug_image",
    "validate_pixel_limit",
    "validate_point_in_region",
    "validate_region",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve public core symbols on first access."""
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        module = importlib.import_module(module_name, package=__name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
