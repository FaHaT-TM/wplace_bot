"""Automatic synchronization of Pixel Limit from wplace.live on-screen pixel counter."""

from __future__ import annotations

import functools
import logging
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import MAX_PIXEL_LIMIT, MIN_PIXEL_LIMIT, clamp_pixel_limit
from .exceptions import CaptureError, RegionError
from .image_io import save_rgb_image
from .screen_capture import (
    MonitorInfo,
    PyAutoGUIScreenCapturer,
    ScreenCapturer,
    detect_monitors,
    get_screen,
    is_region_covered_by_monitors,
)

logger = logging.getLogger(__name__)

# Preset screen regions (left, top, width, height) converted from (x1, y1)..(x2, y2):
# Monitor 1 (1680x1050 primary, left=0):
#   Before painting (idle): (845, 1000) -> (925, 1015)
#   Painting mode:          (1405, 985) -> (1475, 1000)
MON1_IDLE_COUNTER_RECT: tuple[int, int, int, int] = (845, 1000, 80, 15)
MON1_PAINT_COUNTER_RECT: tuple[int, int, int, int] = (1405, 985, 70, 15)

# Monitor 2 (1920x1080 extended right of Monitor 1, left=1680, virtual width=3600):
#   Before painting (idle): (2650, 1000) -> (2725, 1010)
#   Painting mode:          (3205, 980)  -> (3275, 990)
MON2_IDLE_COUNTER_RECT: tuple[int, int, int, int] = (2650, 1000, 75, 10)
MON2_PAINT_COUNTER_RECT: tuple[int, int, int, int] = (3205, 980, 70, 10)

MONITOR_2_X_THRESHOLD: int = 1680
COUNTER_PAD_X_PX: int = 6
COUNTER_PAD_Y_PX: int = 4
MIN_COUNTER_DIMENSION: int = 6
DEFAULT_COUNTER_DEBUG_IMAGE: str = "debug_pixel_counter.png"

UPSCALE_FACTOR: int = 6
MIN_CONTRAST_SPAN: int = 25
MAX_BORDER_FILL_RATIO: float = 0.65
GLYPH_NORM_W: int = 28
GLYPH_NORM_H: int = 40
MIN_GLYPH_SCORE: float = 0.42
MAX_VALID_COUNTER_VALUE: int = 50000

_COUNTER_SLASH_RE = re.compile(r"(\d{1,5})\s*/\s*(\d{1,5})")


@dataclass(frozen=True, slots=True)
class SiteCounterReading:
    """Parsed available and maximum pixel charges read from the wplace.live UI."""

    available: int
    maximum: int
    raw_text: str
    source_label: str
    region: tuple[int, int, int, int]

    def effective_limit(
        self,
        min_limit: int = MIN_PIXEL_LIMIT,
        max_limit: int = MAX_PIXEL_LIMIT,
    ) -> int:
        """Clamp ``available`` into valid bot pixel-limit bounds ``[min_limit, max_limit]``."""
        return clamp_pixel_limit(
            self.available,
            default=min_limit,
            min_limit=min_limit,
            max_limit=max_limit,
        )


def parse_counter_text(text: str) -> tuple[int, int] | None:
    """Extract ``(available, maximum)`` integer pair from an ``'X / Y'`` counter string."""
    if not text:
        return None

    normalized = (
        text.replace("\\", "/")
        .replace("|", "/")
        .replace("O", "0")
        .replace("o", "0")
    )
    match = _COUNTER_SLASH_RE.search(normalized)
    if not match:
        return None

    available = int(match.group(1))
    maximum = int(match.group(2))
    if available < 0 or maximum <= 0:
        return None
    if available > MAX_VALID_COUNTER_VALUE or maximum > MAX_VALID_COUNTER_VALUE:
        return None
    return (available, maximum)


def _discover_font_paths() -> list[str]:
    """Return existing system sans-serif TrueType font paths for glyph template generation."""
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    return [path for path in candidates if os.path.exists(path)]


@dataclass(frozen=True, slots=True)
class _GlyphTemplate:
    """Normalized binary bitmap and aspect ratio for a single reference character."""

    char: str
    bitmap: np.ndarray  # float32 (GLYPH_NORM_H, GLYPH_NORM_W) in [0.0, 1.0]
    aspect: float


