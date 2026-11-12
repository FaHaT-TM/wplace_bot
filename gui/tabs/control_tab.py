"""Bot control tab for pixel limits, live painting, session resume, Dry-Run, and logs."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from core.config import (
    DEFAULT_PIXEL_LIMIT,
    MAX_PIXEL_LIMIT,
    MIN_PIXEL_LIMIT,
    clamp_pixel_limit,
    get_config,
    validate_pixel_limit,
)
from core.exceptions import ConfigError
from core.paint_planner import format_duration
from core.site_counter import read_site_pixel_counter

DEBOUNCE_SAVE_MS: int = 500


class ControlTab:
    """Control tab for running, resuming, simulating (Dry-Run), and stopping the bot."""

    status_label: Any = None
    session_info_label: Any = None
    progress_var: Any = None
    progress_bar: Any = None
    start_btn: Any = None
    resume_btn: Any = None
    reset_progress_btn: Any = None
    dry_run_btn: Any = None
    stop_btn: Any = None
    log_text: Any = None
    pixel_limit_var: Any = None
    pixel_limit_entry: Any = None
    pixel_limit_scale: Any = None
    site_sync_label: Any = None
    sync_limit_btn: Any = None
    reanalyze_var: Any = None
    dry_run_var: Any = None
    _manual_limit_override: bool = False
    _sync_in_progress: bool = False
    _pending_start_after_analysis: bool = False
    _pending_dry_run_after_analysis: bool = False

    def __init__(self, parent: Any, main_window: Any) -> None:
        """Initialize the bot control tab."""
        self.parent = parent
        self.main_window = main_window
        self.data_manager = main_window.data_manager
        self.bot_worker = main_window.bot_worker
        self.message_queue = main_window.message_queue

        self.max_pixel_limit: int = int(
            get_config().get("bot.max_pixel_limit", MAX_PIXEL_LIMIT)
        )
        self._syncing_pixel_limit: bool = False
        self._manual_limit_override: bool = False
        self._sync_in_progress: bool = False
        self._last_valid_pixel_limit: int = DEFAULT_PIXEL_LIMIT
        self._save_timer: Any = None
        self._pending_start_after_analysis: bool = False
        self._pending_dry_run_after_analysis: bool = False

        self.frame = ttk.Frame(parent)

        self.status_label = None
        self.session_info_label = None
        self.progress_var = None
        self.progress_bar = None
        self.start_btn = None
        self.resume_btn = None
        self.reset_progress_btn = None
        self.dry_run_btn = None
        self.stop_btn = None
        self.log_text = None
        self.pixel_limit_var = None
        self.pixel_limit_entry = None
        self.pixel_limit_scale = None
        self.site_sync_label = None
        self.sync_limit_btn = None
        self.reanalyze_var = None
        self.dry_run_var = None

        self._create_ui()

    def _create_ui(self) -> None:
        """Create control tab UI."""
        self._create_status_frame()
        self._add_separator()
        self._create_bot_settings()
        self._add_separator()
        self._create_log_frame()

    def _create_status_frame(self) -> None:
        """Create status, session progress, and Start / Resume / Dry Run / Stop controls."""
        status_frame = ttk.LabelFrame(self.frame, text="Status", padding=10)
        status_frame.pack(fill="x", padx=10, pady=5)

        self.status_label = ttk.Label(
            status_frame, text="Ready", font=("Arial", 12, "bold")
        )
        self.status_label.pack(pady=5)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            status_frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(fill="x", pady=5)

        button_frame = ttk.Frame(status_frame)
        button_frame.pack(pady=8)

        self.start_btn = ttk.Button(
            button_frame,
            text="Start Painting",
            command=self._start_bot,
            state="disabled",
        )
        self.start_btn.pack(side="left", padx=4, pady=4)

        self.resume_btn = ttk.Button(
            button_frame,
            text="Resume Session",
            command=self._resume_bot,
            state="disabled",
        )
        self.resume_btn.pack(side="left", padx=4, pady=4)

        self.dry_run_btn = ttk.Button(
            button_frame,
            text="Dry Run (Preview)",
            command=self._start_dry_run,
            state="disabled",
        )
        self.dry_run_btn.pack(side="left", padx=4, pady=4)

        self.stop_btn = ttk.Button(
            button_frame,
            text="Stop",
            command=self._stop_bot,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=4, pady=4)

        session_frame = ttk.Frame(status_frame)
        session_frame.pack(fill="x", pady=(2, 2))
        self.session_info_label = ttk.Label(
            session_frame,
            text="Session progress: No saved progress",
            foreground="gray",
        )
        self.session_info_label.pack(side="left")

        self.reset_progress_btn = ttk.Button(
            session_frame,
            text="Reset Progress",
            command=self._reset_progress,
            state="disabled",
        )
        self.reset_progress_btn.pack(side="right")

        scaled_font = self.main_window.get_scaled_font(9, "italic")
        self.cancel_info = ttk.Label(
            status_frame,
            text="Press Stop, ESC, or move mouse to cancel bot",
            foreground="blue",
            font=scaled_font,
        )
        self.cancel_info.pack(pady=(5, 0))

    def _create_bot_settings(self) -> None:
        """Create bot settings frame."""
        bot_settings_frame = ttk.LabelFrame(self.frame, text="Bot Settings", padding=10)
        bot_settings_frame.pack(fill="x", padx=10, pady=5)

        limit_frame = ttk.Frame(bot_settings_frame)
        limit_frame.pack(fill="x", pady=5)

        ttk.Label(limit_frame, text="Pixel Limit (stop after painting):").pack(
            side="left"
        )

        raw_saved_limit = self.data_manager.user_settings["preferences"].get(
            "pixel_limit", DEFAULT_PIXEL_LIMIT
        )
        saved_pixel_limit = clamp_pixel_limit(
            raw_saved_limit,
            default=DEFAULT_PIXEL_LIMIT,
            min_limit=MIN_PIXEL_LIMIT,
            max_limit=self.max_pixel_limit,
        )
        self._last_valid_pixel_limit = saved_pixel_limit
        self.pixel_limit_var = tk.IntVar(value=saved_pixel_limit)

        vcmd = (self.parent.register(self._validate_digit_input), "%P")

        self.pixel_limit_entry = ttk.Entry(
            limit_frame,
            textvariable=self.pixel_limit_var,
            width=8,
            validate="key",
            validatecommand=vcmd,
        )
        self.pixel_limit_entry.pack(side="right", padx=(5, 0))
        self.pixel_limit_entry.bind("<KeyRelease>", self._on_pixel_limit_entry_change)
        self.pixel_limit_entry.bind("<FocusOut>", self._on_pixel_limit_entry_change)

        self._setup_validation_styles()

        ttk.Label(limit_frame, text="pixels").pack(side="right", padx=(5, 5))

        slider_frame = ttk.Frame(bot_settings_frame)
        slider_frame.pack(fill="x", pady=2)

        ttk.Label(slider_frame, text="Quick Select:").pack(side="left")
        self.pixel_limit_scale = ttk.Scale(
            slider_frame,
            from_=MIN_PIXEL_LIMIT,
            to=self.max_pixel_limit,
            orient="horizontal",
            command=self._update_pixel_limit_from_slider,
        )
        self._syncing_pixel_limit = True
        try:
            self.pixel_limit_scale.set(saved_pixel_limit)
        finally:
            self._syncing_pixel_limit = False
        self.pixel_limit_scale.pack(side="right", fill="x", expand=True, padx=(10, 0))

        sync_frame = ttk.Frame(bot_settings_frame)
        sync_frame.pack(fill="x", pady=(6, 2))

        self.site_sync_label = ttk.Label(
            sync_frame,
            text="Site Counter: Checking wplace.live...",
            foreground="gray",
        )
        self.site_sync_label.pack(side="left")

        self.sync_limit_btn = ttk.Button(
            sync_frame,
            text="Sync with Site",
            command=lambda: self.request_site_limit_sync(phase="manual"),
        )
        self.sync_limit_btn.pack(side="right")

        options_frame = ttk.Frame(bot_settings_frame)
        options_frame.pack(fill="x", pady=(10, 0))

        saved_reanalyze = self.data_manager.user_settings["preferences"].get(
            "reanalyze_before_start", True
        )
        self.reanalyze_var = tk.BooleanVar(value=saved_reanalyze)

        reanalyze_cb = ttk.Checkbutton(
            options_frame,
            text="Reanalyze before starting",
            variable=self.reanalyze_var,
            command=self._on_reanalyze_change,
        )
        reanalyze_cb.pack(side="left")

        self.dry_run_var = tk.BooleanVar(value=False)
        dry_run_cb = ttk.Checkbutton(
            options_frame,
            text="Dry-run mode (no mouse clicks)",
            variable=self.dry_run_var,
            command=self._on_dry_run_mode_toggle,
        )
        dry_run_cb.pack(side="left", padx=(20, 0))

        self._update_start_button_state()
        self.refresh_session_status()

    def _create_log_frame(self) -> None:
        """Create log frame."""
        log_frame = ttk.LabelFrame(self.frame, text="Log", padding=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = tk.Text(log_frame, state="disabled")
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scrollbar.set)

        self.log_text.pack(side="left", fill="both", expand=True)
        log_scrollbar.pack(side="right", fill="y")

    def get_pixel_limit_or_last_valid(self) -> int:
        """Return validated pixel limit, or last valid value if Entry is temporarily invalid."""
        try:
            raw_value = self.pixel_limit_var.get()
            valid_value = validate_pixel_limit(
                raw_value,
                min_limit=MIN_PIXEL_LIMIT,
                max_limit=self.max_pixel_limit,
            )
            self._last_valid_pixel_limit = valid_value
            return valid_value
        except (tk.TclError, ConfigError, ValueError, TypeError):
            return self._last_valid_pixel_limit

    def refresh_session_status(self) -> None:
        """Refresh saved session progress indicator and Resume / Reset button states."""
        has_resume_fn = getattr(self.data_manager, "has_resumable_progress", None)
        can_resume = bool(has_resume_fn()) if callable(has_resume_fn) else False
        is_busy = bool(getattr(self.main_window, "is_running", False))

        btn_state = "normal" if (can_resume and not is_busy) else "disabled"
        if self.resume_btn is not None:
            self.resume_btn.config(state=btn_state)
        if self.reset_progress_btn is not None:
            self.reset_progress_btn.config(state=btn_state)

        if self.session_info_label is not None:
            if can_resume:
                summary_fn = getattr(self.data_manager, "get_progress_summary", None)
                summary = (
                    summary_fn()
                    if callable(summary_fn)
                    else {"painted": 0, "total_grid": 0}
                )
                self.session_info_label.config(
                    text=(
                        f"Saved session: {summary['painted']} pixels painted "
                        f"({summary['total_grid']} grid cells) — ready to resume"
                    ),
                    foreground="green",
                )
            else:
                self.session_info_label.config(
                    text="Session progress: No saved progress",
                    foreground="gray",
                )

    def _update_pixel_limit_from_slider(self, value: str | float) -> None:
        """Update pixel limit Entry when slider changes."""
        if self._syncing_pixel_limit:
            return

        new_value = clamp_pixel_limit(
            round(float(value)),
            default=self._last_valid_pixel_limit,
            min_limit=MIN_PIXEL_LIMIT,
            max_limit=self.max_pixel_limit,
        )
        self._last_valid_pixel_limit = new_value
        self._manual_limit_override = True

        self._syncing_pixel_limit = True
        try:
            self.pixel_limit_var.set(new_value)
        finally:
            self._syncing_pixel_limit = False

        if self.pixel_limit_entry is not None:
            self.pixel_limit_entry.config(style="TEntry")

        self._debounced_save()

    def _validate_digit_input(self, value: str) -> bool:
        """Validate that input contains only digits (or is temporarily empty while editing)."""
        if value == "":
            return True
        return value.isdigit()

    def _debounced_save(self) -> None:
        """Save settings with debouncing to prevent UI freezing."""
        if getattr(self, "_save_timer", None) is not None:
            self.main_window.root.after_cancel(self._save_timer)
        self._save_timer = self.main_window.root.after(
            DEBOUNCE_SAVE_MS, self.main_window.save_user_settings
        )

    def _on_pixel_limit_entry_change(self, event: Any = None) -> None:
        """Handle manual entry changes for pixel limit with validation and slider sync."""
        _ = event
        if self._syncing_pixel_limit:
            return

        try:
            raw_value = self.pixel_limit_var.get()
        except tk.TclError:
            self.pixel_limit_entry.config(style="Warning.TEntry")
            return

        try:
            valid_value = validate_pixel_limit(
                raw_value,
                min_limit=MIN_PIXEL_LIMIT,
                max_limit=self.max_pixel_limit,
            )
        except ConfigError:
            self.pixel_limit_entry.config(style="Warning.TEntry")
            return

        self._last_valid_pixel_limit = valid_value
        self._manual_limit_override = True
        self.pixel_limit_entry.config(style="TEntry")
        self._syncing_pixel_limit = True
        try:
            self.pixel_limit_scale.set(valid_value)
        finally:
            self._syncing_pixel_limit = False
        self._debounced_save()

    def request_site_limit_sync(self, phase: str = "manual") -> None:
        """Asynchronously read the wplace.live pixel counter without blocking the GUI."""
        if getattr(self, "_sync_in_progress", False):
            return
        if phase != "manual" and self.site_sync_label is None:
            return
        msg_queue = getattr(self, "message_queue", None) or getattr(
            self.main_window, "message_queue", None
        )
        if msg_queue is None:
            return

        self._sync_in_progress = True
        if self.site_sync_label is not None and phase == "manual":
            self.site_sync_label.config(
                text="Site Counter: Reading screen...", foreground="gray"
            )

        def _worker() -> None:
            try:
                reading = read_site_pixel_counter(
                    data_manager=self.data_manager,
                    phase=phase,
                )
                msg_queue.put(
                    {
                        "type": "site_limit_synced",
                        "reading": reading,
                        "phase": phase,
                    }
                )
            finally:
                self._sync_in_progress = False

        threading.Thread(target=_worker, daemon=True).start()

    def apply_site_counter_reading(
        self, reading: Any, phase: str = "manual"
    ) -> None:
        """Apply a ``SiteCounterReading`` on the Tkinter main thread."""
        if reading is None:
            if self.site_sync_label is not None:
                self.site_sync_label.config(
                    text="Site Counter: Not detected on screen (using manual limit)",
                    foreground="gray",
                )
            if phase == "manual":
                self.log_message(
                    "Site pixel counter not detected on screen — keeping current limit."
                )
            return

        slider_max = clamp_pixel_limit(
            int(reading.maximum),
            default=self.max_pixel_limit,
            min_limit=MIN_PIXEL_LIMIT,
            max_limit=self.max_pixel_limit,
        )
        synced_limit = reading.effective_limit(
            min_limit=MIN_PIXEL_LIMIT,
            max_limit=self.max_pixel_limit,
        )

        self._syncing_pixel_limit = True
        try:
            if self.pixel_limit_scale is not None:
                self.pixel_limit_scale.config(to=slider_max)
                self.pixel_limit_scale.set(min(synced_limit, slider_max))
            if self.pixel_limit_var is not None:
                self.pixel_limit_var.set(synced_limit)
            if self.pixel_limit_entry is not None:
                self.pixel_limit_entry.config(style="TEntry")
        finally:
            self._syncing_pixel_limit = False

        self._last_valid_pixel_limit = synced_limit
        self._manual_limit_override = False

        if self.site_sync_label is not None:
            if int(reading.available) > 0:
                self.site_sync_label.config(
                    text=(
                        f"Site Counter: {reading.available} / {reading.maximum} "
                        f"available ({reading.source_label})"
                    ),
                    foreground="green",
                )
            else:
                self.site_sync_label.config(
                    text=(
                        f"Site Counter: 0 / {reading.maximum} "
                        f"(recharging — {reading.source_label})"
                    ),
                    foreground="orange",
                )

        self._debounced_save()

    def _validate_pixel_limit_for_start(self) -> int | None:
        """Validate pixel limit before starting bot and show user feedback on error."""
        try:
            raw_value = self.pixel_limit_var.get()
        except tk.TclError:
            messagebox.showerror(
                "Invalid Input",
                f"Please enter a valid number for pixel limit.\n\n"
                f"• Only numbers are allowed ({MIN_PIXEL_LIMIT}-{self.max_pixel_limit})\n"
                f"• Clear the field and enter a new value\n"
                f"• Use the slider for quick selection",
            )
            self.pixel_limit_entry.focus_set()
            return None

        try:
            return validate_pixel_limit(
                raw_value,
                min_limit=MIN_PIXEL_LIMIT,
                max_limit=self.max_pixel_limit,
            )
        except ConfigError:
            messagebox.showerror(
                "Invalid Pixel Limit",
                f"Pixel limit must be between {MIN_PIXEL_LIMIT} and {self.max_pixel_limit}.\n\n"
                f"Current value: {raw_value}\n\n"
                f"Please adjust the value and try again.",
            )
            self.pixel_limit_entry.focus_set()
            return None

    def _start_bot(self) -> None:
        """Validate inputs and start live painting (or Dry-Run if dry_run_var is checked)."""
        if self.dry_run_var is not None and bool(self.dry_run_var.get()):
            self._start_dry_run()
            return
        self._launch_run(dry_run=False, force_no_reanalyze=False)

    def _resume_bot(self) -> None:
        """Resume painting directly from saved session progress without reanalyzing."""
        is_dry = bool(self.dry_run_var.get()) if self.dry_run_var is not None else False
        self._launch_run(dry_run=is_dry, force_no_reanalyze=True)

    def _reset_progress(self) -> None:
        """Clear saved session progress after user confirmation."""
        ask_fn = getattr(messagebox, "askyesno", None)
        if callable(ask_fn) and not ask_fn(
            "Reset Session Progress",
            "Clear saved painting progress for the current session?\n\n"
            "Previously painted pixels will no longer be tracked locally.",
        ):
            return

        clear_fn = getattr(self.data_manager, "clear_progress", None)
        if callable(clear_fn):
            clear_fn(keep_analysis=True)
        self.refresh_session_status()
        self._update_start_button_state()
        self.log_message("Saved painting progress has been reset.")

    def _start_dry_run(self) -> None:
        """Validate inputs and execute a non-clicking Dry-Run plan and diff preview."""
        self._launch_run(dry_run=True, force_no_reanalyze=False)

    def _launch_run(self, *, dry_run: bool, force_no_reanalyze: bool) -> None:
        """Shared launch pipeline for live painting, session resume, and Dry-Run."""
        if self._validate_pixel_limit_for_start() is None:
            return

        if getattr(self, "_save_timer", None) is not None:
            self.main_window.root.after_cancel(self._save_timer)
            self._save_timer = None
        self.main_window.save_user_settings()

        should_reanalyze = bool(self.reanalyze_var.get()) and not force_no_reanalyze

        if should_reanalyze:
            has_regions_fn = getattr(self.data_manager, "has_regions", None)
            if callable(has_regions_fn) and not has_regions_fn():
                messagebox.showerror(
                    "Regions Not Selected",
                    "Please select both Canvas and Palette regions before starting.\n\n"
                    "Go to Setup tab → Select Canvas & Select Palette.",
                )
                return

            if self.main_window.setup_tab and hasattr(
                self.main_window.setup_tab, "_analyze_regions"
            ):
                action_label = "dry-run preview" if dry_run else "painting"
                self.log_message(f"Starting reanalysis before {action_label}...")
                self._pending_start_after_analysis = not dry_run
                self._pending_dry_run_after_analysis = dry_run
                self._prepare_bot_start(dry_run=dry_run)
                self.main_window.setup_tab._analyze_regions()
            else:
                messagebox.showerror(
                    "Analysis Error",
                    "Cannot perform reanalysis.\n\n"
                    "Possible solutions:\n"
                    "• Go to Setup tab and run analysis manually\n"
                    "• Uncheck 'Reanalyze before starting' option\n"
                    "• Restart the application",
                )
        else:
            if not self.data_manager.has_analysis_data():
                messagebox.showerror(
                    "No Analysis Data",
                    "Analysis is required before starting.\n\n"
                    "Please choose one of these options:\n\n"
                    "1. Check 'Reanalyze before starting' (recommended)\n"
                    "2. Go to Setup tab → Select regions → Run analysis\n\n"
                    "The bot needs to know where to paint and what colors are available.",
                )
                return
            if force_no_reanalyze:
                self.log_message(
                    "Resuming from saved session progress without reanalysis..."
                )
            self._pending_start_after_analysis = False
            self._pending_dry_run_after_analysis = False
            self._prepare_bot_start(dry_run=dry_run)
            self._execute_bot_start(dry_run=dry_run)

    def _stop_bot(self) -> None:
        """Immediately halt reanalysis and/or active painting (Emergency Stop)."""
        was_pending_analysis = bool(
            getattr(self, "_pending_start_after_analysis", False)
            or getattr(self, "_pending_dry_run_after_analysis", False)
        )
        self._pending_start_after_analysis = False
        self._pending_dry_run_after_analysis = False

        if self.stop_btn is not None:
            self.stop_btn.config(state="disabled")

        analysis_worker = getattr(self.main_window, "analysis_worker", None)
        if analysis_worker is not None and hasattr(analysis_worker, "stop_analysis"):
            analysis_worker.stop_analysis()

        self.bot_worker.stop_bot()

        bot_thread = getattr(self.bot_worker, "thread", None)
        bot_alive = bot_thread is not None and bot_thread.is_alive()

        if was_pending_analysis and not bot_alive:
            self.main_window.is_running = False
            self._update_start_button_state()
            self.refresh_session_status()
            self.status_label.config(text="Stopped")
            self.log_message("Operation canceled by user before painting started.")
            self.request_site_limit_sync(phase="stopped")
        else:
            self.status_label.config(text="Stopping...")
            self.log_message("Emergency Stop requested - halting bot...")

    def _prepare_bot_start(self, *, dry_run: bool = False) -> None:
        """Prepare UI controls for active bot or Dry-Run execution."""
        self.main_window.is_running = True
        self.start_btn.config(state="disabled")
        if self.resume_btn is not None:
            self.resume_btn.config(state="disabled")
        if self.reset_progress_btn is not None:
            self.reset_progress_btn.config(state="disabled")
        if self.dry_run_btn is not None:
            self.dry_run_btn.config(state="disabled")
        if self.stop_btn is not None:
            self.stop_btn.config(state="normal")
        if dry_run:
            self.status_label.config(
                text="Dry-Run: moving cursor without clicking (Stop / ESC / move mouse to cancel)"
            )
            self.log_message(
                "Starting Dry-Run cursor simulation (0 mouse clicks)... "
                "Press Stop, ESC, or move mouse to cancel."
            )
        else:
            self.status_label.config(
                text="Painting... (press Stop or move mouse to cancel)"
            )
            self.log_message(
                "Starting painting bot... Press Stop or move mouse to cancel."
            )

    def _execute_bot_start(self, *, dry_run: bool = False) -> None:
        """Execute live painting or Dry-Run planning after validation/reanalysis."""
        pixel_limit = self._validate_pixel_limit_for_start()
        if pixel_limit is None:
            self._pending_start_after_analysis = False
            self._pending_dry_run_after_analysis = False
            self.main_window.is_running = False
            if self.stop_btn is not None:
                self.stop_btn.config(state="disabled")
            self._update_start_button_state()
            self.refresh_session_status()
            self.status_label.config(text="Ready")
            return

        enabled_colors = self.main_window.get_enabled_colors()
        settings = {
            "pixel_limit": pixel_limit,
            "tolerance": self.main_window.setup_tab.tolerance_var.get(),
            "delay": self.main_window.setup_tab.delay_var.get(),
            "dry_run": dry_run,
            "auto_sync_site_limit": True,
            "manual_limit_override": bool(
                getattr(self, "_manual_limit_override", False)
            ),
        }
        self.bot_worker.start_bot(self.message_queue, enabled_colors, settings)

    def _on_reanalyze_change(self) -> None:
        """Handle reanalyze checkbox change."""
        self._debounced_save()
        self._update_start_button_state()

    def _on_dry_run_mode_toggle(self) -> None:
        """Update primary Start button text when Dry-run mode checkbox is toggled."""
        if self.start_btn is not None and self.dry_run_var is not None:
            label = "Run Dry-Run" if self.dry_run_var.get() else "Start Painting"
            self.start_btn.config(text=label)

    def _update_start_button_state(self) -> None:
        """Update Start and Dry Run button states based on analysis data and reanalyze checkbox."""
        should_enable = self.data_manager.has_analysis_data() or self.reanalyze_var.get()
        target_state = "normal" if should_enable else "disabled"
        self.start_btn.config(state=target_state)
        if self.dry_run_btn is not None:
            self.dry_run_btn.config(state=target_state)

    def log_message(self, message: str) -> None:
        """Append a message line to the log widget (must be called on main thread)."""
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def update_progress(self, progress: float, status_text: str) -> None:
        """Update progress bar and status."""
        self.progress_var.set(progress)
        self.status_label.config(text=status_text)

    def on_analysis_error(self, error_message: str) -> None:
        """Reset running state and controls if reanalysis fails."""
        self._pending_start_after_analysis = False
        self._pending_dry_run_after_analysis = False
        self.main_window.is_running = False
        if self.stop_btn is not None:
            self.stop_btn.config(state="disabled")
        self._update_start_button_state()
        self.refresh_session_status()
        self.status_label.config(text=f"Analysis failed: {error_message}")

    def on_dry_run_complete(
        self,
        plan: Any,
        report: str,
        diff_image_path: str | None = None,
        *,
        simulated_pixels: int | None = None,
        stopped_by_user: bool = False,
        cancelled_by_mouse: bool = False,
    ) -> None:
        """Handle Dry-Run completion, display per-color report, and restore controls."""
        _ = diff_image_path
        self._pending_start_after_analysis = False
        self._pending_dry_run_after_analysis = False
        self.main_window.is_running = False
        if self.stop_btn is not None:
            self.stop_btn.config(state="disabled")
        self._update_start_button_state()
        self.refresh_session_status()

        traced = (
            plan.total_planned_pixels
            if simulated_pixels is None
            else int(simulated_pixels)
        )
        if self.progress_var is not None:
            pct = (
                (traced / plan.total_planned_pixels) * 100.0
                if plan.total_planned_pixels > 0
                else 100.0
            )
            self.progress_var.set(pct)

        eta = format_duration(plan.estimated_seconds)
        if stopped_by_user:
            self.status_label.config(
                text=(
                    f"Dry-run stopped by user ({traced}/{plan.total_planned_pixels} "
                    f"pixels traced, 0 clicks)"
                )
            )
        elif cancelled_by_mouse:
            self.status_label.config(
                text=(
                    f"Dry-run canceled by mouse ({traced}/{plan.total_planned_pixels} "
                    f"pixels traced, 0 clicks)"
                )
            )
        else:
            self.status_label.config(
                text=(
                    f"Dry-run complete: {traced} pixels traced, "
                    f"ETA {eta} (0 clicks)"
                )
            )
        self.log_message(report)

    def on_bot_complete(
        self,
        total_painted: int,
        limit_reached: bool,
        stopped_by_user: bool = False,
        cancelled_by_mouse: bool = False,
    ) -> None:
        """Handle bot completion and restore Start / Resume / Dry Run / Stop states."""
        self._pending_start_after_analysis = False
        self._pending_dry_run_after_analysis = False
        self.main_window.is_running = False
        if self.stop_btn is not None:
            self.stop_btn.config(state="disabled")
        self.start_btn.config(state="normal")
        if self.dry_run_btn is not None:
            self.dry_run_btn.config(state="normal")
        self.refresh_session_status()

        if stopped_by_user:
            self.status_label.config(text=f"Stopped by user ({total_painted} pixels)")
            self.log_message(
                f"Bot stopped by Emergency Stop. Total pixels painted: {total_painted}"
            )
        elif cancelled_by_mouse:
            self.status_label.config(
                text=f"Canceled by mouse ({total_painted} pixels)"
            )
            self.log_message(
                f"Bot canceled by mouse movement. Total pixels painted: {total_painted}"
            )
        elif limit_reached:
            self.status_label.config(
                text=f"Pixel limit reached! ({total_painted} pixels)"
            )
            self.log_message(
                f"Bot stopped - pixel limit reached! Total pixels painted: {total_painted}"
            )
        else:
            self.status_label.config(
                text=f"Painting complete! ({total_painted} pixels)"
            )
            self.log_message(
                f"Painting completed successfully! Total pixels painted: {total_painted}"
            )

    def on_bot_error(self, error_message: str) -> None:
        """Handle bot error and restore Start / Resume / Dry Run / Stop states."""
        self._pending_start_after_analysis = False
        self._pending_dry_run_after_analysis = False
        self.main_window.is_running = False
        if self.stop_btn is not None:
            self.stop_btn.config(state="disabled")
        self.start_btn.config(state="normal")
        if self.dry_run_btn is not None:
            self.dry_run_btn.config(state="normal")
        self.refresh_session_status()
        self.status_label.config(text=f"Error: {error_message}")
        self.log_message(f"Bot error: {error_message}")

    def _setup_validation_styles(self) -> None:
        """Setup validation styles for input fields."""
        style = ttk.Style()
        style.configure(
            "Warning.TEntry", fieldbackground="#fff3cd", bordercolor="#ffc107"
        )
        style.configure(
            "Error.TEntry", fieldbackground="#f8d7da", bordercolor="#dc3545"
        )

    def enable_start_button(self) -> None:
        """Enable buttons after successful analysis and continue pending run if any."""
        self.start_btn.config(state="normal")
        if self.dry_run_btn is not None:
            self.dry_run_btn.config(state="normal")
        self.refresh_session_status()

        if getattr(self, "_pending_dry_run_after_analysis", False):
            self._pending_dry_run_after_analysis = False
            if self.main_window.is_running:
                self._execute_bot_start(dry_run=True)
        elif getattr(self, "_pending_start_after_analysis", False):
            self._pending_start_after_analysis = False
            if self.main_window.is_running:
                self._execute_bot_start(dry_run=False)

    def refresh_fonts(self) -> None:
        """Refresh fonts when scaling changes."""
        if hasattr(self, "cancel_info"):
            font = self.main_window.get_scaled_font(9, "italic")
            self.cancel_info.config(font=font)

    def _add_separator(self) -> None:
        """Add visual separator."""
        ttk.Separator(self.frame, orient="horizontal").pack(fill="x", padx=20, pady=8)
