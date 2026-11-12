"""Palette swatch color detection and debug visualization."""

from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np

from .config import DEFAULT_DEBUG_DIR, get_config
from .image_io import save_bgr_image
from .screen_capture import (
    compute_region_scale,
    image_offset_to_screen_point,
    validate_region,
)

logger = logging.getLogger(__name__)

DEFAULT_PALETTE_TOLERANCE: int = 3
MIN_SWATCH_CONTOUR_AREA: float = 100.0
DEBUG_SWATCH_BOX_SIZE: int = 10
DEFAULT_PALETTE_DEBUG_IMAGE: str = f"{DEFAULT_DEBUG_DIR}/palette.png"


def detect_palette_colors(
    palette_img_rgb: np.ndarray,
    palette_region: Any,
    known_colors: list[dict[str, Any]],
    tolerance: int = DEFAULT_PALETTE_TOLERANCE,
) -> dict[tuple[int, int, int], tuple[int, int]]:
    """Detect palette color swatches and map RGB tuples to logical screen coordinates.

    Args:
        palette_img_rgb: RGB screenshot array of the selected palette region.
        palette_region: Logical ``(left, top, width, height)`` screen region of the palette.
        known_colors: Palette color definitions containing ``"rgb"``.
        tolerance: Per-channel RGB matching tolerance.

    Returns:
        Dictionary mapping ``(r, g, b)`` tuples to logical screen ``(x, y)`` coordinates.
    """
    validated_region = validate_region(
        palette_region, name="Palette region", allow_negative_origin=True
    )
    scale = compute_region_scale(palette_img_rgb.shape, validated_region)

    color_map: dict[tuple[int, int, int], tuple[int, int]] = {}
    palette_img_bgr = cv2.cvtColor(palette_img_rgb, cv2.COLOR_RGB2BGR)

    left, top, width, height = validated_region

    for color_data in known_colors:
        rgb_list = color_data["rgb"]
        target_rgb: tuple[int, int, int] = (
            int(rgb_list[0]),
            int(rgb_list[1]),
            int(rgb_list[2]),
        )
        target_bgr = (target_rgb[2], target_rgb[1], target_rgb[0])

        lower_bound = np.array([max(0, c - tolerance) for c in target_bgr], dtype=np.uint8)
        upper_bound = np.array([min(255, c + tolerance) for c in target_bgr], dtype=np.uint8)

        mask = cv2.inRange(palette_img_bgr, lower_bound, upper_bound)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contours:
            largest_contour = max(contours, key=cv2.contourArea)
            if cv2.contourArea(largest_contour) > MIN_SWATCH_CONTOUR_AREA:
                moments = cv2.moments(largest_contour)
                if moments["m00"] != 0:
                    cx = int(moments["m10"] / moments["m00"])
                    cy = int(moments["m01"] / moments["m00"])

                    screen_x, screen_y = image_offset_to_screen_point(
                        cx, cy, validated_region, scale
                    )
                    # Clamp to interior of palette region in case of rounding on border
                    screen_x = max(left, min(left + width - 1, screen_x))
                    screen_y = max(top, min(top + height - 1, screen_y))
                    color_map[target_rgb] = (screen_x, screen_y)

    return color_map


def save_palette_debug_image(
    palette_img_rgb: np.ndarray,
    color_map: dict[tuple[int, int, int], tuple[int, int]],
    palette_region: Any,
    filename: str = DEFAULT_PALETTE_DEBUG_IMAGE,
) -> None:
    """Save a debug image highlighting detected palette swatch centers."""
    if filename == DEFAULT_PALETTE_DEBUG_IMAGE and not bool(
        get_config().get("analysis.save_debug_images", True)
    ):
        return

    validated_region = validate_region(
        palette_region, name="Palette region", allow_negative_origin=True
    )
    scale_x, scale_y = compute_region_scale(palette_img_rgb.shape, validated_region)

    debug_img = cv2.cvtColor(palette_img_rgb, cv2.COLOR_RGB2BGR)
    half_box = DEBUG_SWATCH_BOX_SIZE // 2

    for screen_coords in color_map.values():
        img_x = round((screen_coords[0] - validated_region[0]) * scale_x)
        img_y = round((screen_coords[1] - validated_region[1]) * scale_y)
        cv2.rectangle(
            debug_img,
            (img_x - half_box, img_y - half_box),
            (img_x + half_box, img_y + half_box),
            (0, 255, 0),
            2,
        )

    saved_path = save_bgr_image(filename, debug_img)
    logger.debug("Palette debug image saved: %s", saved_path)
