"""Computer vision utilities for canvas grid estimation and pixel map construction."""

from __future__ import annotations

import logging
import statistics
from typing import Any

import cv2
import numpy as np

from .image_io import save_bgr_image

logger = logging.getLogger(__name__)

MIN_CONTOUR_SIZE_PX: int = 5
MAX_CONTOUR_SIZE_PX: int = 50
MIN_SQUARE_SAMPLES: int = 10
DEFAULT_FALLBACK_PIXEL_SIZE: int = 22
SQUARE_ASPECT_MIN: float = 0.8
SQUARE_ASPECT_MAX: float = 1.2
PREVIEW_TO_PIXEL_RATIO: int = 3
PREVIEW_MAX_SCALE_FACTOR: float = 1.5
SAMPLE_CORNER_OFFSET_PX: int = 2
MAX_DEBUG_ANNOTATIONS: int = 50


def estimate_pixel_size(
    img: np.ndarray,
    min_size: int = MIN_CONTOUR_SIZE_PX,
    max_size: int = MAX_CONTOUR_SIZE_PX,
    debug_filename: str = "debug_size_estimation.png",
) -> int:
    """Estimate canvas grid pixel size and save a debug visualization image."""
    if img.size == 0 or img.shape[0] <= 0 or img.shape[1] <= 0:
        logger.warning(
            "Empty canvas image received; falling back to default pixel size (%d).",
            DEFAULT_FALLBACK_PIXEL_SIZE,
        )
        return DEFAULT_FALLBACK_PIXEL_SIZE

    debug_img = img.copy()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 0, 0, apertureSize=3)
    contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    square_sizes: list[int] = []
    square_contours: list[tuple[Any, int, int]] = []

    for cnt in contours:
        _, _, w, h = cv2.boundingRect(cnt)
        if h == 0:
            continue
        is_square_like = SQUARE_ASPECT_MIN <= w / h <= SQUARE_ASPECT_MAX
        is_right_size = min_size < w < max_size and min_size < h < max_size

        if is_square_like and is_right_size:
            square_sizes.append(w)
            square_contours.append((cnt, w, h))

    if len(square_sizes) < MIN_SQUARE_SAMPLES:
        logger.warning(
            "Could not find enough squares (%d < %d); falling back to default (%d).",
            len(square_sizes),
            MIN_SQUARE_SAMPLES,
            DEFAULT_FALLBACK_PIXEL_SIZE,
        )
        return DEFAULT_FALLBACK_PIXEL_SIZE

    sorted_sizes = sorted(square_sizes)
    preview_count = max(1, len(sorted_sizes) // 4)
    potential_previews = sorted_sizes[:preview_count]
    preview_median = statistics.median(potential_previews)
    expected_pixel_size = preview_median * PREVIEW_TO_PIXEL_RATIO
    pixel_min = expected_pixel_size * SQUARE_ASPECT_MIN
    pixel_max = expected_pixel_size * SQUARE_ASPECT_MAX

    pixel_sizes: list[int] = []
    for cnt, w, h in square_contours:
        x, y, _, _ = cv2.boundingRect(cnt)

        if w <= preview_median * PREVIEW_MAX_SCALE_FACTOR:
            cv2.rectangle(debug_img, (x, y), (x + w, y + h), (0, 255, 0), 1)
        elif pixel_min <= w <= pixel_max:
            cv2.rectangle(debug_img, (x, y), (x + w, y + h), (0, 0, 255), 1)
            pixel_sizes.append(w)
        else:
            cv2.rectangle(debug_img, (x, y), (x + w, y + h), (128, 128, 128), 1)

    if not pixel_sizes:
        logger.warning(
            "Could not find valid single pixel squares; using calculated size."
        )
        estimated_size = round(expected_pixel_size)
    else:
        estimated_size = round(statistics.median(pixel_sizes))

    saved_debug = save_bgr_image(debug_filename, debug_img)
    logger.debug("Size estimation debug image saved: %s", saved_debug)
    return max(1, int(estimated_size))


def find_pixels_to_paint(
    img: np.ndarray,
    target_color_bgr: tuple[int, int, int],
    pixel_size: int,
    tolerance: int = 1,
    debug_filename: str | None = None,
) -> list[tuple[int, int]]:
    """Scan a BGR canvas image on a fixed grid to find unpainted target pixels."""
    if img.size == 0 or pixel_size <= 0:
        return []

    h, w, _ = img.shape
    matches: list[tuple[int, int]] = []

    debug_img = img.copy() if debug_filename else None
    debug_counter = 0

    step = pixel_size + 1
    for y in range(0, h - pixel_size, step):
        for x in range(0, w - pixel_size, step):
            cx = x + pixel_size // 2
            cy = y + pixel_size // 2
            if cy >= h or cx >= w or y + SAMPLE_CORNER_OFFSET_PX >= h or x + SAMPLE_CORNER_OFFSET_PX >= w:
                continue

            preview_color = img[cy, cx]
            is_preview_match = all(
                abs(int(preview_color[i]) - target_color_bgr[i]) <= tolerance
                for i in range(3)
            )

            if is_preview_match:
                pixel_color = img[y + SAMPLE_CORNER_OFFSET_PX, x + SAMPLE_CORNER_OFFSET_PX]
                is_pixel_match = all(
                    abs(int(pixel_color[i]) - target_color_bgr[i]) <= tolerance
                    for i in range(3)
                )

                if not is_pixel_match:
                    matches.append((cx, cy))

                    if debug_img is not None and debug_counter < MAX_DEBUG_ANNOTATIONS:
                        cv2.rectangle(
                            debug_img,
                            (x, y),
                            (x + pixel_size, y + pixel_size),
                            (255, 255, 0),
                            1,
                        )
                        cv2.circle(debug_img, (cx, cy), 2, (255, 0, 0), -1)
                        cv2.circle(
                            debug_img,
                            (x + SAMPLE_CORNER_OFFSET_PX, y + SAMPLE_CORNER_OFFSET_PX),
                            2,
                            (0, 0, 255),
                            -1,
                        )
                        cv2.putText(
                            debug_img,
                            "PAINT",
                            (x, y - 5),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.4,
                            (0, 255, 0),
                            1,
                        )
                        debug_counter += 1

    if debug_img is not None and debug_filename:
        saved_scan = save_bgr_image(debug_filename, debug_img)
        logger.debug("Painting scan debug image saved: %s", saved_scan)

    return matches


def build_pixel_map(
    img: np.ndarray,
    pixel_size: int,
    preview_positions: list[tuple[int, int]],
) -> dict[tuple[int, int], dict[str, tuple[int, int, int]]]:
    """Build a map of detected pixel positions with their template preview and current BGR colors."""
    pixel_map: dict[tuple[int, int], dict[str, tuple[int, int, int]]] = {}
    if img.size == 0 or img.ndim < 3 or pixel_size <= 0:
        return pixel_map

    img_h, img_w = img.shape[:2]

    for preview_x, preview_y in preview_positions:
        pixel_x = preview_x - pixel_size // 2
        pixel_y = preview_y - pixel_size // 2
        sample_x = pixel_x + SAMPLE_CORNER_OFFSET_PX
        sample_y = pixel_y + SAMPLE_CORNER_OFFSET_PX

        if (
            0 <= pixel_x
            and 0 <= pixel_y
            and sample_x < img_w
            and sample_y < img_h
            and 0 <= preview_x < img_w
            and 0 <= preview_y < img_h
        ):
            preview_raw = img[preview_y, preview_x]
            pixel_raw = img[sample_y, sample_x]

            pixel_map[(preview_x, preview_y)] = {
                "preview_color": (
                    int(preview_raw[0]),
                    int(preview_raw[1]),
                    int(preview_raw[2]),
                ),
                "pixel_color": (
                    int(pixel_raw[0]),
                    int(pixel_raw[1]),
                    int(pixel_raw[2]),
                ),
            }

    return pixel_map


def get_preview_positions_from_estimation(
    img: np.ndarray,
    pixel_size: int,
) -> list[tuple[int, int]]:
    """Extract template preview center coordinates from the canvas screenshot."""
    _ = pixel_size
    if img.size == 0 or img.shape[0] <= 0 or img.shape[1] <= 0:
        return []

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 0, 0, apertureSize=3)
    contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    square_sizes: list[int] = []
    square_contours: list[tuple[Any, int, int]] = []

    for cnt in contours:
        _, _, w, h = cv2.boundingRect(cnt)
        if h == 0:
            continue
        is_square_like = SQUARE_ASPECT_MIN <= w / h <= SQUARE_ASPECT_MAX
        is_right_size = (
            MIN_CONTOUR_SIZE_PX < w < MAX_CONTOUR_SIZE_PX
            and MIN_CONTOUR_SIZE_PX < h < MAX_CONTOUR_SIZE_PX
        )

        if is_square_like and is_right_size:
            square_sizes.append(w)
            square_contours.append((cnt, w, h))

    if len(square_sizes) < MIN_SQUARE_SAMPLES:
        return []

    sorted_sizes = sorted(square_sizes)
    preview_count = max(1, len(sorted_sizes) // 4)
    potential_previews = sorted_sizes[:preview_count]
    preview_median = statistics.median(potential_previews)

    preview_positions: list[tuple[int, int]] = []
    for cnt, w, h in square_contours:
        if w <= preview_median * PREVIEW_MAX_SCALE_FACTOR:
            x, y, _, _ = cv2.boundingRect(cnt)
            center_x = x + w // 2
            center_y = y + h // 2
            preview_positions.append((center_x, center_y))

    return preview_positions
