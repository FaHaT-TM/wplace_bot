"""Screen capture utilities, region validation, and HiDPI coordinate scaling."""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from .exceptions import CaptureError, RegionError

MIN_REGION_DIMENSION: int = 10


class ScreenCapturer(Protocol):
    """Protocol abstracting screen size queries and region screenshots."""

    def screen_size(self) -> tuple[int, int] | None:
        """Return logical screen ``(width, height)`` or ``None`` if unavailable."""
        ...

    def screenshot(self, region: tuple[int, int, int, int]) -> Any:
        """Capture ``(left, top, width, height)`` and return an RGB image or array."""
        ...


class PyAutoGUIScreenCapturer:
    """Default screen capturer backed by ``pyautogui`` (lazily imported)."""

    def screen_size(self) -> tuple[int, int] | None:
        """Return logical primary/virtual screen dimensions from ``pyautogui``."""
        import pyautogui

        width, height = pyautogui.size()
        return (int(width), int(height))

    def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
        """Capture a region screenshot as an RGB ``numpy.ndarray``."""
        import pyautogui

        shot = pyautogui.screenshot(region=region)
        return np.asarray(shot)


def _is_strict_int(value: Any) -> bool:
    """Return ``True`` if ``value`` is an ``int`` and not a ``bool``."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_region(
    region: Any,
    *,
    name: str = "Region",
    screen_size: tuple[int, int] | None = None,
    min_dimension: int = MIN_REGION_DIMENSION,
) -> tuple[int, int, int, int]:
    """Validate a ``(left, top, width, height)`` screen region.

    Args:
        region: Candidate 4-element tuple or list of integers.
        name: Human-readable region label for error messages.
        screen_size: Optional ``(screen_width, screen_height)`` to verify bounds.
        min_dimension: Minimum allowed width and height in pixels.

    Returns:
        Normalized ``(left, top, width, height)`` integer tuple.

    Raises:
        RegionError: If ``region`` is missing, malformed, too small, or exceeds screen bounds.
    """
    if not isinstance(region, (tuple, list)) or len(region) != 4:
        raise RegionError(
            f"{name} is not selected or invalid (expected 4 coordinates, got {region!r})."
        )

    if not all(_is_strict_int(item) for item in region):
        raise RegionError(f"{name} coordinates must be integers (got {region!r}).")

    left, top, width, height = (
        int(region[0]),
        int(region[1]),
        int(region[2]),
        int(region[3]),
    )

    if left < 0 or top < 0:
        raise RegionError(
            f"{name} origin must be non-negative (got left={left}, top={top})."
        )

    if width < min_dimension or height < min_dimension:
        raise RegionError(
            f"{name} is too small ({width}x{height}); minimum size is "
            f"{min_dimension}x{min_dimension}."
        )

    if screen_size is not None:
        screen_w, screen_h = screen_size
        if screen_w > 0 and screen_h > 0 and (
            left + width > screen_w or top + height > screen_h
        ):
            raise RegionError(
                f"{name} ({left}, {top}, {width}, {height}) exceeds current screen "
                f"resolution ({screen_w}x{screen_h}). Please reselect the region."
            )

    return (left, top, width, height)


def validate_point_in_region(
    x: int,
    y: int,
    region: tuple[int, int, int, int],
    *,
    name: str = "Region",
    screen_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Verify that screen point ``(x, y)`` lies strictly within ``region`` and screen bounds.

    Args:
        x: Logical screen X coordinate.
        y: Logical screen Y coordinate.
        region: Validated ``(left, top, width, height)`` region tuple.
        name: Human-readable region label for error messages.
        screen_size: Optional ``(screen_width, screen_height)`` bounds.

    Returns:
        Validated ``(x, y)`` integer tuple.

    Raises:
        RegionError: If ``(x, y)`` falls outside ``region`` or ``screen_size``.
    """
    left, top, width, height = region
    if not (left <= x < left + width and top <= y < top + height):
        raise RegionError(
            f"Target point ({x}, {y}) is outside {name} ({left}, {top}, {width}, {height})."
        )

    if screen_size is not None:
        screen_w, screen_h = screen_size
        if screen_w > 0 and screen_h > 0 and not (0 <= x < screen_w and 0 <= y < screen_h):
            raise RegionError(
                f"Target point ({x}, {y}) is outside current screen ({screen_w}x{screen_h})."
            )

    return (x, y)


