"""Deterministic paint order planning, ETA estimation, and Dry-Run diff visualization."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .config import (
    MAX_PIXEL_LIMIT,
    MIN_PIXEL_LIMIT,
    get_config,
    validate_pixel_limit,
)
from .exceptions import PaintError, PaletteDetectionError, RegionError
from .image_io import save_rgb_image
from .pixel_mapping import find_pixels_to_paint_from_map
from .screen_capture import (
    MonitorInfo,
    image_offset_to_screen_point,
    validate_point_in_region,
    validate_region,
)

logger = logging.getLogger(__name__)

DEFAULT_DRY_RUN_IMAGE: str = "debug_dry_run.png"
DEFAULT_PALETTE_SWITCH_DELAY_S: float = 0.2
CANVAS_BG_DIM_FACTOR: float = 0.45
SYNTHETIC_CANVAS_MIN_WIDTH: int = 120
SYNTHETIC_CANVAS_MIN_HEIGHT: int = 80
SYNTHETIC_CANVAS_PADDING_PX: int = 20
SYNTHETIC_BG_VALUE: int = 32
UNCHANGED_BORDER_BGR: tuple[int, int, int] = (75, 75, 75)
PLANNED_BORDER_BGR: tuple[int, int, int] = (0, 255, 0)
DEFERRED_BORDER_BGR: tuple[int, int, int] = (0, 165, 255)


@dataclass(frozen=True, slots=True)
class ColorPlanEntry:
    """Planned painting work and timing estimate for a single palette color."""

    color_id: int
    color_name: str
    rgb: tuple[int, int, int]
    premium: bool
    swatch_point: tuple[int, int]
    matched_count: int
    planned_count: int
    deferred_by_limit_count: int
    targets: tuple[tuple[tuple[int, int], tuple[int, int]], ...]
    deferred_positions: tuple[tuple[int, int], ...]
    estimated_seconds: float


@dataclass(frozen=True, slots=True)
class PaintPlan:
    """Complete deterministic execution plan for dry-run inspection or live painting."""

    pixel_limit: int
    tolerance: int
    click_delay_ms: int
    palette_switch_delay_s: float
    total_detected_pixels: int
    total_matching_unpainted: int
    total_planned_pixels: int
    total_deferred_by_limit: int
    limit_will_be_reached: bool
    color_entries: tuple[ColorPlanEntry, ...]
    active_color_switches: int
    skipped_unbought_colors: tuple[str, ...]
    missing_palette_colors: tuple[str, ...]
    out_of_bounds_skipped: int
    estimated_seconds: float
    resumed_skipped_count: int = 0


def format_duration(seconds: float) -> str:
    """Format a duration in seconds into a concise human-readable string."""
    clamped = max(0.0, float(seconds))
    if clamped < 60.0:
        return f"{clamped:.1f}s"

    total_secs = round(clamped)
    hours, rem = divmod(total_secs, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def _is_color_paintable(data_manager: Any, color: dict[str, Any]) -> bool:
    """Return ``True`` if ``color`` is free or marked as bought in ``data_manager``."""
    if not color.get("premium", False):
        return True
    checker = getattr(data_manager, "is_color_bought", None)
    if callable(checker):
        return bool(checker(color))
    return bool(color.get("bought", False))


def build_paint_plan(
    data_manager: Any,
    enabled_colors: Sequence[dict[str, Any]],
    pixel_limit: Any,
    tolerance: int,
    click_delay_ms: int,
    palette_switch_delay_s: float = DEFAULT_PALETTE_SWITCH_DELAY_S,
    screen_size: tuple[int, int] | None = None,
    monitors: Sequence[MonitorInfo] | None = None,
) -> PaintPlan:
    """Compute the exact per-color pixel painting plan and estimated runtime without clicking.

    Args:
        data_manager: Shared application data manager holding regions and analysis maps.
        enabled_colors: Sequence of enabled palette color dictionaries.
        pixel_limit: Requested maximum pixel count for this run.
        tolerance: Per-channel color matching tolerance.
        click_delay_ms: Post-click delay in milliseconds.
        palette_switch_delay_s: Delay after clicking a palette swatch in seconds.
        screen_size: Optional fallback primary screen dimensions ``(width, height)``.
        monitors: Optional connected display topology for multi-monitor bounds validation.

    Returns:
        Deterministic :class:`PaintPlan` with per-color targets, counts, and ETA.
    """
    max_limit = int(get_config().get("bot.max_pixel_limit", MAX_PIXEL_LIMIT))
    validated_limit = validate_pixel_limit(
        pixel_limit,
        min_limit=MIN_PIXEL_LIMIT,
        max_limit=max_limit,
    )
    validated_tolerance = int(tolerance)
    validated_delay_ms = max(0, int(click_delay_ms))
    switch_delay = max(0.0, float(palette_switch_delay_s))

    allow_negative = monitors is None and screen_size is None
    canvas_region = validate_region(
        data_manager.canvas_region,
        name="Canvas region",
        screen_size=screen_size,
        monitors=monitors,
        allow_negative_origin=allow_negative,
    )

    palette_region_raw = getattr(data_manager, "palette_region", None)
    palette_region: tuple[int, int, int, int] | None = None
    if palette_region_raw is not None:
        palette_region = validate_region(
            palette_region_raw,
            name="Palette region",
            screen_size=screen_size,
            monitors=monitors,
            allow_negative_origin=allow_negative,
        )

    pixel_map = data_manager.pixel_map
    if pixel_map is None:
        raise PaintError(
            "Canvas pixel map is missing. Please run analysis before starting."
        )

    color_position_map = data_manager.color_position_map
    if not color_position_map:
        raise PaletteDetectionError(
            "Palette color map is empty. Please reanalyze the palette region before starting."
        )

    canvas_scale: tuple[float, float] = getattr(
        data_manager, "canvas_scale", (1.0, 1.0)
    )

    skipped_unbought: list[str] = []
    missing_palette: list[str] = []
    paintable_colors: list[dict[str, Any]] = []

    for color in enabled_colors:
        if _is_color_paintable(data_manager, color):
            paintable_colors.append(color)
        else:
            skipped_unbought.append(str(color.get("name", color.get("id", "Unknown"))))

    already_painted: set[tuple[int, int]] = set(
        getattr(data_manager, "painted_positions", ())
    )
    claimed_positions: set[tuple[int, int]] = set(already_painted)
    resumed_skipped_count = len(already_painted)
    color_entries: list[ColorPlanEntry] = []
    out_of_bounds_skipped = 0
    total_matching_unpainted = 0
    total_planned_pixels = 0
    total_estimated_seconds = 0.0
    delay_seconds = validated_delay_ms / 1000.0

    for color in paintable_colors:
        rgb_raw = color["rgb"]
        target_rgb: tuple[int, int, int] = (
            int(rgb_raw[0]),
            int(rgb_raw[1]),
            int(rgb_raw[2]),
        )
        color_name = str(color.get("name", f"Color {color.get('id', '?')}"))

        if target_rgb not in color_position_map:
            missing_palette.append(color_name)
            continue

        target_bgr = (target_rgb[2], target_rgb[1], target_rgb[0])
        raw_positions = find_pixels_to_paint_from_map(
            pixel_map, target_bgr, tolerance=validated_tolerance
        )
        if not raw_positions:
            continue

        px, py = color_position_map[target_rgb]
        if palette_region is not None:
            validate_point_in_region(
                px,
                py,
                palette_region,
                name="Palette region",
                screen_size=screen_size,
                monitors=monitors,
            )

        matched_valid: list[tuple[tuple[int, int], tuple[int, int]]] = []
        for pos in raw_positions:
            if pos in claimed_positions:
                continue

            click_x, click_y = image_offset_to_screen_point(
                pos[0], pos[1], canvas_region, canvas_scale
            )
            try:
                validate_point_in_region(
                    click_x,
                    click_y,
                    canvas_region,
                    name="Canvas region",
                    screen_size=screen_size,
                    monitors=monitors,
                )
            except RegionError as exc:
                out_of_bounds_skipped += 1
                logger.warning("Skipping out-of-bounds pixel %s: %s", pos, exc)
                continue

            claimed_positions.add(pos)
            matched_valid.append((pos, (click_x, click_y)))

        if not matched_valid:
            continue

        matched_count = len(matched_valid)
        total_matching_unpainted += matched_count

        remaining_budget = max(0, validated_limit - total_planned_pixels)
        planned_slice = matched_valid[:remaining_budget]
        deferred_slice = [item[0] for item in matched_valid[remaining_budget:]]
        planned_count = len(planned_slice)
        deferred_count = len(deferred_slice)

        entry_seconds = 0.0
        if planned_count > 0:
            total_planned_pixels += planned_count
            reaches_limit_here = total_planned_pixels >= validated_limit
            inter_click_waits = (
                max(0, planned_count - 1) if reaches_limit_here else planned_count
            )
            entry_seconds = switch_delay + (inter_click_waits * delay_seconds)
            total_estimated_seconds += entry_seconds

        color_entries.append(
            ColorPlanEntry(
                color_id=int(color.get("id", 0)),
                color_name=color_name,
                rgb=target_rgb,
                premium=bool(color.get("premium", False)),
                swatch_point=(int(px), int(py)),
                matched_count=matched_count,
                planned_count=planned_count,
                deferred_by_limit_count=deferred_count,
                targets=tuple(planned_slice),
                deferred_positions=tuple(deferred_slice),
                estimated_seconds=entry_seconds,
            )
        )

    active_switches = sum(1 for entry in color_entries if entry.planned_count > 0)
    total_deferred = total_matching_unpainted - total_planned_pixels
    limit_reached = (
        total_planned_pixels >= validated_limit
        and total_matching_unpainted >= validated_limit
    )

    return PaintPlan(
        pixel_limit=validated_limit,
        tolerance=validated_tolerance,
        click_delay_ms=validated_delay_ms,
        palette_switch_delay_s=switch_delay,
        total_detected_pixels=len(pixel_map),
        total_matching_unpainted=total_matching_unpainted,
        total_planned_pixels=total_planned_pixels,
        total_deferred_by_limit=total_deferred,
        limit_will_be_reached=limit_reached,
        color_entries=tuple(color_entries),
        active_color_switches=active_switches,
        skipped_unbought_colors=tuple(skipped_unbought),
        missing_palette_colors=tuple(missing_palette),
        out_of_bounds_skipped=out_of_bounds_skipped,
        estimated_seconds=total_estimated_seconds,
        resumed_skipped_count=resumed_skipped_count,
    )


def format_paint_plan_report(plan: PaintPlan) -> str:
    """Format a structured multi-line Dry-Run report with per-color counts and ETA."""
    eta_str = format_duration(plan.estimated_seconds)
    lines: list[str] = [
        "=== DRY-RUN PAINT PLAN (0 MOUSE CLICKS) ===",
        f"Detected Grid Pixels : {plan.total_detected_pixels}",
        f"Painted in Session   : {plan.resumed_skipped_count}",
        f"Unpainted Matches    : {plan.total_matching_unpainted}",
        f"Planned This Run     : {plan.total_planned_pixels} / {plan.pixel_limit}"
        + (" (pixel limit reached)" if plan.limit_will_be_reached else ""),
        f"Deferred by Limit    : {plan.total_deferred_by_limit}",
        f"Palette Switches     : {plan.active_color_switches}",
        f"Settings             : tolerance={plan.tolerance}, delay={plan.click_delay_ms}ms",
        f"Estimated Duration   : {eta_str}",
        "",
    ]

    if plan.color_entries:
        lines.append("Per-Color Breakdown:")
        lines.append(
            f"{'Color':<20} {'Planned':>8} {'Matched':>8} {'Deferred':>9} {'ETA':>10}"
        )
        lines.append("-" * 59)
        for entry in plan.color_entries:
            entry_eta = (
                format_duration(entry.estimated_seconds)
                if entry.planned_count > 0
                else "0.0s"
            )
            lines.append(
                f"{entry.color_name:<20.20} "
                f"{entry.planned_count:>8} "
                f"{entry.matched_count:>8} "
                f"{entry.deferred_by_limit_count:>9} "
                f"{entry_eta:>10}"
            )
    else:
        lines.append("No unpainted pixels matched any available palette colors.")

    if plan.skipped_unbought_colors:
        lines.append("")
        lines.append(
            "Skipped unbought premium colors: "
            + ", ".join(plan.skipped_unbought_colors)
        )

    if plan.missing_palette_colors:
        lines.append(
            "Enabled colors not found in detected palette: "
            + ", ".join(plan.missing_palette_colors)
        )

    if plan.out_of_bounds_skipped > 0:
        lines.append(
            f"Out-of-bounds pixels skipped: {plan.out_of_bounds_skipped}"
        )

    return "\n".join(lines)


def build_dry_run_diff_rgb(
    plan: PaintPlan,
    pixel_map: dict[tuple[int, int], dict[str, Any]],
    pixel_size: int,
    canvas_image_rgb: np.ndarray | None = None,
) -> np.ndarray:
    """Build an in-memory RGB visual diff array showing planned and deferred pixels."""
    box_size = max(4, int(pixel_size or 10))
    half = box_size // 2

    if (
        canvas_image_rgb is not None
        and isinstance(canvas_image_rgb, np.ndarray)
        and canvas_image_rgb.ndim >= 3
        and canvas_image_rgb.shape[0] > 0
        and canvas_image_rgb.shape[1] > 0
    ):
        base_bgr = cv2.cvtColor(canvas_image_rgb, cv2.COLOR_RGB2BGR)
        diff_bgr = np.clip(
            base_bgr.astype(np.float32) * CANVAS_BG_DIM_FACTOR, 0, 255
        ).astype(np.uint8)
    else:
        max_x = max((pos[0] for pos in pixel_map), default=0)
        max_y = max((pos[1] for pos in pixel_map), default=0)
        width = max(
            SYNTHETIC_CANVAS_MIN_WIDTH,
            max_x + half + SYNTHETIC_CANVAS_PADDING_PX,
        )
        height = max(
            SYNTHETIC_CANVAS_MIN_HEIGHT,
            max_y + half + SYNTHETIC_CANVAS_PADDING_PX,
        )
        diff_bgr = np.full(
            (height, width, 3), SYNTHETIC_BG_VALUE, dtype=np.uint8
        )

    img_h, img_w = diff_bgr.shape[:2]

    planned_lookup: dict[tuple[int, int], tuple[int, int, int]] = {}
    deferred_lookup: dict[tuple[int, int], tuple[int, int, int]] = {}
    for entry in plan.color_entries:
        bgr = (entry.rgb[2], entry.rgb[1], entry.rgb[0])
        for map_pos, _screen_pos in entry.targets:
            planned_lookup[map_pos] = bgr
        for map_pos in entry.deferred_positions:
            deferred_lookup[map_pos] = bgr

    for cx, cy in pixel_map:
        if (cx, cy) in planned_lookup or (cx, cy) in deferred_lookup:
            continue
        x1 = max(0, cx - half)
        y1 = max(0, cy - half)
        x2 = min(img_w - 1, cx + half)
        y2 = min(img_h - 1, cy + half)
        if x2 > x1 and y2 > y1:
            cv2.rectangle(diff_bgr, (x1, y1), (x2, y2), UNCHANGED_BORDER_BGR, 1)

    for (cx, cy), bgr in deferred_lookup.items():
        x1 = max(0, cx - half)
        y1 = max(0, cy - half)
        x2 = min(img_w - 1, cx + half)
        y2 = min(img_h - 1, cy + half)
        if x2 > x1 and y2 > y1:
            roi = diff_bgr[y1 : y2 + 1, x1 : x2 + 1].astype(np.float32)
            blended = (roi * 0.5) + (np.array(bgr, dtype=np.float32) * 0.5)
            diff_bgr[y1 : y2 + 1, x1 : x2 + 1] = np.clip(blended, 0, 255).astype(
                np.uint8
            )
            cv2.rectangle(diff_bgr, (x1, y1), (x2, y2), DEFERRED_BORDER_BGR, 1)

    for (cx, cy), bgr in planned_lookup.items():
        x1 = max(0, cx - half)
        y1 = max(0, cy - half)
        x2 = min(img_w - 1, cx + half)
        y2 = min(img_h - 1, cy + half)
        if x2 > x1 and y2 > y1:
            cv2.rectangle(diff_bgr, (x1, y1), (x2, y2), bgr, -1)
            cv2.rectangle(diff_bgr, (x1, y1), (x2, y2), PLANNED_BORDER_BGR, 1)
            if 0 <= cy < img_h and 0 <= cx < img_w:
                diff_bgr[cy, cx] = bgr

    return cv2.cvtColor(diff_bgr, cv2.COLOR_BGR2RGB)


def render_dry_run_diff_image(
    plan: PaintPlan,
    pixel_map: dict[tuple[int, int], dict[str, Any]],
    pixel_size: int,
    canvas_image_rgb: np.ndarray | None = None,
    output_path: str = DEFAULT_DRY_RUN_IMAGE,
) -> str:
    """Render and save a visual diff preview image showing planned and deferred pixels.

    Planned pixels are filled in their target color with a bright green border;
    pixels deferred by ``pixel_limit`` are tinted with an amber border; already
    matching pixels are outlined subtly in gray.
    """
    diff_rgb = build_dry_run_diff_rgb(
        plan=plan,
        pixel_map=pixel_map,
        pixel_size=pixel_size,
        canvas_image_rgb=canvas_image_rgb,
    )
    saved_output = save_rgb_image(output_path, diff_rgb)
    logger.debug("Dry-run diff preview image saved: %s", saved_output)
    return saved_output
