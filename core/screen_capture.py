"""Screen capture utilities, multi-monitor topology, region validation, and HiDPI scaling."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .exceptions import CaptureError, RegionError

logger = logging.getLogger(__name__)

MIN_REGION_DIMENSION: int = 10
XRANDR_TIMEOUT_SECONDS: float = 1.0

# Windows User32 system metric constants for virtual screen
SM_XVIRTUALSCREEN: int = 76
SM_YVIRTUALSCREEN: int = 77
SM_CXVIRTUALSCREEN: int = 78
SM_CYVIRTUALSCREEN: int = 79
MONITORINFOF_PRIMARY: int = 1
CCHDEVICENAME: int = 32

_XRANDR_LINE_RE = re.compile(
    r"^(?P<name>\S+)\s+connected(?:\s+(?P<primary>primary))?\s+"
    r"(?P<w>\d+)x(?P<h>\d+)(?P<x>[+-]\d+)(?P<y>[+-]\d+)"
)


@dataclass(frozen=True, slots=True)
class MonitorInfo:
    """Logical screen geometry for a single connected display monitor."""

    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool = False
    name: str = ""

    @property
    def right(self) -> int:
        """Return exclusive right X coordinate ``left + width``."""
        return self.left + self.width

    @property
    def bottom(self) -> int:
        """Return exclusive bottom Y coordinate ``top + height``."""
        return self.top + self.height

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """Return ``(left, top, width, height)`` tuple."""
        return (self.left, self.top, self.width, self.height)

    def contains_point(self, x: int, y: int) -> bool:
        """Return ``True`` if point ``(x, y)`` lies inside this monitor."""
        return self.left <= x < self.right and self.top <= y < self.bottom

    def intersects_region(self, region: tuple[int, int, int, int]) -> bool:
        """Return ``True`` if ``region`` overlaps this monitor with positive area."""
        rx, ry, rw, rh = region
        return (
            rx < self.right
            and rx + rw > self.left
            and ry < self.bottom
            and ry + rh > self.top
        )


class ScreenCapturer(Protocol):
    """Protocol abstracting screen size queries and region screenshots."""

    def screen_size(self) -> tuple[int, int] | None:
        """Return logical primary screen ``(width, height)`` or ``None`` if unavailable."""
        ...

    def screenshot(self, region: tuple[int, int, int, int]) -> Any:
        """Capture ``(left, top, width, height)`` and return an RGB image or array."""
        ...


def parse_xrandr_monitors(output: str) -> list[MonitorInfo]:
    """Parse ``xrandr --query`` output into sorted :class:`MonitorInfo` entries."""
    raw_items: list[tuple[bool, int, int, int, int, str]] = []
    for line in output.splitlines():
        match = _XRANDR_LINE_RE.match(line.strip())
        if not match:
            continue
        width = int(match.group("w"))
        height = int(match.group("h"))
        if width <= 0 or height <= 0:
            continue
        left = int(match.group("x"))
        top = int(match.group("y"))
        is_primary = match.group("primary") == "primary"
        name = match.group("name")
        raw_items.append((is_primary, left, top, width, height, name))

    raw_items.sort(key=lambda item: (not item[0], item[1], item[2]))
    return [
        MonitorInfo(
            index=idx,
            left=left,
            top=top,
            width=width,
            height=height,
            is_primary=is_primary,
            name=name,
        )
        for idx, (is_primary, left, top, width, height, name) in enumerate(
            raw_items, start=1
        )
    ]


def _detect_windows_monitors() -> list[MonitorInfo]:
    """Enumerate connected monitors on Windows via User32 ``EnumDisplayMonitors``."""
    if sys.platform != "win32":
        return []

    user32 = getattr(ctypes, "windll", None)
    if user32 is None or not hasattr(user32, "user32"):
        return []

    win_user32 = user32.user32

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", ctypes.c_ulong),
            ("szDevice", ctypes.c_wchar * CCHDEVICENAME),
        ]

    raw_items: list[tuple[bool, int, int, int, int, str]] = []

    try:
        monitor_enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_RECT),
            ctypes.c_ssize_t,
        )

        def _callback(
            hmonitor: int,
            _hdc: int,
            _lprect: Any,
            _lparam: int,
        ) -> int:
            info = _MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if win_user32.GetMonitorInfoW(ctypes.c_void_p(hmonitor), ctypes.byref(info)):
                left = int(info.rcMonitor.left)
                top = int(info.rcMonitor.top)
                width = int(info.rcMonitor.right - info.rcMonitor.left)
                height = int(info.rcMonitor.bottom - info.rcMonitor.top)
                if width > 0 and height > 0:
                    is_primary = bool(info.dwFlags & MONITORINFOF_PRIMARY)
                    raw_items.append(
                        (is_primary, left, top, width, height, str(info.szDevice))
                    )
            return 1

        cb_instance = monitor_enum_proc(_callback)
        win_user32.EnumDisplayMonitors(None, None, cb_instance, 0)
    except (AttributeError, OSError, ValueError):
        logger.debug("EnumDisplayMonitors failed on Windows; falling back to virtual screen metrics.", exc_info=True)

    if raw_items:
        raw_items.sort(key=lambda item: (not item[0], item[1], item[2]))
        return [
            MonitorInfo(
                index=idx,
                left=left,
                top=top,
                width=width,
                height=height,
                is_primary=is_primary,
                name=name,
            )
            for idx, (is_primary, left, top, width, height, name) in enumerate(
                raw_items, start=1
            )
        ]

    try:
        vx = int(win_user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        vy = int(win_user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        vw = int(win_user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        vh = int(win_user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if vw > 0 and vh > 0:
            return [
                MonitorInfo(
                    index=1,
                    left=vx,
                    top=vy,
                    width=vw,
                    height=vh,
                    is_primary=True,
                    name="VirtualScreen",
                )
            ]
    except (AttributeError, OSError):
        logger.debug("GetSystemMetrics failed on Windows.", exc_info=True)

    return []


def _detect_macos_monitors() -> list[MonitorInfo]:
    """Enumerate active displays on macOS via CoreGraphics when available."""
    if sys.platform != "darwin":
        return []

    cg_path = ctypes.util.find_library("CoreGraphics")
    if not cg_path:
        return []

    try:
        cg = ctypes.cdll.LoadLibrary(cg_path)

        class _CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        class _CGSize(ctypes.Structure):
            _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

        class _CGRect(ctypes.Structure):
            _fields_ = [("origin", _CGPoint), ("size", _CGSize)]

        max_displays = 16
        display_ids = (ctypes.c_uint32 * max_displays)()
        display_count = ctypes.c_uint32(0)

        err = cg.CGGetActiveDisplayList(
            max_displays, display_ids, ctypes.byref(display_count)
        )
        if err != 0 or display_count.value == 0:
            return []

        cg.CGDisplayBounds.restype = _CGRect
        cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        main_id = int(cg.CGMainDisplayID())

        raw_items: list[tuple[bool, int, int, int, int, str]] = []
        for i in range(int(display_count.value)):
            did = int(display_ids[i])
            rect = cg.CGDisplayBounds(did)
            width = round(rect.size.width)
            height = round(rect.size.height)
            if width <= 0 or height <= 0:
                continue
            left = round(rect.origin.x)
            top = round(rect.origin.y)
            is_primary = did == main_id
            raw_items.append((is_primary, left, top, width, height, f"Display-{did}"))

        raw_items.sort(key=lambda item: (not item[0], item[1], item[2]))
        return [
            MonitorInfo(
                index=idx,
                left=left,
                top=top,
                width=width,
                height=height,
                is_primary=is_primary,
                name=name,
            )
            for idx, (is_primary, left, top, width, height, name) in enumerate(
                raw_items, start=1
            )
        ]
    except (AttributeError, OSError, ValueError):
        logger.debug("CoreGraphics monitor detection failed on macOS.", exc_info=True)
        return []


def _detect_linux_monitors() -> list[MonitorInfo]:
    """Enumerate connected X11 displays on Linux via ``xrandr --query``."""
    if not sys.platform.startswith("linux"):
        return []
    if not os.environ.get("DISPLAY") or not shutil.which("xrandr"):
        return []

    try:
        completed = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True,
            text=True,
            timeout=XRANDR_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout:
            return parse_xrandr_monitors(completed.stdout)
    except (OSError, subprocess.SubprocessError):
        logger.debug("xrandr monitor query failed.", exc_info=True)
    return []


def detect_monitors() -> list[MonitorInfo]:
    """Detect all active monitors across Windows, macOS, and Linux with safe fallback."""
    if sys.platform == "win32":
        monitors = _detect_windows_monitors()
    elif sys.platform == "darwin":
        monitors = _detect_macos_monitors()
    else:
        monitors = _detect_linux_monitors()

    if monitors:
        return monitors

    try:
        import pyautogui

        width, height = pyautogui.size()
        if int(width) > 0 and int(height) > 0:
            return [
                MonitorInfo(
                    index=1,
                    left=0,
                    top=0,
                    width=int(width),
                    height=int(height),
                    is_primary=True,
                    name="Primary",
                )
            ]
    except (ImportError, OSError, RuntimeError, AttributeError):
        logger.debug("Fallback primary screen query unavailable.", exc_info=True)

    return []


def compute_virtual_screen_bounds(
    monitors: Sequence[MonitorInfo],
) -> tuple[int, int, int, int]:
    """Compute bounding rectangle ``(left, top, width, height)`` across all ``monitors``."""
    if not monitors:
        raise RegionError("No connected monitors available to compute virtual screen bounds.")

    min_left = min(m.left for m in monitors)
    min_top = min(m.top for m in monitors)
    max_right = max(m.right for m in monitors)
    max_bottom = max(m.bottom for m in monitors)
    return (min_left, min_top, max_right - min_left, max_bottom - min_top)


def is_region_covered_by_monitors(
    region: tuple[int, int, int, int],
    monitors: Sequence[MonitorInfo],
) -> bool:
    """Return ``True`` iff every pixel of ``region`` lies on at least one connected monitor."""
    if not monitors:
        return False

    left, top, width, height = region
    if width <= 0 or height <= 0:
        return False

    uncovered: list[tuple[int, int, int, int]] = [
        (left, top, left + width, top + height)
    ]

    for mon in monitors:
        mx1, my1, mx2, my2 = mon.left, mon.top, mon.right, mon.bottom
        next_uncovered: list[tuple[int, int, int, int]] = []

        for rx1, ry1, rx2, ry2 in uncovered:
            if rx2 <= mx1 or rx1 >= mx2 or ry2 <= my1 or ry1 >= my2:
                next_uncovered.append((rx1, ry1, rx2, ry2))
                continue

            if rx1 < mx1:
                next_uncovered.append((rx1, ry1, mx1, ry2))
            if rx2 > mx2:
                next_uncovered.append((mx2, ry1, rx2, ry2))

            ox1 = max(rx1, mx1)
            ox2 = min(rx2, mx2)
            if ry1 < my1:
                next_uncovered.append((ox1, ry1, ox2, my1))
            if ry2 > my2:
                next_uncovered.append((ox1, my2, ox2, ry2))

        uncovered = next_uncovered
        if not uncovered:
            return True

    return not uncovered


def is_point_on_monitors(
    x: int,
    y: int,
    monitors: Sequence[MonitorInfo],
) -> bool:
    """Return ``True`` if screen coordinate ``(x, y)`` lies on any connected monitor."""
    return any(mon.contains_point(x, y) for mon in monitors)


def find_monitors_for_region(
    region: tuple[int, int, int, int],
    monitors: Sequence[MonitorInfo],
) -> list[MonitorInfo]:
    """Return connected monitors intersecting ``region``, ordered by monitor index."""
    return [
        mon
        for mon in sorted(monitors, key=lambda item: item.index)
        if mon.intersects_region(region)
    ]


def format_monitor_summary(
    region: tuple[int, int, int, int],
    monitors: Sequence[MonitorInfo],
) -> str:
    """Format a concise human-readable monitor badge (e.g. ``'Monitor 2'``)."""
    matched = find_monitors_for_region(region, monitors)
    if not matched:
        return ""
    if len(matched) == 1:
        return f"Monitor {matched[0].index}"
    joined = ", ".join(str(mon.index) for mon in matched)
    return f"Monitors {joined}"


class PyAutoGUIScreenCapturer:
    """Default multi-monitor screen capturer backed by ``PIL.ImageGrab`` and ``pyautogui``."""

    def monitors(self) -> list[MonitorInfo]:
        """Return all currently connected monitors."""
        return detect_monitors()

    def screen_size(self) -> tuple[int, int] | None:
        """Return logical primary screen dimensions from ``pyautogui``."""
        import pyautogui

        width, height = pyautogui.size()
        return (int(width), int(height))

    def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
        """Capture a region screenshot across any connected monitor as an RGB ``numpy.ndarray``."""
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)

        try:
            from PIL import ImageGrab

            try:
                shot = ImageGrab.grab(bbox=bbox, all_screens=True)
            except TypeError:
                shot = ImageGrab.grab(bbox=bbox)

            if getattr(shot, "mode", "RGB") != "RGB":
                shot = shot.convert("RGB")
            return np.asarray(shot)
        except (ImportError, NotImplementedError, OSError, ValueError):
            logger.debug(
                "ImageGrab multi-screen capture unavailable for %s; falling back to pyautogui.",
                region,
                exc_info=True,
            )

        import pyautogui

        fallback_shot = pyautogui.screenshot(region=region)
        if hasattr(fallback_shot, "mode") and fallback_shot.mode != "RGB":
            fallback_shot = fallback_shot.convert("RGB")
        return np.asarray(fallback_shot)


def _is_strict_int(value: Any) -> bool:
    """Return ``True`` if ``value`` is an ``int`` and not a ``bool``."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_region(
    region: Any,
    *,
    name: str = "Region",
    screen_size: tuple[int, int] | None = None,
    virtual_bounds: tuple[int, int, int, int] | None = None,
    monitors: Sequence[MonitorInfo] | None = None,
    allow_negative_origin: bool = False,
    min_dimension: int = MIN_REGION_DIMENSION,
) -> tuple[int, int, int, int]:
    """Validate a ``(left, top, width, height)`` screen region across single or multiple monitors.

    Args:
        region: Candidate 4-element tuple or list of integers.
        name: Human-readable region label for error messages.
        screen_size: Optional single-screen ``(screen_width, screen_height)`` bounds.
        virtual_bounds: Optional ``(v_left, v_top, v_width, v_height)`` virtual desktop bounds.
        monitors: Optional sequence of connected :class:`MonitorInfo` displays.
        allow_negative_origin: Allow negative ``left``/``top`` when display topology is omitted.
        min_dimension: Minimum allowed width and height in pixels.

    Returns:
        Normalized ``(left, top, width, height)`` integer tuple.

    Raises:
        RegionError: If ``region`` is missing, malformed, too small, or outside connected screens.
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

    if width < min_dimension or height < min_dimension:
        raise RegionError(
            f"{name} is too small ({width}x{height}); minimum size is "
            f"{min_dimension}x{min_dimension}."
        )

    normalized = (left, top, width, height)

    if monitors:
        if not is_region_covered_by_monitors(normalized, monitors):
            v_bounds = compute_virtual_screen_bounds(monitors)
            raise RegionError(
                f"{name} {normalized} exceeds connected monitor screen bounds "
                f"(virtual desktop {v_bounds}, {len(monitors)} monitor(s)). "
                "Please reselect the region."
            )
        return normalized

    if virtual_bounds is not None:
        vx, vy, vw, vh = virtual_bounds
        if vw > 0 and vh > 0 and (
            left < vx
            or top < vy
            or left + width > vx + vw
            or top + height > vy + vh
        ):
            raise RegionError(
                f"{name} {normalized} exceeds virtual screen bounds {virtual_bounds}. "
                "Please reselect the region."
            )
        return normalized

    if screen_size is not None:
        if left < 0 or top < 0:
            raise RegionError(
                f"{name} origin must be non-negative on primary screen "
                f"(got left={left}, top={top})."
            )
        screen_w, screen_h = screen_size
        if screen_w > 0 and screen_h > 0 and (
            left + width > screen_w or top + height > screen_h
        ):
            raise RegionError(
                f"{name} ({left}, {top}, {width}, {height}) exceeds current screen "
                f"resolution ({screen_w}x{screen_h}). Please reselect the region."
            )
        return normalized

    if not allow_negative_origin and (left < 0 or top < 0):
        raise RegionError(
            f"{name} origin must be non-negative (got left={left}, top={top})."
        )

    return normalized


def validate_point_in_region(
    x: int,
    y: int,
    region: tuple[int, int, int, int],
    *,
    name: str = "Region",
    screen_size: tuple[int, int] | None = None,
    virtual_bounds: tuple[int, int, int, int] | None = None,
    monitors: Sequence[MonitorInfo] | None = None,
) -> tuple[int, int]:
    """Verify that screen point ``(x, y)`` lies strictly within ``region`` and connected screens."""
    left, top, width, height = region
    if not (left <= x < left + width and top <= y < top + height):
        raise RegionError(
            f"Target point ({x}, {y}) is outside {name} ({left}, {top}, {width}, {height})."
        )

    if monitors:
        if not is_point_on_monitors(x, y, monitors):
            raise RegionError(
                f"Target point ({x}, {y}) is outside connected monitor screen bounds."
            )
        return (x, y)

    if virtual_bounds is not None:
        vx, vy, vw, vh = virtual_bounds
        if vw > 0 and vh > 0 and not (vx <= x < vx + vw and vy <= y < vy + vh):
            raise RegionError(
                f"Target point ({x}, {y}) is outside virtual screen {virtual_bounds}."
            )
        return (x, y)

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
    """Compute HiDPI/Retina pixel-to-logical scale factors ``(scale_x, scale_y)``."""
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
    """Convert pixel offsets inside a captured region image to logical screen coordinates."""
    scale_x = scale[0] if scale[0] > 0 else 1.0
    scale_y = scale[1] if scale[1] > 0 else 1.0
    screen_x = region[0] + round(offset_x / scale_x)
    screen_y = region[1] + round(offset_y / scale_y)
    return (int(screen_x), int(screen_y))


def get_screen(
    region: Any = None,
    *,
    capturer: ScreenCapturer | None = None,
    min_dimension: int = MIN_REGION_DIMENSION,
) -> np.ndarray:
    """Capture a validated screen region across single or multiple monitors as an RGB array."""
    if region is None:
        raise RegionError("Screen capture region cannot be None.")

    active_capturer: ScreenCapturer = (
        capturer if capturer is not None else PyAutoGUIScreenCapturer()
    )

    active_monitors: Sequence[MonitorInfo] | None = None
    monitors_fn = getattr(active_capturer, "monitors", None)
    if callable(monitors_fn):
        try:
            queried_monitors = monitors_fn()
            if queried_monitors:
                active_monitors = queried_monitors
        except Exception as exc:
            raise CaptureError(f"Failed to query connected monitors: {exc}") from exc

    current_screen: tuple[int, int] | None = None
    if not active_monitors:
        try:
            current_screen = active_capturer.screen_size()
        except Exception as exc:
            raise CaptureError(f"Failed to query screen dimensions: {exc}") from exc

    validated_region = validate_region(
        region,
        screen_size=current_screen,
        monitors=active_monitors,
        min_dimension=min_dimension,
    )

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
