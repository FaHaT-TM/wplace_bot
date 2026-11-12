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
    "CHECKPOINT_INTERVAL_PIXELS": (
        ".session_progress",
        "CHECKPOINT_INTERVAL_PIXELS",
    ),
    "CaptureError": (".exceptions", "CaptureError"),
    "ColorPlanEntry": (".paint_planner", "ColorPlanEntry"),
    "ConfigError": (".exceptions", "ConfigError"),
    "DEFAULT_COUNTER_DEBUG_IMAGE": (
        ".site_counter",
        "DEFAULT_COUNTER_DEBUG_IMAGE",
    ),
    "DEFAULT_DRY_RUN_IMAGE": (".paint_planner", "DEFAULT_DRY_RUN_IMAGE"),
    "DEFAULT_PIXEL_LIMIT": (".config", "DEFAULT_PIXEL_LIMIT"),
    "DEFAULT_PROGRESS_FILENAME": (
        ".session_progress",
        "DEFAULT_PROGRESS_FILENAME",
    ),
    "DataManager": (".data_manager", "DataManager"),
    "MAX_PIXEL_LIMIT": (".config", "MAX_PIXEL_LIMIT"),
    "MIN_PIXEL_LIMIT": (".config", "MIN_PIXEL_LIMIT"),
    "MIN_REGION_DIMENSION": (".screen_capture", "MIN_REGION_DIMENSION"),
    "MonitorInfo": (".screen_capture", "MonitorInfo"),
    "PaintError": (".exceptions", "PaintError"),
    "PaintPlan": (".paint_planner", "PaintPlan"),
    "PaletteDetectionError": (".exceptions", "PaletteDetectionError"),
    "PlaceBotError": (".exceptions", "PlaceBotError"),
    "RegionError": (".exceptions", "RegionError"),
    "ScreenCapturer": (".screen_capture", "ScreenCapturer"),
    "SessionProgress": (".session_progress", "SessionProgress"),
    "SiteCounterReading": (".site_counter", "SiteCounterReading"),
    "auto_click_positions": (".automation", "auto_click_positions"),
    "build_dry_run_diff_rgb": (".paint_planner", "build_dry_run_diff_rgb"),
    "build_paint_plan": (".paint_planner", "build_paint_plan"),
    "build_pixel_map": (".image_analysis", "build_pixel_map"),
    "clamp_pixel_limit": (".config", "clamp_pixel_limit"),
    "clear_session_progress": (".session_progress", "clear_session_progress"),
    "compute_region_scale": (".screen_capture", "compute_region_scale"),
    "compute_template_fingerprint": (
        ".session_progress",
        "compute_template_fingerprint",
    ),
    "compute_virtual_screen_bounds": (
        ".screen_capture",
        "compute_virtual_screen_bounds",
    ),
    "detect_monitors": (".screen_capture", "detect_monitors"),
    "detect_palette_colors": (".color_detection", "detect_palette_colors"),
    "estimate_pixel_size": (".image_analysis", "estimate_pixel_size"),
    "find_monitors_for_region": (".screen_capture", "find_monitors_for_region"),
    "find_pixels_to_paint": (".image_analysis", "find_pixels_to_paint"),
    "find_pixels_to_paint_from_map": (
        ".pixel_mapping",
        "find_pixels_to_paint_from_map",
    ),
    "format_duration": (".paint_planner", "format_duration"),
    "format_monitor_summary": (".screen_capture", "format_monitor_summary"),
    "format_paint_plan_report": (".paint_planner", "format_paint_plan_report"),
    "get_app_dir": (".config", "get_app_dir"),
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
    "is_point_on_monitors": (".screen_capture", "is_point_on_monitors"),
    "is_region_covered_by_monitors": (
        ".screen_capture",
        "is_region_covered_by_monitors",
    ),
    "load_session_progress": (".session_progress", "load_session_progress"),
    "parse_counter_text": (".site_counter", "parse_counter_text"),
    "parse_xrandr_monitors": (".screen_capture", "parse_xrandr_monitors"),
    "read_site_pixel_counter": (".site_counter", "read_site_pixel_counter"),
    "recognize_counter_image": (".site_counter", "recognize_counter_image"),
    "render_dry_run_diff_image": (".paint_planner", "render_dry_run_diff_image"),
    "resolve_resource_path": (".config", "resolve_resource_path"),
    "resolve_writable_path": (".config", "resolve_writable_path"),
    "save_bgr_image": (".image_io", "save_bgr_image"),
    "save_palette_debug_image": (".color_detection", "save_palette_debug_image"),
    "save_rgb_image": (".image_io", "save_rgb_image"),
    "save_session_progress": (".session_progress", "save_session_progress"),
    "validate_pixel_limit": (".config", "validate_pixel_limit"),
    "validate_point_in_region": (".screen_capture", "validate_point_in_region"),
    "validate_region": (".screen_capture", "validate_region"),
}

__all__ = [
    "CHECKPOINT_INTERVAL_PIXELS",
    "DEFAULT_COUNTER_DEBUG_IMAGE",
    "DEFAULT_DRY_RUN_IMAGE",
    "DEFAULT_PIXEL_LIMIT",
    "DEFAULT_PROGRESS_FILENAME",
    "MAX_PIXEL_LIMIT",
    "MIN_PIXEL_LIMIT",
    "MIN_REGION_DIMENSION",
    "AnalysisWorker",
    "BotWorker",
    "CaptureError",
    "ColorPlanEntry",
    "ConfigError",
    "DataManager",
    "MonitorInfo",
    "PaintError",
    "PaintPlan",
    "PaletteDetectionError",
    "PlaceBotError",
    "RegionError",
    "ScreenCapturer",
    "SessionProgress",
    "SiteCounterReading",
    "auto_click_positions",
    "build_dry_run_diff_rgb",
    "build_paint_plan",
    "build_pixel_map",
    "clamp_pixel_limit",
    "clear_session_progress",
    "compute_region_scale",
    "compute_template_fingerprint",
    "compute_virtual_screen_bounds",
    "detect_monitors",
    "detect_palette_colors",
    "estimate_pixel_size",
    "find_monitors_for_region",
    "find_pixels_to_paint",
    "find_pixels_to_paint_from_map",
    "format_duration",
    "format_monitor_summary",
    "format_paint_plan_report",
    "get_app_dir",
    "get_config",
    "get_logger",
    "get_preview_positions_from_estimation",
    "get_screen",
    "image_offset_to_screen_point",
    "is_point_on_monitors",
    "is_region_covered_by_monitors",
    "load_session_progress",
    "parse_counter_text",
    "parse_xrandr_monitors",
    "read_site_pixel_counter",
    "recognize_counter_image",
    "render_dry_run_diff_image",
    "resolve_resource_path",
    "resolve_writable_path",
    "save_bgr_image",
    "save_palette_debug_image",
    "save_rgb_image",
    "save_session_progress",
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
