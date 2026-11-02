"""Background worker for automated pixel painting."""

from __future__ import annotations

import logging
import queue
import threading
import types
from typing import Any, Protocol

from .config import MAX_PIXEL_LIMIT, MIN_PIXEL_LIMIT, get_config, validate_pixel_limit
from .exceptions import PaintError, PaletteDetectionError, RegionError
from .logger import get_logger
from .pixel_mapping import find_pixels_to_paint_from_map
from .screen_capture import (
    image_offset_to_screen_point,
    validate_point_in_region,
    validate_region,
)

module_logger = logging.getLogger(__name__)

MOUSE_CANCEL_THRESHOLD_PX: int = 10
PALETTE_SWITCH_DELAY_SECONDS: float = 0.2
PROGRESS_UPDATE_INTERVAL: int = 10


class MousePoint(Protocol):
    """Protocol representing 2D screen coordinates returned by mouse position."""

    x: int
    y: int


class MouseController(Protocol):
    """Protocol abstracting OS mouse operations for testability."""

    def position(self) -> MousePoint:
        """Return current cursor coordinates."""
        ...

    def click(self, x: int, y: int) -> None:
        """Perform a left click at screen coordinates ``(x, y)``."""
        ...


def _is_failsafe_exception(exc: BaseException) -> bool:
    """Detect PyAutoGUI FailSafeException without requiring eager pyautogui import."""
    return exc.__class__.__name__ == "FailSafeException"


