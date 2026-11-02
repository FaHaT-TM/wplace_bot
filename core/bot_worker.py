"""Background worker for automated pixel painting and Dry-Run simulation."""

from __future__ import annotations

import logging
import queue
import threading
import types
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .logger import get_logger
from .paint_planner import (
    DEFAULT_DRY_RUN_IMAGE,
    ColorPlanEntry,
    build_paint_plan,
    format_duration,
    format_paint_plan_report,
    render_dry_run_diff_image,
)
from .screen_capture import MonitorInfo, detect_monitors

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
    """Handles live painting and non-clicking Dry-Run execution in a background thread."""

    def __init__(
        self,
        data_manager: Any,
        mouse_controller: MouseController | None = None,
        palette_switch_delay: float = PALETTE_SWITCH_DELAY_SECONDS,
        monitors_provider: Callable[[], Sequence[MonitorInfo]] | None = None,
    ) -> None:
        """Initialize the bot worker."""
        self.data_manager = data_manager
        self._custom_mouse_injected = mouse_controller is not None
        self._mouse_controller = mouse_controller
        self._palette_switch_delay = palette_switch_delay
        self._monitors_provider = monitors_provider
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

    def _resolve_display_bounds(
        self, *, dry_run: bool = False
    ) -> tuple[Sequence[MonitorInfo] | None, tuple[int, int] | None]:
        """Resolve active multi-monitor topology or fallback single-screen resolution."""
        if self._monitors_provider is not None:
            provided = self._monitors_provider()
            if provided:
                return (provided, None)

        mouse = self._mouse_controller if dry_run else self._get_mouse()
        if mouse is not None:
            monitors_fn = getattr(mouse, "monitors", None)
            if callable(monitors_fn):
                mouse_monitors = monitors_fn()
                if mouse_monitors:
                    return (mouse_monitors, None)

        if not self._custom_mouse_injected:
            detected = detect_monitors()
            if detected:
                return (detected, None)

        if mouse is not None:
            size_fn = getattr(mouse, "size", None)
            if callable(size_fn):
                raw_size = size_fn()
                if isinstance(raw_size, (tuple, list)) and len(raw_size) >= 2:
                    return (None, (int(raw_size[0]), int(raw_size[1])))
                width = getattr(raw_size, "width", None)
                height = getattr(raw_size, "height", None)
                if width is not None and height is not None:
                    return (None, (int(width), int(height)))

        return (None, None)

    def start_bot(
        self,
        message_queue: queue.Queue[dict[str, Any]],
        enabled_colors: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> None:
        """Start live painting or Dry-Run planning in a daemon background thread."""
        if self.thread is not None and self.thread.is_alive():
            module_logger.warning(
                "BotWorker thread is already running; ignoring duplicate start."
            )
            return

        is_dry_run = bool(settings.get("dry_run", False))
        self._stop_event.clear()
        self.is_running = True
        self.stopped_by_user = False
        self.mouse_moved = False
        self.last_bot_mouse_pos = (
            None if is_dry_run else self._get_mouse().position()
        )
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
        """Execute Dry-Run planning or live painting loop in a background thread."""
        total_pixels_painted = 0
        is_dry_run = bool(settings.get("dry_run", False))
        try:
            monitors, screen_size = self._resolve_display_bounds(dry_run=is_dry_run)
            plan = build_paint_plan(
                data_manager=self.data_manager,
                enabled_colors=enabled_colors,
                pixel_limit=settings.get("pixel_limit"),
                tolerance=int(settings["tolerance"]),
                click_delay_ms=int(settings["delay"]),
                palette_switch_delay_s=self._palette_switch_delay,
                screen_size=screen_size,
                monitors=monitors,
            )

            if (
                not plan.color_entries
                and plan.skipped_unbought_colors
                and len(plan.skipped_unbought_colors) == len(enabled_colors)
            ):
                self.logger.warning(
                    "No available colors to paint: all enabled colors are unbought premium colors."
                )

            if is_dry_run:
                diff_output = settings.get("diff_output_path", DEFAULT_DRY_RUN_IMAGE)
                saved_diff_path: str | None = None
                if diff_output:
                    saved_diff_path = render_dry_run_diff_image(
                        plan=plan,
                        pixel_map=self.data_manager.pixel_map,
                        pixel_size=int(self.data_manager.pixel_size or 10),
                        canvas_image_rgb=getattr(
                            self.data_manager, "canvas_image_rgb", None
                        ),
                        output_path=str(diff_output),
                    )
                report = format_paint_plan_report(plan)
                self.logger.info(
                    f"Dry-run complete: {plan.total_planned_pixels} pixels planned "
                    f"across {plan.active_color_switches} colors "
                    f"(ETA {format_duration(plan.estimated_seconds)}, 0 mouse clicks)"
                )
                message_queue.put(
                    {
                        "type": "dry_run_complete",
                        "plan": plan,
                        "report": report,
                        "diff_image_path": saved_diff_path,
                    }
                )
                return

            self.logger.bot_start(plan.pixel_limit)

            for entry in plan.color_entries:
                if entry.planned_count <= 0:
                    continue
                if (
                    self._should_stop()
                    or total_pixels_painted >= plan.pixel_limit
                    or self._check_mouse_movement()
                ):
                    break

                total_pixels_painted = self._paint_planned_entry(
                    entry=entry,
                    total_pixels_painted=total_pixels_painted,
                    pixel_limit=plan.pixel_limit,
                    delay_ms=plan.click_delay_ms,
                    message_queue=message_queue,
                )

            limit_reached = total_pixels_painted >= plan.pixel_limit
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

    def _paint_planned_entry(
        self,
        entry: ColorPlanEntry,
        total_pixels_painted: int,
        pixel_limit: int,
        delay_ms: int,
        message_queue: queue.Queue[dict[str, Any]],
    ) -> int:
        """Execute clicks for a single planned color entry and return updated painted count."""
        self.logger.debug(
            f"Painting {entry.planned_count} pixels with {entry.color_name} "
            f"(Total: {total_pixels_painted + entry.planned_count}/{pixel_limit})"
        )

        px, py = entry.swatch_point
        self._bot_click(px, py)
        if self._palette_switch_delay > 0 and self._stop_event.wait(
            timeout=self._palette_switch_delay
        ):
            return total_pixels_painted

        target_len = len(entry.targets)
        for pos_i, (_map_pos, (click_x, click_y)) in enumerate(entry.targets):
            if self._should_stop() or self._check_mouse_movement():
                break

            self._bot_click(click_x, click_y)
            total_pixels_painted += 1

            if pos_i % PROGRESS_UPDATE_INTERVAL == 0 or pos_i == target_len - 1:
                progress = (total_pixels_painted / pixel_limit) * 100
                self.logger.bot_progress(
                    total_pixels_painted, pixel_limit, entry.color_name
                )
                message_queue.put(
                    {
                        "type": "progress",
                        "progress": min(progress, 100),
                        "status": (
                            f"Painting {entry.color_name} "
                            f"({total_pixels_painted}/{pixel_limit} pixels)"
                        ),
                    }
                )

            if total_pixels_painted >= pixel_limit:
                break

            if self._check_mouse_movement():
                break

            if delay_ms > 0 and self._stop_event.wait(timeout=delay_ms / 1000.0):
                break

        return total_pixels_painted
