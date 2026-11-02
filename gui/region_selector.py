"""Overlay window for drag-to-select screen region functionality."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

from core.screen_capture import MIN_REGION_DIMENSION


class RegionSelector:
    """Fullscreen overlay window for drag-to-select region capture."""

    def __init__(
        self, callback: Callable[[tuple[int, int, int, int] | None], None]
    ) -> None:
        """Initialize the region selection overlay."""
        self.callback = callback
        self.start_x: int | None = None
        self.start_y: int | None = None
        self.start_root_x: int | None = None
        self.start_root_y: int | None = None
        self.rect_id: Any = None

        self.overlay = tk.Toplevel()
        self.overlay.attributes("-fullscreen", True)
        self.overlay.attributes("-alpha", 0.3)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="black")

        self.canvas = tk.Canvas(self.overlay, highlightthickness=0, bg="black")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.overlay.bind("<Escape>", self.cancel)
        self.overlay.focus_set()

        self.canvas.create_text(
            self.overlay.winfo_screenwidth() // 2,
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
        """Finish selection and invoke callback with global screen coordinates."""
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

        if width >= MIN_REGION_DIMENSION and height >= MIN_REGION_DIMENSION:
            region = (left, top, width, height)
            self.close()
            self.callback(region)
        else:
            self.canvas.create_text(
                event.x,
                event.y - 20,
                text="Region too small! Try again.",
                fill="yellow",
                font=("Arial", 12, "bold"),
            )
            self._reset_selection()

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