class BotWorker:
    """Handles bot painting logic in a separate background thread."""

    def __init__(
        self,
        data_manager: Any,
        mouse_controller: MouseController | None = None,
        palette_switch_delay: float = PALETTE_SWITCH_DELAY_SECONDS,
    ) -> None:
        """Initialize the bot worker.

        Args:
            data_manager: Shared application data manager holding regions and maps.
            mouse_controller: Optional mouse controller implementation (defaults to ``pyautogui``).
            palette_switch_delay: Pause in seconds after clicking a palette swatch.
        """
        self.data_manager = data_manager
        self._mouse_controller = mouse_controller
        self._palette_switch_delay = palette_switch_delay
        self._stop_event = threading.Event()
        self.is_running: bool = False
        self.stopped_by_user: bool = False
        self.thread: threading.Thread | None = None
        self.logger = get_logger()
        self.last_bot_mouse_pos: MousePoint | None = None
        self.mouse_moved: bool = False

    def _get_mouse(self) -> MouseController:
        """Lazily resolve the mouse controller, defaulting to ``pyautogui``."""
        if self._mouse_controller is None:
            import pyautogui

            self._mouse_controller = pyautogui
        return self._mouse_controller

    def _get_screen_size(self) -> tuple[int, int] | None:
        """Return logical screen ``(width, height)`` if exposed by mouse controller."""
        size_fn = getattr(self._get_mouse(), "size", None)
        if callable(size_fn):
            raw_size = size_fn()
            if isinstance(raw_size, (tuple, list)) and len(raw_size) >= 2:
                return (int(raw_size[0]), int(raw_size[1]))
            width = getattr(raw_size, "width", None)
            height = getattr(raw_size, "height", None)
            if width is not None and height is not None:
                return (int(width), int(height))
        return None

    def _is_color_paintable(self, color: dict[str, Any]) -> bool:
        """Verify that a color is free or marked as bought before clicking its swatch."""
        if not color.get("premium", False):
            return True
        checker = getattr(self.data_manager, "is_color_bought", None)
        if callable(checker):
            return bool(checker(color))
        return bool(color.get("bought", False))

    def start_bot(
        self,
        message_queue: queue.Queue[dict[str, Any]],
        enabled_colors: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> None:
        """Start the painting bot in a daemon background thread.

        Args:
            message_queue: Thread-safe queue used to notify the GUI.
            enabled_colors: List of enabled palette color dictionaries.
            settings: Dictionary containing ``pixel_limit``, ``tolerance``, and ``delay``.
        """
        if self.thread is not None and self.thread.is_alive():
            module_logger.warning(
                "BotWorker thread is already running; ignoring duplicate start."
            )
            return

        self._stop_event.clear()
        self.is_running = True
        self.stopped_by_user = False
        self.mouse_moved = False
        self.last_bot_mouse_pos = self._get_mouse().position()
        self.thread = threading.Thread(
            target=self._bot_worker,
            args=(message_queue, enabled_colors, settings),
            daemon=True,
        )
        self.thread.start()

    def stop_bot(self) -> None:
        """Request immediate stop of the painting bot (Emergency Stop)."""
        self.stopped_by_user = True
        self.is_running = False
        self._stop_event.set()

    def _should_stop(self) -> bool:
        """Check whether execution was halted by Emergency Stop or flag."""
        return not self.is_running or self._stop_event.is_set()

    def _check_mouse_movement(self) -> bool:
        """Check if mouse has moved from last bot target position (user cancellation)."""
        if self.last_bot_mouse_pos is None:
            return False

        current_pos = self._get_mouse().position()
        moved = (
            abs(current_pos.x - self.last_bot_mouse_pos.x) > MOUSE_CANCEL_THRESHOLD_PX
            or abs(current_pos.y - self.last_bot_mouse_pos.y)
            > MOUSE_CANCEL_THRESHOLD_PX
        )

        if moved and not self.mouse_moved:
            self.mouse_moved = True
            self.is_running = False
            self._stop_event.set()
            self.logger.info("User mouse movement detected - canceling bot")

        return moved

    def _bot_click(self, x: int, y: int) -> None:
        """Perform a click and record target coordinates ``(x, y)`` as expected cursor position."""
        self.last_bot_mouse_pos = types.SimpleNamespace(x=x, y=y)
        self._get_mouse().click(x, y)

    def _bot_worker(
        self,
        message_queue: queue.Queue[dict[str, Any]],
        enabled_colors: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> None:
        """Execute painting loop in a background thread."""
        total_pixels_painted = 0
        try:
            max_limit = int(get_config().get("bot.max_pixel_limit", MAX_PIXEL_LIMIT))
            pixel_limit = validate_pixel_limit(
                settings.get("pixel_limit"),
                min_limit=MIN_PIXEL_LIMIT,
                max_limit=max_limit,
            )
            tolerance = int(settings["tolerance"])
            delay = int(settings["delay"])

            screen_size = self._get_screen_size()
            canvas_region = validate_region(
                self.data_manager.canvas_region,
                name="Canvas region",
                screen_size=screen_size,
            )
            palette_region_raw = getattr(self.data_manager, "palette_region", None)
            palette_region: tuple[int, int, int, int] | None = None
            if palette_region_raw is not None:
                palette_region = validate_region(
                    palette_region_raw,
                    name="Palette region",
                    screen_size=screen_size,
                )

            if self.data_manager.pixel_map is None:
                raise PaintError(
                    "Canvas pixel map is missing. Please run analysis before starting the bot."
                )

            if not self.data_manager.color_position_map:
                raise PaletteDetectionError(
                    "Palette color map is empty. Please reanalyze the palette region before starting."
                )

            paintable_colors = []
            skipped_unbought = 0
            for color in enabled_colors:
                if self._is_color_paintable(color):
                    paintable_colors.append(color)
                else:
                    skipped_unbought += 1
                    module_logger.debug(
                        "Skipping premium color '%s' (id=%s): not marked as bought",
                        color.get("name"),
                        color.get("id"),
                    )

            if not paintable_colors and skipped_unbought > 0:
                self.logger.warning(
                    "No available colors to paint: all enabled colors are unbought premium colors."
                )

            canvas_scale: tuple[float, float] = getattr(
                self.data_manager, "canvas_scale", (1.0, 1.0)
            )
            claimed_positions: set[tuple[int, int]] = set()

            self.logger.bot_start(pixel_limit)

            for color in paintable_colors:
                if (
                    self._should_stop()
                    or total_pixels_painted >= pixel_limit
                    or self._check_mouse_movement()
                ):
                    break

                total_pixels_painted = self._paint_color(
                    color=color,
                    total_pixels_painted=total_pixels_painted,
                    pixel_limit=pixel_limit,
                    tolerance=tolerance,
                    delay=delay,
                    message_queue=message_queue,
                    canvas_region=canvas_region,
                    palette_region=palette_region,
                    canvas_scale=canvas_scale,
                    screen_size=screen_size,
                    claimed_positions=claimed_positions,
                )

            limit_reached = total_pixels_painted >= pixel_limit
            cancelled_by_mouse = self.mouse_moved

            message_queue.put(
                {
                    "type": "bot_complete",
                    "total_painted": total_pixels_painted,
                    "limit_reached": limit_reached,
                    "cancelled_by_mouse": cancelled_by_mouse,
                    "stopped_by_user": self.stopped_by_user and not cancelled_by_mouse,
                }
            )

        except Exception as exc:
            if _is_failsafe_exception(exc):
                self.mouse_moved = True
                self.is_running = False
                self._stop_event.set()
                self.logger.warning(
                    "PyAutoGUI fail-safe triggered - emergency stopping bot"
                )
                message_queue.put(
                    {
                        "type": "bot_complete",
                        "total_painted": total_pixels_painted,
                        "limit_reached": False,
                        "cancelled_by_mouse": True,
                        "stopped_by_user": False,
                    }
                )
            else:
                module_logger.exception("Unhandled error in BotWorker")
                message_queue.put({"type": "bot_error", "error": str(exc)})
        finally:
            self.is_running = False

    def _paint_color(
        self,
        color: dict[str, Any],
        total_pixels_painted: int,
        pixel_limit: int,
        tolerance: int,
        delay: int,
        message_queue: queue.Queue[dict[str, Any]],
        canvas_region: tuple[int, int, int, int],
        palette_region: tuple[int, int, int, int] | None,
        canvas_scale: tuple[float, float],
        screen_size: tuple[int, int] | None,
        claimed_positions: set[tuple[int, int]],
    ) -> int:
        """Paint unclaimed in-bounds pixels matching ``color`` up to ``pixel_limit``."""
        target_rgb = tuple(color["rgb"])
        if target_rgb not in self.data_manager.color_position_map:
            return total_pixels_painted

        target_bgr = target_rgb[::-1]
        raw_positions = find_pixels_to_paint_from_map(
            self.data_manager.pixel_map, target_bgr, tolerance=tolerance
        )
        if not raw_positions:
            return total_pixels_painted

        px, py = self.data_manager.color_position_map[target_rgb]
        if palette_region is not None:
            validate_point_in_region(
                px,
                py,
                palette_region,
                name="Palette region",
                screen_size=screen_size,
            )

        remaining_pixels = pixel_limit - total_pixels_painted
        valid_targets: list[tuple[tuple[int, int], tuple[int, int]]] = []
        for pos in raw_positions:
            if len(valid_targets) >= remaining_pixels:
                break
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
                )
            except RegionError as exc:
                module_logger.warning("Skipping out-of-bounds pixel %s: %s", pos, exc)
                continue

            valid_targets.append((pos, (click_x, click_y)))

        if not valid_targets:
            return total_pixels_painted

        self.logger.debug(
            f"Painting {len(valid_targets)} pixels with {color['name']} "
            f"(Total: {total_pixels_painted + len(valid_targets)}/{pixel_limit})"
        )

        self._bot_click(px, py)
        if self._palette_switch_delay > 0 and self._stop_event.wait(
            timeout=self._palette_switch_delay
        ):
            return total_pixels_painted

        for pos_i, (map_pos, (click_x, click_y)) in enumerate(valid_targets):
            if self._should_stop() or self._check_mouse_movement():
                break

            self._bot_click(click_x, click_y)
            claimed_positions.add(map_pos)
            total_pixels_painted += 1

            if pos_i % PROGRESS_UPDATE_INTERVAL == 0 or pos_i == len(valid_targets) - 1:
                progress = (total_pixels_painted / pixel_limit) * 100
                self.logger.bot_progress(
                    total_pixels_painted, pixel_limit, color["name"]
                )
                message_queue.put(
                    {
                        "type": "progress",
                        "progress": min(progress, 100),
                        "status": (
                            f"Painting {color['name']} "
                            f"({total_pixels_painted}/{pixel_limit} pixels)"
                        ),
                    }
                )

            if total_pixels_painted >= pixel_limit:
                break

            if self._check_mouse_movement():
                break

            if delay > 0 and self._stop_event.wait(timeout=delay / 1000.0):
                break

        return total_pixels_painted
