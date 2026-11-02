"""Setup tab for multi-monitor region selection, analysis, and core parameters."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence
from tkinter import ttk
from typing import Any

from core.exceptions import RegionError
from core.screen_capture import (
    MonitorInfo,
    compute_virtual_screen_bounds,
    detect_monitors,
    find_monitors_for_region,
    format_monitor_summary,
    validate_region,
)
from gui.region_selector import RegionSelector


class SetupTab:
    """Setup tab for region selection and analysis."""

    def __init__(self, parent: Any, main_window: Any) -> None:
        self.parent = parent
        self.main_window = main_window
        self.data_manager = main_window.data_manager
        self.analysis_worker = main_window.analysis_worker
        self.message_queue = main_window.message_queue

        self.frame = ttk.Frame(parent)

        self.monitor_status: Any = None
        self.canvas_status: Any = None
        self.palette_status: Any = None
        self.analyze_btn: Any = None
        self.analysis_status: Any = None
        self.tolerance_var: Any = None
        self.delay_var: Any = None
        self.tolerance_label: Any = None
        self.delay_label: Any = None
        self._save_timer: Any = None

        self._create_ui()
        self._load_saved_regions()

    def _create_ui(self) -> None:
        """Create setup tab UI."""
        self._create_instructions()
        self._add_separator()
        self._create_region_selection()
        self._add_separator()
        self._create_analysis_frame()
        self._add_separator()
        self._create_settings_frame()

    def _create_instructions(self) -> None:
        """Create instructions frame."""
        instructions_frame = ttk.LabelFrame(self.frame, text="Instructions", padding=10)
        instructions_frame.pack(fill="x", padx=10, pady=5)

        instructions_text = """How to select regions (multi-monitor supported):
