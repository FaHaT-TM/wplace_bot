"""Preview tab for displaying Dry-Run visual diffs, debug images, and analysis stats."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Any

from PIL import Image, ImageTk

from core.config import resolve_writable_path
from core.paint_planner import DEFAULT_DRY_RUN_IMAGE


class PreviewTab:
    """Preview tab for showing debug images, Dry-Run diff previews, and analysis results."""

    image_var: Any = None
    image_label: Any = None
    stats_text: Any = None
    _last_dry_run_report: str | None = None
    _last_dry_run_image_path: str | None = None

    def __init__(self, parent: Any, main_window: Any) -> None:
        self.parent = parent
        self.main_window = main_window
        self.data_manager = main_window.data_manager

        self.frame = ttk.Frame(parent)

        self.image_var = None
        self.image_label = None
        self.stats_text = None
        self._last_dry_run_report = None
        self._last_dry_run_image_path = None

        self._create_ui()

    def _create_ui(self) -> None:
        """Create preview tab UI."""
        self._create_image_frame()
        self._create_stats_frame()

    def _create_image_frame(self) -> None:
        """Create image display frame."""
        image_frame = ttk.LabelFrame(self.frame, text="Debug & Dry-Run Images", padding=10)
        image_frame.pack(fill="both", expand=True, padx=10, pady=5)

        img_select_frame = ttk.Frame(image_frame)
        img_select_frame.pack(fill="x", pady=5)

        self.image_var = tk.StringVar()
        image_combo = ttk.Combobox(
            img_select_frame,
            textvariable=self.image_var,
            values=["Dry-Run Diff", "Size Estimation", "Palette Detection"],
            state="readonly",
        )
        image_combo.pack(side="left", padx=5)
        image_combo.bind("<<ComboboxSelected>>", self._load_debug_image)

        ttk.Button(
            img_select_frame, text="Refresh", command=self._refresh_debug_images
        ).pack(side="left", padx=5)

        self.image_label = ttk.Label(image_frame, text="No image selected")
        self.image_label.pack(expand=True)

    def _create_stats_frame(self) -> None:
        """Create statistics frame."""
        stats_frame = ttk.LabelFrame(
            self.frame, text="Analysis & Dry-Run Plan", padding=10
        )
        stats_frame.pack(fill="x", padx=10, pady=5)

        self.stats_text = tk.Text(stats_frame, height=10, state="disabled")
        stats_scrollbar = ttk.Scrollbar(stats_frame, command=self.stats_text.yview)
        self.stats_text.config(yscrollcommand=stats_scrollbar.set)

        self.stats_text.pack(side="left", fill="both", expand=True)
        stats_scrollbar.pack(side="right", fill="y")

    def _load_debug_image(self, event: Any = None) -> None:
        """Load and display the selected debug or Dry-Run diff image."""
        _ = event
        image_type = self.image_var.get()
        dry_run_file = getattr(self, "_last_dry_run_image_path", None) or DEFAULT_DRY_RUN_IMAGE
        filename_map = {
            "Dry-Run Diff": dry_run_file,
            "Size Estimation": "debug_size_estimation.png",
            "Palette Detection": "debug_palette.png",
        }

        filename = filename_map.get(image_type)
        if filename:
            filename = resolve_writable_path(filename)
        if filename and os.path.exists(filename):
            try:
                img = Image.open(filename)
                img.thumbnail((600, 400), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.image_label.config(image=photo, text="")
                self.image_label.image = photo
            except (OSError, RuntimeError, tk.TclError) as exc:
                self.image_label.config(text=f"Error loading image: {exc}")
        else:
            hint = (
                "Dry-Run image not found. Click 'Dry Run (Preview)' in Bot Control first."
                if image_type == "Dry-Run Diff"
                else "Image not found. Run analysis first."
            )
            self.image_label.config(text=hint)

    def _refresh_debug_images(self) -> None:
        """Refresh debug image display."""
        self._load_debug_image()

    def show_dry_run_report(
        self,
        plan: Any,
        report: str,
        diff_image_path: str | None = None,
    ) -> None:
        """Display Dry-Run diff image and detailed per-color report in the Preview tab."""
        _ = plan
        self._last_dry_run_report = report
        if diff_image_path:
            self._last_dry_run_image_path = diff_image_path

        if self.image_var is not None:
            self.image_var.set("Dry-Run Diff")
            self._load_debug_image()

        self.update_stats()

    def update_stats(self) -> None:
        """Update statistics display with analysis metrics and latest Dry-Run report."""
        if not self.data_manager.has_analysis_data():
            return

        setup_tab = self.main_window.setup_tab
        colors_tab = self.main_window.colors_tab
        control_tab = self.main_window.control_tab

        tolerance = (
            setup_tab.tolerance_var.get()
            if setup_tab
            else self.data_manager.get_preference("color_tolerance", 1)
        )
        delay = (
            setup_tab.delay_var.get()
            if setup_tab
            else self.data_manager.get_preference("click_delay", 15)
        )
        pixel_limit = (
            control_tab.get_pixel_limit_or_last_valid()
            if control_tab
            else self.data_manager.get_preference("pixel_limit", 1000)
        )

        enabled_count = (
            len([var for var in colors_tab.color_vars.values() if var.get()])
            if colors_tab
            else 0
        )
        total_count = len(self.data_manager.color_palette)

        stats = (
            f"Analysis Results:\n\n"
            f"Pixel Size: {self.data_manager.pixel_size}x{self.data_manager.pixel_size}\n"
            f"Total Pixels Detected: {len(self.data_manager.pixel_map)}\n"
            f"Colors Found in Palette: {len(self.data_manager.color_position_map)}\n\n"
            f"Enabled Colors: {enabled_count}\n"
            f"Total Colors: {total_count}\n\n"
            f"Settings:\n"
            f"Color Tolerance: {tolerance}\n"
            f"Click Delay: {delay}ms\n"
            f"Pixel Limit: {pixel_limit}\n"
        )

        dry_run_report = getattr(self, "_last_dry_run_report", None)
        if dry_run_report:
            stats = f"{stats}\n{dry_run_report}\n"

        self.stats_text.config(state="normal")
        self.stats_text.delete(1.0, tk.END)
        self.stats_text.insert(1.0, stats)
        self.stats_text.config(state="disabled")
