"""Background worker for automated pixel painting and cursor-tracing Dry-Run simulation."""

from __future__ import annotations

import logging
import queue
import threading
import types
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .image_io import save_rgb_image
from .logger import get_logger
from .paint_planner import (
    DEFAULT_DRY_RUN_IMAGE,
    ColorPlanEntry,
    PaintPlan,
    build_dry_run_diff_rgb,
    build_paint_plan,
    format_duration,
    format_paint_plan_report,
)
from .screen_capture import MonitorInfo, detect_monitors
from .session_progress import CHECKPOINT_INTERVAL_PIXELS

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
    """Handles live painting and zero-click Dry-Run cursor simulation in a background thread."""

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

    def _try_get_mouse_for_dry_run(self) -> Any | None:
        """Return mouse controller for Dry-Run cursor tracing if a display is available."""
        if self._mouse_controller is not None:
            return self._mouse_controller
        try:
            return self._get_mouse()
        except (ImportError, KeyError, OSError, RuntimeError):
            module_logger.debug(
                "No GUI mouse backend available in headless environment; skipping Dry-Run cursor movement."
            )
            return None

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
        """Start live painting or Dry-Run simulation in a daemon background thread."""
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
        """Request immediate stop of the painting bot or Dry-Run simulation (Emergency Stop)."""
        self.stopped_by_user = True
        self.is_running = False
        self._stop_event.set()

    def _should_stop(self) -> bool:
        """Check whether execution was halted by Emergency Stop or flag."""
        return not self.is_running or self._stop_event.is_set()

    def _sleep_interruptibly(self, duration_seconds: float) -> bool:
        """Wait up to ``duration_seconds``; return ``True`` immediately if stop was requested."""
        if duration_seconds <= 0:
            return self._should_stop()
        return self._stop_event.wait(timeout=duration_seconds)

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

    def _bot_move(self, x: int, y: int) -> bool:
        """Move cursor to ``(x, y)`` WITHOUT clicking; return ``True`` if cursor was moved."""
        mouse = self._try_get_mouse_for_dry_run()
        if mouse is None:
            return False
        move_fn = getattr(mouse, "moveTo", None)
        if not callable(move_fn):
            return False

        self.last_bot_mouse_pos = types.SimpleNamespace(x=x, y=y)
        try:
            move_fn(x, y, _pause=False)
        except TypeError:
            move_fn(x, y)
        return True

    def _flush_checkpoint(self, status: str) -> None:
        """Persist current session progress checkpoint if supported by data_manager."""
        save_fn = getattr(self.data_manager, "save_progress_checkpoint", None)
        if callable(save_fn):
            save_fn(status=status)

    def _run_dry_run_simulation(
        self,
        plan: PaintPlan,
        settings: dict[str, Any],
        message_queue: queue.Queue[dict[str, Any]],
    ) -> None:
        """Render visual diff map and trace planned palette/canvas targets without clicking."""
        diff_rgb = build_dry_run_diff_rgb(
            plan=plan,
            pixel_map=self.data_manager.pixel_map,
            pixel_size=int(self.data_manager.pixel_size or 10),
            canvas_image_rgb=getattr(self.data_manager, "canvas_image_rgb", None),
        )

        diff_output = settings.get("diff_output_path", DEFAULT_DRY_RUN_IMAGE)
        saved_diff_path: str | None = None
        if diff_output:
            try:
                saved_diff_path = save_rgb_image(str(diff_output), diff_rgb)
            except OSError:
                module_logger.exception(
                    "Could not write Dry-Run diff image to %s; using in-memory preview.",
                    diff_output,
                )

        report = format_paint_plan_report(plan)
        total_simulated = 0

        try:
            for entry in plan.color_entries:
                if entry.planned_count <= 0:
                    continue
                if (
                    self._should_stop()
                    or total_simulated >= plan.total_planned_pixels
                    or self._check_mouse_movement()
                ):
                    break

                total_simulated = self._simulate_planned_entry(
                    entry=entry,
                    total_simulated=total_simulated,
                    total_planned=max(1, plan.total_planned_pixels),
                    delay_ms=plan.click_delay_ms,
                    message_queue=message_queue,
                )
        except Exception as exc:
            if _is_failsafe_exception(exc):
                self.mouse_moved = True
                self.is_running = False
                self._stop_event.set()
                self.logger.warning(
                    "PyAutoGUI fail-safe triggered during Dry-Run - stopping cursor preview"
                )
            else:
                raise

        cancelled_by_mouse = self.mouse_moved
        stopped_user = self.stopped_by_user and not cancelled_by_mouse

        self.logger.info(
            f"Dry-run complete: traced {total_simulated}/{plan.total_planned_pixels} pixels "
            f"across {plan.active_color_switches} colors "
            f"(ETA {format_duration(plan.estimated_seconds)}, 0 mouse clicks)"
        )
        message_queue.put(
            {
                "type": "dry_run_complete",
                "plan": plan,
                "report": report,
                "diff_image_path": saved_diff_path,
                "diff_image_rgb": diff_rgb,
                "simulated_pixels": total_simulated,
                "stopped_by_user": stopped_user,
                "cancelled_by_mouse": cancelled_by_mouse,
            }
        )

    def _simulate_planned_entry(
        self,
        entry: ColorPlanEntry,
        total_simulated: int,
        total_planned: int,
        delay_ms: int,
        message_queue: queue.Queue[dict[str, Any]],
    ) -> int:
        """Move cursor over a palette swatch and its planned canvas pixels with 0 clicks."""
        px, py = entry.swatch_point
        moved_palette = self._bot_move(px, py)
        if self._sleep_interruptibly(self._palette_switch_delay):
            return total_simulated
        if self._should_stop() or (moved_palette and self._check_mouse_movement()):
            return total_simulated

        progress_pct = (total_simulated / total_planned) * 100.0
        message_queue.put(
            {
                "type": "progress",
                "progress": progress_pct,
                "status": (
                    f"Dry-Run (0 clicks): tracing {entry.color_name}... "
                    f"({total_simulated}/{total_planned})"
                ),
            }
        )

        target_len = len(entry.targets)
        for pos_i, (_map_pos, (target_x, target_y)) in enumerate(entry.targets):
            if self._should_stop() or (
                self.last_bot_mouse_pos is not None and self._check_mouse_movement()
            ):
                break

            moved_pixel = self._bot_move(target_x, target_y)
            total_simulated += 1

            if (pos_i + 1) % PROGRESS_UPDATE_INTERVAL == 0 or pos_i == target_len - 1:
                pct = (total_simulated / total_planned) * 100.0
                message_queue.put(
                    {
                        "type": "progress",
                        "progress": pct,
                        "status": (
                            f"Dry-Run (0 clicks): tracing {entry.color_name}... "
                            f"({total_simulated}/{total_planned})"
                        ),
                    }
                )

            if total_simulated >= total_planned:
                break

            if moved_pixel and self._check_mouse_movement():
                break

            if delay_ms > 0 and self._sleep_interruptibly(delay_ms / 1000.0):
                break

        return total_simulated

    def _bot_worker(
        self,
        message_queue: queue.Queue[dict[str, Any]],
        enabled_colors: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> None:
        """Execute Dry-Run simulation or live painting loop in a background thread."""
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
                self._run_dry_run_simulation(
                    plan=plan,
                    settings=settings,
                    message_queue=message_queue,
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
            stopped_user = self.stopped_by_user and not cancelled_by_mouse

            if total_pixels_painted > 0:
                if cancelled_by_mouse:
                    checkpoint_status = "canceled_by_mouse"
                elif stopped_user:
                    checkpoint_status = "stopped_by_user"
                elif limit_reached:
                    checkpoint_status = "limit_reached"
                else:
                    checkpoint_status = "completed"
                self._flush_checkpoint(checkpoint_status)

            message_queue.put(
                {
                    "type": "bot_complete",
                    "total_painted": total_pixels_painted,
                    "limit_reached": limit_reached,
                    "cancelled_by_mouse": cancelled_by_mouse,
                    "stopped_by_user": stopped_user,
                }
            )

        except Exception as exc:
            if _is_failsafe_exception(exc):
                self.mouse_moved = True
                self.is_running = False
                self._stop_event.set()
                if total_pixels_painted > 0:
                    self._flush_checkpoint("canceled_by_mouse")
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
                if total_pixels_painted > 0:
                    self._flush_checkpoint("error")
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
        """Select a palette swatch and click all planned canvas coordinates for one color."""
        px, py = entry.swatch_point
        self._bot_click(px, py)

        if self._sleep_interruptibly(self._palette_switch_delay):
            return total_pixels_painted
        if self._should_stop() or self._check_mouse_movement():
            return total_pixels_painted

        progress = (total_pixels_painted / pixel_limit) * 100
        message_queue.put(
            {
                "type": "progress",
                "progress": progress,
                "status": (
                    f"Painting {entry.color_name}... "
                    f"({total_pixels_painted}/{pixel_limit})"
                ),
            }
        )

        record_fn = getattr(self.data_manager, "record_painted_pixel", None)
        target_bgr = (entry.rgb[2], entry.rgb[1], entry.rgb[0])
        target_len = len(entry.targets)
        for pos_i, (map_pos, (click_x, click_y)) in enumerate(entry.targets):
            if self._should_stop() or self._check_mouse_movement():
                break

            self._bot_click(click_x, click_y)
            if callable(record_fn):
                record_fn(map_pos, target_bgr)
            total_pixels_painted += 1

            if total_pixels_painted % CHECKPOINT_INTERVAL_PIXELS == 0:
                self._flush_checkpoint("in_progress")

            if (pos_i + 1) % PROGRESS_UPDATE_INTERVAL == 0 or pos_i == target_len - 1:
                progress = (total_pixels_painted / pixel_limit) * 100
                message_queue.put(
                    {
                        "type": "progress",
                        "progress": progress,
                        "status": (
                            f"Painting {entry.color_name}... "
                            f"({total_pixels_painted}/{pixel_limit})"
                        ),
                    }
                )
                self.logger.bot_progress(
                    total_pixels_painted, pixel_limit, entry.color_name
                )

            if total_pixels_painted >= pixel_limit:
                break

            if self._check_mouse_movement():
                break

            if delay_ms > 0 and self._sleep_interruptibly(delay_ms / 1000.0):
                break

        return total_pixels_painted
