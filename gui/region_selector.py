"""Overlay window for multi-monitor drag-to-select screen region functionality."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable, Sequence
from typing import Any

from core.exceptions import RegionError
from core.screen_capture import (
    MIN_REGION_DIMENSION,
    MonitorInfo,
    compute_virtual_screen_bounds,
    detect_monitors,
    validate_region,
)

logger = logging.getLogger(__name__)
_TCL_ERROR: type[BaseException] = getattr(tk, "TclError", RuntimeError)


class RegionSelector:
    """Borderless virtual-desktop overlay window for drag-to-select region capture."""

    def __init__(
        self,
        callback: Callable[[tuple[int, int, int, int] | None], None],
        monitors: Sequence[MonitorInfo] | None = None,
    ) -> None:
        """Initialize the region selection overlay across all connected monitors."""
        self.callback = callback
        self.monitors: list[MonitorInfo] = (
            list(monitors) if monitors is not None else detect_monitors()
        )
        self.start_x: int | None = None
        self.start_y: int | None = None
        self.start_root_x: int | None = None
        self.start_root_y: int | None = None
        self.rect_id: Any = None
        self._hint_id: Any = None

        self.overlay = tk.Toplevel()
        self.virtual_bounds = self._configure_overlay_geometry()
        self.overlay.attributes("-alpha", 0.3)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="black")

        self.canvas = tk.Canvas(self.overlay, highlightthickness=0, bg="black")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.cancel)
        self.canvas.bind("<Escape>", self.cancel)
        self.overlay.bind("<Escape>", self.cancel)

        self._focus_overlay()
        self._draw_instructions()

    def _configure_overlay_geometry(self) -> tuple[int, int, int, int]:
        """Span the overlay across the full virtual desktop or fall back to fullscreen."""
        if self.monitors:
            vx, vy, vw, vh = compute_virtual_screen_bounds(self.monitors)
            try:
                self.overlay.overrideredirect(True)
                self.overlay.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
                return (vx, vy, vw, vh)
            except (_TCL_ERROR, AttributeError, RuntimeError):
                logger.debug(
                    "Virtual desktop geometry failed; falling back to fullscreen.",
                    exc_info=True,
                )

        self.overlay.attributes("-fullscreen", True)
        sw = int(self.overlay.winfo_screenwidth())
        sh = int(self.overlay.winfo_screenheight())
        return (0, 0, sw, sh)

    def _focus_overlay(self) -> None:
        """Raise and focus the overlay window so keyboard shortcuts work across monitors."""
        for method_name in ("lift", "focus_force", "focus_set"):
            fn = getattr(self.overlay, method_name, None)
            if callable(fn):
                try:
                    fn()
                except (_TCL_ERROR, AttributeError, RuntimeError):
                    logger.debug("Overlay %s failed.", method_name, exc_info=True)
        canvas_focus = getattr(self.canvas, "focus_set", None)
        if callable(canvas_focus):
            canvas_focus()

    def _draw_instructions(self) -> None:
        """Render selection instructions centered at the top of each connected monitor."""
        vx, vy, vw, _ = self.virtual_bounds
        if self.monitors:
            multi = len(self.monitors) > 1
            for mon in self.monitors:
                center_x = (mon.left - vx) + mon.width // 2
                top_y = (mon.top - vy) + 50
                banner = "Drag to select region. Press ESC or right-click to cancel."
                if multi:
                    banner = f"Monitor {mon.index} ({mon.width}x{mon.height}) — {banner}"
                self.canvas.create_text(
                    center_x,
                    top_y,
                    text=banner,
                    fill="white",
                    font=("Arial", 16, "bold"),
                )
        else:
            self.canvas.create_text(
                vw // 2,
                50,
                text="Drag to select region. Press ESC to cancel.",
                fill="white",
                font=("Arial", 16, "bold"),
            )

    def on_click(self, event: Any) -> None:
        """Start selection in both canvas-local and global screen coordinates."""
        self.start_x = int(event.x)
        self.start_y = int(event.y)
        self.start_root_x = int(getattr(event, "x_root", event.x))
        self.start_root_y = int(getattr(event, "y_root", event.y))
        if self.rect_id:
            self.canvas.delete(self.rect_id)
            self.rect_id = None
        if getattr(self, "_hint_id", None):
            self.canvas.delete(self._hint_id)
            self._hint_id = None

    def on_drag(self, event: Any) -> None:
        """Update selection rectangle while dragging."""
        if self.start_x is not None and self.start_y is not None:
            if self.rect_id:
                self.canvas.delete(self.rect_id)

            self.rect_id = self.canvas.create_rectangle(
                self.start_x,
                self.start_y,
                event.x,
                event.y,
                outline="red",
                width=2,
                fill="",
                stipple="gray25",
            )

    def on_release(self, event: Any) -> None:
        """Finish selection, validate monitor bounds, and invoke callback."""
        if self.start_x is None or self.start_y is None:
            return

        start_rx = (
            self.start_root_x if self.start_root_x is not None else self.start_x
        )
        start_ry = (
            self.start_root_y if self.start_root_y is not None else self.start_y
        )
        end_rx = int(getattr(event, "x_root", event.x))
        end_ry = int(getattr(event, "y_root", event.y))

        left = min(start_rx, end_rx)
        top = min(start_ry, end_ry)
        width = abs(end_rx - start_rx)
        height = abs(end_ry - start_ry)

        if width < MIN_REGION_DIMENSION or height < MIN_REGION_DIMENSION:
            self._hint_id = self.canvas.create_text(
                event.x,
                event.y - 20,
                text="Region too small! Try again.",
                fill="yellow",
                font=("Arial", 12, "bold"),
            )
            self._reset_selection()
            return

        region = (left, top, width, height)
        active_monitors: Sequence[MonitorInfo] | None = getattr(self, "monitors", None)
        if active_monitors:
            try:
                region = validate_region(
                    region, name="Selected region", monitors=active_monitors
                )
            except RegionError:
                self._hint_id = self.canvas.create_text(
                    event.x,
                    event.y - 20,
                    text="Region extends outside monitor bounds! Try again.",
                    fill="yellow",
                    font=("Arial", 12, "bold"),
                )
                self._reset_selection()
                return

        self.close()
        self.callback(region)

    def _reset_selection(self) -> None:
        """Reset selection state."""
        self.start_x = None
        self.start_y = None
        self.start_root_x = None
        self.start_root_y = None
        if self.rect_id:
            self.canvas.delete(self.rect_id)
            self.rect_id = None

    def cancel(self, event: Any = None) -> None:
        """Cancel selection."""
        _ = event
        self.close()
        self.callback(None)

    def close(self) -> None:
        """Close the overlay."""
        self.overlay.destroy()