1. Click 'Select Canvas' or 'Select Palette' button
2. All connected displays will show a dark selection overlay
3. Click and drag on any monitor to select the desired region
4. Release mouse to confirm selection (Canvas and Palette can be on different monitors)
5. Press ESC or right-click to cancel selection"""

        self.instructions_label = ttk.Label(
            instructions_frame, text=instructions_text, justify="left"
        )
        self.instructions_label.pack(anchor="w")

    def _create_region_selection(self) -> None:
        """Create region selection frame with display topology indicator."""
        region_frame = ttk.LabelFrame(self.frame, text="Region Selection", padding=10)
        region_frame.pack(fill="x", padx=10, pady=5)

        # Connected displays indicator
        displays_frame = ttk.Frame(region_frame)
        displays_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(displays_frame, text="Connected Displays:").pack(side="left")
        self.monitor_status = ttk.Label(
            displays_frame, text="Detecting...", foreground="gray"
        )
        self.monitor_status.pack(side="left", padx=(10, 0))
        refresh_btn = ttk.Button(
            displays_frame, text="Refresh Displays", command=self._refresh_displays
        )
        refresh_btn.pack(side="right")
        self._create_tooltip(
            refresh_btn,
            "Re-detect connected monitors and validate selected regions",
        )

        # Canvas region
        canvas_frame = ttk.Frame(region_frame)
        canvas_frame.pack(fill="x", pady=5)
        ttk.Label(canvas_frame, text="Canvas Region:").pack(side="left")
        self.canvas_status = ttk.Label(
            canvas_frame, text="Not selected", foreground="red"
        )
        self.canvas_status.pack(side="left", padx=(10, 0))
        canvas_btn = ttk.Button(
            canvas_frame, text="Select Canvas", command=self._select_canvas_region
        )
        canvas_btn.pack(side="right")
        self._create_tooltip(
            canvas_btn, "Click to select the drawing area on any connected monitor"
        )

        # Palette region
        palette_frame = ttk.Frame(region_frame)
        palette_frame.pack(fill="x", pady=5)
        ttk.Label(palette_frame, text="Palette Region:").pack(side="left")
        self.palette_status = ttk.Label(
            palette_frame, text="Not selected", foreground="red"
        )
        self.palette_status.pack(side="left", padx=(10, 0))
        palette_btn = ttk.Button(
            palette_frame, text="Select Palette", command=self._select_palette_region
        )
        palette_btn.pack(side="right")
        self._create_tooltip(
            palette_btn,
            "Click to select the color palette area on any connected monitor",
        )

    def _create_analysis_frame(self) -> None:
        """Create analysis frame."""
        analysis_frame = ttk.LabelFrame(self.frame, text="Analysis", padding=10)
        analysis_frame.pack(fill="x", padx=10, pady=5)

        self.analyze_btn = ttk.Button(
            analysis_frame,
            text="Analyze Canvas & Palette",
            command=self._analyze_regions,
            state="disabled",
        )
        self.analyze_btn.pack(pady=5)
        self._create_tooltip(
            self.analyze_btn, "Analyze selected regions to detect pixels and colors"
        )
        self.analysis_status = ttk.Label(analysis_frame, text="Select regions first")
        self.analysis_status.pack(pady=5)

    def _create_settings_frame(self) -> None:
        """Create settings frame."""
        settings_frame = ttk.LabelFrame(self.frame, text="Settings", padding=10)
        settings_frame.pack(fill="x", padx=10, pady=5)

        saved_tolerance = self.data_manager.user_settings["preferences"][
            "color_tolerance"
        ]
        saved_delay = self.data_manager.user_settings["preferences"]["click_delay"]

        tolerance_frame = ttk.Frame(settings_frame)
        tolerance_frame.pack(fill="x", pady=2)
        ttk.Label(tolerance_frame, text="Color Tolerance:").pack(side="left")
        self.tolerance_var = tk.IntVar(value=saved_tolerance)
        tolerance_scale = ttk.Scale(
            tolerance_frame,
            from_=1,
            to=20,
            variable=self.tolerance_var,
            orient="horizontal",
            command=self._update_tolerance_label,
        )
        tolerance_scale.pack(side="right", fill="x", expand=True, padx=(10, 0))
        self._create_tooltip(
            tolerance_scale, "How closely colors must match (1=exact, 20=loose)"
        )
        self.tolerance_label = ttk.Label(tolerance_frame, text=str(saved_tolerance))
        self.tolerance_label.pack(side="right", padx=(5, 10))

        delay_frame = ttk.Frame(settings_frame)
        delay_frame.pack(fill="x", pady=2)
        ttk.Label(delay_frame, text="Click Delay (ms):").pack(side="left")
        self.delay_var = tk.IntVar(value=saved_delay)
        delay_scale = ttk.Scale(
            delay_frame,
            from_=10,
            to=100,
            variable=self.delay_var,
            orient="horizontal",
            command=self._update_delay_label,
        )
        delay_scale.pack(side="right", fill="x", expand=True, padx=(10, 0))
        self._create_tooltip(
            delay_scale, "Delay between mouse clicks in milliseconds (lower=faster)"
        )
        self.delay_label = ttk.Label(delay_frame, text=str(saved_delay))
        self.delay_label.pack(side="right", padx=(5, 10))

    def refresh_fonts(self) -> None:
        """Refresh fonts when scaling changes."""
        if hasattr(self, "instructions_label"):
            font = self.main_window.get_scaled_font(9)
            self.instructions_label.config(font=font)

    def _query_monitors(self) -> list[MonitorInfo]:
        """Query connected monitors and update the display status indicator."""
        monitors = detect_monitors()
        monitor_label = getattr(self, "monitor_status", None)
        if monitor_label is not None:
            if monitors:
                _, _, vw, vh = compute_virtual_screen_bounds(monitors)
                count = len(monitors)
                noun = "display" if count == 1 else "displays"
                monitor_label.config(
                    text=f"{count} {noun} detected (Virtual desktop: {vw}x{vh})",
                    foreground="green" if count > 1 else "blue",
                )
            else:
                sw = self.main_window.root.winfo_screenwidth()
                sh = self.main_window.root.winfo_screenheight()
                monitor_label.config(
                    text=f"Primary display ({sw}x{sh})", foreground="blue"
                )
        return monitors

    def _format_region_text(
        self,
        prefix: str,
        region: tuple[int, int, int, int],
        monitors: Sequence[MonitorInfo],
    ) -> str:
        """Format region coordinates with an optional monitor badge."""
        badge = format_monitor_summary(region, monitors) if monitors else ""
        if badge:
            return f"{prefix}: {region} [{badge}]"
        return f"{prefix}: {region}"

    def _refresh_displays(self) -> None:
        """Re-query display topology and re-validate current regions."""
        self._load_saved_regions()
        self.main_window.log_message("Refreshed connected display layout")

    def _load_saved_regions(self) -> None:
        """Load and validate saved regions against all connected monitors."""
        monitors = self._query_monitors()
        fallback_screen = (
            None
            if monitors
            else (
                self.main_window.root.winfo_screenwidth(),
                self.main_window.root.winfo_screenheight(),
            )
        )

        if self.data_manager.canvas_region is not None:
            try:
                valid_canvas = validate_region(
                    self.data_manager.canvas_region,
                    name="Canvas region",
                    screen_size=fallback_screen,
                    monitors=monitors or None,
                )
                self.data_manager.canvas_region = valid_canvas
                self.canvas_status.config(
                    text=self._format_region_text("Loaded", valid_canvas, monitors),
                    foreground="blue",
                )
            except RegionError:
                self.data_manager.canvas_region = None
                self.canvas_status.config(
                    text="Saved region is on a disconnected monitor - reselect",
                    foreground="orange",
                )

        if self.data_manager.palette_region is not None:
            try:
                valid_palette = validate_region(
                    self.data_manager.palette_region,
                    name="Palette region",
                    screen_size=fallback_screen,
                    monitors=monitors or None,
                )
                self.data_manager.palette_region = valid_palette
                self.palette_status.config(
                    text=self._format_region_text("Loaded", valid_palette, monitors),
                    foreground="blue",
                )
            except RegionError:
                self.data_manager.palette_region = None
                self.palette_status.config(
                    text="Saved region is on a disconnected monitor - reselect",
                    foreground="orange",
                )

        self._check_ready_for_analysis()

    def _select_canvas_region(self) -> None:
        """Start canvas region selection across all connected monitors."""
        monitors = self._query_monitors()
        self.main_window.root.withdraw()
        RegionSelector(self._on_canvas_region_selected, monitors=monitors)

    def _select_palette_region(self) -> None:
        """Start palette region selection across all connected monitors."""
        monitors = self._query_monitors()
        self.main_window.root.withdraw()
        RegionSelector(self._on_palette_region_selected, monitors=monitors)

    def _on_canvas_region_selected(
        self, region: tuple[int, int, int, int] | None
    ) -> None:
        """Handle canvas region selection."""
        self.main_window.root.deiconify()
        monitors = self._query_monitors()
        if region:
            self.data_manager.set_canvas_region(region)
            self.canvas_status.config(
                text=self._format_region_text("Selected", region, monitors),
                foreground="green",
            )
            if len(find_monitors_for_region(region, monitors)) > 1:
                self.main_window.log_message(
                    "Warning: Canvas region spans multiple monitors; ensure identical display scaling."
                )
        else:
            self.main_window.log_message("Canvas region selection canceled")
        self._check_ready_for_analysis()
        control_tab = getattr(self.main_window, "control_tab", None)
        if control_tab is not None and hasattr(control_tab, "refresh_session_status"):
            control_tab.refresh_session_status()

    def _on_palette_region_selected(
        self, region: tuple[int, int, int, int] | None
    ) -> None:
        """Handle palette region selection."""
        self.main_window.root.deiconify()
        monitors = self._query_monitors()
        if region:
            self.data_manager.set_palette_region(region)
            self.palette_status.config(
                text=self._format_region_text("Selected", region, monitors),
                foreground="green",
            )
            if len(find_monitors_for_region(region, monitors)) > 1:
                self.main_window.log_message(
                    "Warning: Palette region spans multiple monitors; ensure identical display scaling."
                )
        else:
            self.main_window.log_message("Palette region selection canceled")
        self._check_ready_for_analysis()
        control_tab = getattr(self.main_window, "control_tab", None)
        if control_tab is not None and hasattr(control_tab, "refresh_session_status"):
            control_tab.refresh_session_status()

    def _check_ready_for_analysis(self) -> None:
        """Enable analysis button if both regions are selected, otherwise disable it."""
        if self.data_manager.canvas_region and self.data_manager.palette_region:
            self.analyze_btn.config(state="normal")
        else:
            self.analyze_btn.config(state="disabled")

    def _analyze_regions(self) -> None:
        """Run analysis in a separate thread."""
        self.analyze_btn.config(state="disabled", text="Analyzing...")
        self.analysis_status.config(text="Running analysis...")
        self.main_window.log_message("Starting analysis...")
        self.analysis_worker.start_analysis(self.message_queue)

    def _update_tolerance_label(self, value: str) -> None:
        """Update tolerance value display and save."""
        self.tolerance_label.config(text=f"{int(float(value))}")
        self._debounced_save()

    def _update_delay_label(self, value: str) -> None:
        """Update delay value display and save."""
        self.delay_label.config(text=f"{int(float(value))}")
        self._debounced_save()

    def _debounced_save(self) -> None:
        """Save settings with debouncing to prevent UI freezing."""
        if getattr(self, "_save_timer", None) is not None:
            self.main_window.root.after_cancel(self._save_timer)
        self._save_timer = self.main_window.root.after(
            500, self.main_window.save_user_settings
        )

    def on_analysis_complete(self, message: dict[str, Any]) -> None:
        """Handle analysis completion from main window."""
        self.analyze_btn.config(state="normal", text="Analyze Canvas & Palette")
        self.analysis_status.config(
            text=(
                f"Analysis complete! Pixel size: {message['pixel_size']}, "
                f"Pixels: {message['pixel_count']}, Colors: {message['colors_found']}"
            )
        )

    def _create_tooltip(self, widget: Any, text: str) -> None:
        """Create modern styled tooltip for widget."""

        def on_enter(event: Any) -> None:
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root + 15}+{event.y_root + 10}")
            tooltip.configure(bg="#2d2d2d")

            shadow_frame = tk.Frame(tooltip, bg="#1a1a1a", bd=0)
            shadow_frame.pack(fill="both", expand=True, padx=(2, 0), pady=(2, 0))

            main_frame = tk.Frame(shadow_frame, bg="#2d2d2d", bd=1, relief="solid")
            main_frame.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))

            label = tk.Label(
                main_frame,
                text=text,
                bg="#2d2d2d",
                fg="#ffffff",
                font=("Segoe UI", 9),
                padx=8,
                pady=4,
            )
            label.pack()
            widget.tooltip = tooltip

        def on_leave(_event: Any) -> None:
            if hasattr(widget, "tooltip"):
                widget.tooltip.destroy()
                del widget.tooltip

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def _add_separator(self) -> None:
        """Add visual separator."""
        ttk.Separator(self.frame, orient="horizontal").pack(fill="x", padx=20, pady=8)

    def on_analysis_error(self, message: dict[str, Any]) -> None:
        """Handle analysis error from main window."""
        self.analyze_btn.config(state="normal", text="Analyze Canvas & Palette")
        self.analysis_status.config(text=f"Analysis failed: {message['error']}")