def compute_region_scale(
    image_shape: tuple[int, ...],
    region: tuple[int, int, int, int],
) -> tuple[float, float]:
    """Compute HiDPI/Retina pixel-to-logical scale factors ``(scale_x, scale_y)``.

    Args:
        image_shape: Numpy array shape ``(height, width, ...)``.
        region: Logical ``(left, top, width, height)`` region tuple.

    Returns:
        ``(scale_x, scale_y)`` ratio between captured image pixels and logical region size.

    Raises:
        CaptureError: If image dimensions are invalid or zero.
    """
    if len(image_shape) < 2 or image_shape[0] <= 0 or image_shape[1] <= 0:
        raise CaptureError(f"Captured image has invalid shape: {image_shape!r}.")

    img_h, img_w = int(image_shape[0]), int(image_shape[1])
    _, _, reg_w, reg_h = region
    if reg_w <= 0 or reg_h <= 0:
        raise RegionError(f"Region dimensions must be positive (got {reg_w}x{reg_h}).")

    return (img_w / reg_w, img_h / reg_h)


def image_offset_to_screen_point(
    offset_x: float,
    offset_y: float,
    region: tuple[int, int, int, int],
    scale: tuple[float, float] = (1.0, 1.0),
) -> tuple[int, int]:
    """Convert pixel offsets inside a captured region image to logical screen coordinates.

    Args:
        offset_x: Horizontal pixel coordinate inside the region screenshot.
        offset_y: Vertical pixel coordinate inside the region screenshot.
        region: Logical ``(left, top, width, height)`` region tuple.
        scale: ``(scale_x, scale_y)`` ratio from :func:`compute_region_scale`.

    Returns:
        Logical screen ``(x, y)`` coordinates suitable for ``pyautogui.click``.
    """
    scale_x = scale[0] if scale[0] > 0 else 1.0
    scale_y = scale[1] if scale[1] > 0 else 1.0
    screen_x = region[0] + round(offset_x / scale_x)
    screen_y = region[1] + round(offset_y / scale_y)
    return (int(screen_x), int(screen_y))


def get_screen(
    region: Any = None,
    *,
    capturer: ScreenCapturer | None = None,
) -> np.ndarray:
    """Capture a validated screen region as an RGB ``numpy.ndarray``.

    Args:
        region: ``(left, top, width, height)`` screen region to capture.
        capturer: Optional ``ScreenCapturer`` implementation for dependency injection.

    Returns:
        RGB image array of shape ``(H, W, 3)``.

    Raises:
        RegionError: If ``region`` is ``None``, malformed, or exceeds screen resolution.
        CaptureError: If taking the screenshot fails or returns an empty image.
    """
    if region is None:
        raise RegionError("Screen capture region cannot be None.")

    active_capturer: ScreenCapturer = (
        capturer if capturer is not None else PyAutoGUIScreenCapturer()
    )

    try:
        current_screen = active_capturer.screen_size()
    except Exception as exc:
        raise CaptureError(f"Failed to query screen dimensions: {exc}") from exc

    validated_region = validate_region(region, screen_size=current_screen)

    try:
        raw_shot = active_capturer.screenshot(validated_region)
        image_rgb = np.asarray(raw_shot)
    except Exception as exc:
        raise CaptureError(
            f"Failed to capture screen region {validated_region}: {exc}"
        ) from exc

    if image_rgb.ndim < 2 or image_rgb.shape[0] <= 0 or image_rgb.shape[1] <= 0:
        raise CaptureError(
            f"Captured empty image for region {validated_region} (shape={image_rgb.shape!r})."
        )

    return image_rgb