def _normalize_glyph_mask(mask: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Tight-crop a binary glyph mask (0/255) and resize to ``(GLYPH_NORM_W, GLYPH_NORM_H)``."""
    ys, xs = np.where(mask > 0)
    if ys.size == 0 or xs.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    cropped = mask[y0:y1, x0:x1]
    h, w = cropped.shape[:2]
    if h <= 0 or w <= 0:
        return None
    resized = cv2.resize(
        cropped, (GLYPH_NORM_W, GLYPH_NORM_H), interpolation=cv2.INTER_AREA
    )
    binary = (resized >= 110).astype(np.float32)
    return (binary, float(w) / float(h))


@functools.lru_cache(maxsize=1)
def _get_template_bank() -> tuple[_GlyphTemplate, ...]:
    """Build and cache multi-font reference templates for ``'0123456789/'``."""
    chars = "0123456789/"
    fonts: list[Any] = []
    for path in _discover_font_paths():
        for font_px in (28, 36):
            try:
                fonts.append(ImageFont.truetype(path, size=font_px))
            except OSError:
                continue

    try:
        fonts.append(ImageFont.load_default(size=32))
    except TypeError:
        fonts.append(ImageFont.load_default())

    templates: list[_GlyphTemplate] = []
    kernel = np.ones((2, 2), dtype=np.uint8)

    for font in fonts:
        for ch in chars:
            img = Image.new("L", (96, 96), color=0)
            draw = ImageDraw.Draw(img)
            draw.text((16, 16), ch, fill=255, font=font)
            arr = np.array(img, dtype=np.uint8)
            _, base_mask = cv2.threshold(arr, 100, 255, cv2.THRESH_BINARY)

            for variant in (base_mask, cv2.dilate(base_mask, kernel, iterations=1)):
                norm_res = _normalize_glyph_mask(variant)
                if norm_res is not None:
                    bmp, aspect = norm_res
                    templates.append(_GlyphTemplate(char=ch, bitmap=bmp, aspect=aspect))

    return tuple(templates)


def _analyze_glyph_topology(mask: np.ndarray) -> tuple[int, float | None, bool]:
    """Return ``(hole_count, mean_hole_rel_y, is_slash_like)`` for a tight glyph mask."""
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        return (0, None, False)
    cropped = mask[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]
    h, w = cropped.shape[:2]

    padded = cv2.copyMakeBorder(cropped, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=0)
    contours, hierarchy = cv2.findContours(
        padded, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    hole_ys: list[float] = []
    if hierarchy is not None and len(contours) > 0:
        min_hole_area = max(4.0, float(h * w) * 0.018)
        for idx, h_info in enumerate(hierarchy[0]):
            parent_idx = int(h_info[3])
            if parent_idx >= 0:
                area = cv2.contourArea(contours[idx])
                if area >= min_hole_area:
                    moments = cv2.moments(contours[idx])
                    if moments["m00"] > 0:
                        cy = (moments["m01"] / moments["m00"]) - 4.0
                        hole_ys.append(cy / max(1.0, float(h)))

    # Check forward-slash geometry: top-right to bottom-left diagonal with 0 holes
    is_slash_like = False
    if len(hole_ys) == 0 and h >= 8 and 0.20 <= (w / h) <= 0.72:
        q = max(2, h // 4)
        top_xs = np.where(cropped[:q, :] > 0)[1]
        bot_xs = np.where(cropped[h - q :, :] > 0)[1]
        top_left_fill = float(np.mean(cropped[: q, : max(1, w // 3)] > 0))
        bot_right_fill = float(np.mean(cropped[h - q :, w - max(1, w // 3) :] > 0))
        if (
            top_xs.size > 0
            and bot_xs.size > 0
            and (float(top_xs.mean()) - float(bot_xs.mean())) >= 0.22 * w
            and top_left_fill < 0.12
            and bot_right_fill < 0.12
        ):
            is_slash_like = True

    mean_y = float(np.mean(hole_ys)) if hole_ys else None
    return (len(hole_ys), mean_y, is_slash_like)


def _classify_glyph(mask: np.ndarray) -> tuple[str, float]:
    """Classify a single segmented binary glyph into one of ``'0123456789/'``."""
    norm_res = _normalize_glyph_mask(mask)
    if norm_res is None:
        return ("", 0.0)
    bmp, aspect = norm_res
    holes, hole_y, is_slash_like = _analyze_glyph_topology(mask)

    if is_slash_like:
        return ("/", 0.96)

    best_by_char: dict[str, float] = {}
    bmp_sum = float(bmp.sum())

    for tmpl in _get_template_bank():
        intersection = float(np.minimum(bmp, tmpl.bitmap).sum())
        union = bmp_sum + float(tmpl.bitmap.sum()) - intersection
        iou = intersection / max(1.0, union)
        aspect_diff = abs(aspect - tmpl.aspect)
        score = iou - 0.35 * aspect_diff

        ch = tmpl.char
        # Topological priors to separate visually similar small glyphs
        if ch == "8":
            score += 0.10 if holes >= 2 else -0.08
        elif ch == "0":
            if holes == 1 and hole_y is not None and 0.36 <= hole_y <= 0.64:
                score += 0.08
            elif holes == 0 or holes >= 2:
                score -= 0.07
        elif ch == "6":
            if holes == 1 and hole_y is not None and hole_y > 0.52:
                score += 0.08
            elif holes == 1 and hole_y is not None and hole_y < 0.44:
                score -= 0.10
        elif ch == "9":
            if holes == 1 and hole_y is not None and hole_y < 0.48:
                score += 0.08
            elif holes == 1 and hole_y is not None and hole_y > 0.56:
                score -= 0.10
        elif ch in "12357/" and holes >= 2:
            score -= 0.12

        if score > best_by_char.get(ch, -1.0):
            best_by_char[ch] = score

    best_char, best_score = max(best_by_char.items(), key=lambda kv: kv[1])
    return (best_char, best_score)


def _split_wide_component(submask: np.ndarray, x_offset: int, y_offset: int) -> list[tuple[int, int, int, int]]:
    """Recursively split horizontally touching digit pairs along vertical projection valleys."""
    h, w = submask.shape[:2]
    if h <= 0 or w <= 0:
        return []
    if (w / float(h)) <= 1.02 or w < 16:
        return [(x_offset, y_offset, w, h)]

    col_sums = (submask > 0).sum(axis=0)
    margin = max(4, int(w * 0.24))
    if w - 2 * margin <= 2:
        return [(x_offset, y_offset, w, h)]

    interior = col_sums[margin : w - margin]
    min_idx = int(np.argmin(interior)) + margin
    if int(col_sums[min_idx]) > int(0.38 * h):
        return [(x_offset, y_offset, w, h)]

    left_boxes = _split_wide_component(submask[:, :min_idx], x_offset, y_offset)
    right_boxes = _split_wide_component(
        submask[:, min_idx + 1 :], x_offset + min_idx + 1, y_offset
    )
    return left_boxes + right_boxes


def _recognize_polarity_mask(mask: np.ndarray) -> tuple[str, float]:
    """Segment and classify left-to-right characters from one binary foreground polarity."""
    img_h, img_w = mask.shape[:2]
    border_pixels = np.concatenate(
        [mask[0, :], mask[-1, :], mask[:, 0], mask[:, -1]]
    )
    if float(np.mean(border_pixels > 0)) > MAX_BORDER_FILL_RATIO:
        return ("", 0.0)

    num_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    raw_boxes: list[tuple[int, int, int, int, float]] = []
    min_h = max(8, int(img_h * 0.30))
    min_area = max(18, int(img_h * img_w * 0.002))

    for idx in range(1, num_labels):
        x = int(stats[idx, cv2.CC_STAT_LEFT])
        y = int(stats[idx, cv2.CC_STAT_TOP])
        w = int(stats[idx, cv2.CC_STAT_WIDTH])
        h = int(stats[idx, cv2.CC_STAT_HEIGHT])
        area = int(stats[idx, cv2.CC_STAT_AREA])
        cy = float(centroids[idx][1])

        if area < min_area or h < min_h or w >= int(img_w * 0.85):
            continue
        if h >= int(img_h * 0.98) and (y == 0 or y + h >= img_h):
            continue
        raw_boxes.append((x, y, w, h, cy))

    if not raw_boxes:
        return ("", 0.0)

    median_cy = float(np.median([b[4] for b in raw_boxes]))
    median_h = float(np.median([b[3] for b in raw_boxes]))
    line_boxes: list[tuple[int, int, int, int]] = []
    for x, y, w, h, cy in raw_boxes:
        if abs(cy - median_cy) <= 0.45 * median_h and h >= 0.55 * median_h:
            line_boxes.extend(_split_wide_component(mask[y : y + h, x : x + w], x, y))

    if not line_boxes:
        return ("", 0.0)

    line_boxes.sort(key=lambda b: b[0])
    chars: list[str] = []
    scores: list[float] = []
    for x, y, w, h in line_boxes:
        glyph_mask = mask[y : y + h, x : x + w]
        ch, sc = _classify_glyph(glyph_mask)
        if ch and sc >= MIN_GLYPH_SCORE:
            chars.append(ch)
            scores.append(sc)

    if not chars:
        return ("", 0.0)
    return ("".join(chars), float(np.mean(scores)))


def _try_winocr(img_rgb: np.ndarray) -> str | None:
    """Attempt native Windows 10/11 OCR via ``winocr`` if running on Windows."""
    if sys.platform != "win32":
        return None
    try:
        import winocr  # type: ignore[import-not-found]
    except ImportError:
        return None

    upscaled = cv2.resize(
        img_rgb,
        (img_rgb.shape[1] * 5, img_rgb.shape[0] * 5),
        interpolation=cv2.INTER_CUBIC,
    )
    padded = cv2.copyMakeBorder(
        upscaled, 24, 24, 24, 24, cv2.BORDER_REPLICATE
    )
    for variant in (padded, 255 - padded):
        try:
            pil_img = Image.fromarray(variant)
            res = winocr.recognize_pil_sync(pil_img, "en")
            text = str(getattr(res, "text", "") or "").strip()
            if parse_counter_text(text) is not None:
                return text
        except Exception:
            logger.debug("winocr recognition pass failed", exc_info=True)
    return None


def recognize_counter_image(
    img_rgb: np.ndarray,
    *,
    allow_winocr: bool = True,
) -> str:
    """Recognize an ``'X / Y'`` counter string from a small RGB screenshot crop."""
    if img_rgb is None or img_rgb.size == 0 or img_rgb.ndim < 2:
        return ""

    if img_rgb.ndim == 3:
        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_rgb

    if int(gray.max()) - int(gray.min()) < MIN_CONTRAST_SPAN:
        return ""

    up_gray = cv2.resize(
        gray,
        (gray.shape[1] * UPSCALE_FACTOR, gray.shape[0] * UPSCALE_FACTOR),
        interpolation=cv2.INTER_CUBIC,
    )
    norm_gray = cv2.normalize(
        up_gray, np.empty_like(up_gray), 0, 255, cv2.NORM_MINMAX
    )
    _, mask_light_otsu = cv2.threshold(
        norm_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    _, mask_dark_otsu = cv2.threshold(
        norm_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    _, mask_light_mid = cv2.threshold(norm_gray, 120, 255, cv2.THRESH_BINARY)
    _, mask_dark_mid = cv2.threshold(norm_gray, 135, 255, cv2.THRESH_BINARY_INV)

    candidates: list[tuple[bool, float, str]] = []
    for mask in (mask_light_otsu, mask_dark_otsu, mask_light_mid, mask_dark_mid):
        text, avg_score = _recognize_polarity_mask(mask)
        if text:
            is_valid_pair = parse_counter_text(text) is not None
            candidates.append((is_valid_pair, avg_score, text))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if candidates and candidates[0][0] and candidates[0][1] >= 0.78:
        return candidates[0][2]

    if allow_winocr and img_rgb.ndim == 3:
        win_text = _try_winocr(img_rgb)
        if win_text:
            return win_text

    if not candidates:
        return ""
    return candidates[0][2]


def _preferred_monitor_number(data_manager: Any) -> int | None:
    """Infer whether the user is working on Monitor 1 or Monitor 2 from saved regions."""
    if data_manager is None:
        return None
    for region_attr in ("canvas_region", "palette_region"):
        reg = getattr(data_manager, region_attr, None)
        if isinstance(reg, (tuple, list)) and len(reg) == 4:
            try:
                center_x = int(reg[0]) + int(reg[2]) // 2
                return 2 if center_x >= MONITOR_2_X_THRESHOLD else 1
            except (TypeError, ValueError):
                continue
    return None


def _build_candidate_regions(
    phase: str,
    preferred_monitor: int | None,
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Order preset counter regions by preferred monitor and application lifecycle phase."""
    mon_rects: dict[int, dict[str, tuple[str, tuple[int, int, int, int]]]] = {
        1: {
            "idle": ("Monitor 1 (before painting)", MON1_IDLE_COUNTER_RECT),
            "paint": ("Monitor 1 (painting mode)", MON1_PAINT_COUNTER_RECT),
        },
        2: {
            "idle": ("Monitor 2 (before painting)", MON2_IDLE_COUNTER_RECT),
            "paint": ("Monitor 2 (painting mode)", MON2_PAINT_COUNTER_RECT),
        },
    }

    mode_order = ("idle", "paint") if phase == "startup" else ("paint", "idle")
    mon_order = [2, 1] if preferred_monitor == 2 else [1, 2]

    ordered: list[tuple[str, tuple[int, int, int, int]]] = []
    for mon_num in mon_order:
        for mode_key in mode_order:
            ordered.append(mon_rects[mon_num][mode_key])
    return ordered


def _expand_rect_within_monitors(
    rect: tuple[int, int, int, int],
    monitors: Sequence[MonitorInfo] | None,
    screen_size: tuple[int, int] | None,
) -> tuple[int, int, int, int] | None:
    """Add a small vertical/horizontal pad around ``rect`` if covered by displays."""
    left, top, width, height = rect
    padded = (
        left - COUNTER_PAD_X_PX,
        top - COUNTER_PAD_Y_PX,
        width + 2 * COUNTER_PAD_X_PX,
        height + 2 * COUNTER_PAD_Y_PX,
    )
    if monitors:
        if is_region_covered_by_monitors(padded, monitors):
            return padded
        return None
    if screen_size is not None:
        sw, sh = screen_size
        if (
            padded[0] >= 0
            and padded[1] >= 0
            and padded[0] + padded[2] <= sw
            and padded[1] + padded[3] <= sh
        ):
            return padded
    return None


def read_site_pixel_counter(
    data_manager: Any = None,
    *,
    phase: str = "manual",
    capturer: ScreenCapturer | None = None,
    debug_image_path: str | None = DEFAULT_COUNTER_DEBUG_IMAGE,
    allow_winocr: bool = True,
) -> SiteCounterReading | None:
    """Capture and parse the on-screen wplace.live pixel counter across preset regions."""
    try:
        active_capturer: ScreenCapturer = (
            capturer if capturer is not None else PyAutoGUIScreenCapturer()
        )
        monitors_fn = getattr(active_capturer, "monitors", None)
        monitors: Sequence[MonitorInfo] | None = (
            monitors_fn() if callable(monitors_fn) else detect_monitors()
        )
        screen_size: tuple[int, int] | None = (
            None if monitors else active_capturer.screen_size()
        )
    except (CaptureError, ImportError, KeyError, OSError, RuntimeError) as exc:
        logger.debug("Display capture unavailable for site counter sync: %s", exc)
        return None

    preferred_mon = _preferred_monitor_number(data_manager)
    candidates = _build_candidate_regions(phase=phase, preferred_monitor=preferred_mon)

    for label, base_rect in candidates:
        if monitors and not is_region_covered_by_monitors(base_rect, monitors):
            continue
        if (
            not monitors
            and screen_size is not None
            and (
                base_rect[0] < 0
                or base_rect[1] < 0
                or base_rect[0] + base_rect[2] > screen_size[0]
                or base_rect[1] + base_rect[3] > screen_size[1]
            )
        ):
            continue

        padded_rect = _expand_rect_within_monitors(base_rect, monitors, screen_size)
        rects_to_try = [base_rect]
        if padded_rect is not None and padded_rect != base_rect:
            rects_to_try.append(padded_rect)

        for try_rect in rects_to_try:
            try:
                crop_rgb = get_screen(
                    try_rect,
                    capturer=active_capturer,
                    min_dimension=MIN_COUNTER_DIMENSION,
                )
            except (CaptureError, RegionError, OSError, RuntimeError):
                continue

            raw_text = recognize_counter_image(crop_rgb, allow_winocr=allow_winocr)
            parsed = parse_counter_text(raw_text)
            if parsed is not None:
                available, maximum = parsed
                if debug_image_path:
                    try:
                        save_rgb_image(debug_image_path, crop_rgb)
                    except OSError:
                        logger.debug(
                            "Could not save counter debug image to %s",
                            debug_image_path,
                            exc_info=True,
                        )
                logger.info(
                    "Synced pixel counter from %s: %d/%d (phase=%s)",
                    label,
                    available,
                    maximum,
                    phase,
                )
                return SiteCounterReading(
                    available=available,
                    maximum=maximum,
                    raw_text=raw_text,
                    source_label=label,
                    region=try_rect,
                )

    logger.debug("No valid X/Y pixel counter detected on screen (phase=%s).", phase)
    return None
