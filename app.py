"""Main GUI application entry point for Place Bot."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any

from core import (
    AnalysisWorker,
    BotWorker,
    DataManager,
    format_duration,
    get_config,
    get_logger,
)
from gui.tabs import ColorsTab, ControlTab, PreviewTab, SetupTab

config = get_config()

MAX_MESSAGES_PER_CYCLE: int = 50
QUEUE_PROCESS_INTERVAL: int = 100  # milliseconds
WORKER_JOIN_TIMEOUT_SECONDS: float = 1.0


class PlaceBotGUI:
    """Main GUI application for Place Bot."""

    def __init__(self, root: tk.Tk) -> None:
        """Initialize the main window, core workers, tabs, and message queue loop."""
        self.root = root
        self.root.title("Place Bot - Pixel Art Automation")
        self.root.geometry(config.get("ui.window_size", "900x700"))
        self.root.minsize(700, 550)

        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except tk.TclError:
            pass

        self._center_window()

        # Thread-safe communication queue MUST exist before attaching logger callback
        self.message_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        # Initialize core components
        self.data_manager = DataManager()
        self.analysis_worker = AnalysisWorker(self.data_manager)
        self.bot_worker = BotWorker(self.data_manager)
        self.logger = get_logger()
        self.logger.set_gui_callback(self._gui_log_callback)

        # Font scaling (load from settings, default to Extra Large 125%)
        self.font_scale: float = float(
            self.data_manager.user_settings["preferences"].get("font_scale", 1.25)
        )

        self._setup_styling()

        # State variables
        self.is_running: bool = False

        # Tab references
        self.setup_tab: SetupTab | None = None
        self.colors_tab: ColorsTab | None = None
        self.control_tab: ControlTab | None = None
        self.preview_tab: PreviewTab | None = None

        # Setup UI and start processing
        self.setup_ui()
        self.load_saved_regions()
        self._setup_cleanup()
        self._setup_keyboard_shortcuts()
        self.process_queue()

    def _center_window(self) -> None:
        """Center the window on screen."""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _setup_styling(self) -> None:
        """Setup modern UI styling."""
        style = ttk.Style()

        for theme_name in ("vista", "clam", "default"):
            try:
                style.theme_use(theme_name)
                break
            except tk.TclError:
                continue

        base_title_size = int(12 * self.font_scale)
        base_body_size = int(9 * self.font_scale)

        title_font = ("Segoe UI", base_title_size, "bold")
        body_font = ("Segoe UI", base_body_size)

        colors = {
            "primary": "#0078d4",
            "success": "#107c10",
            "error": "#d13438",
            "warning": "#ff8c00",
            "info": "#0078d4",
            "text": "#323130",
            "bg": "#faf9f8",
            "hover": "#f3f2f1",
            "border": "#c8c6c4",
        }

        style.configure("Title.TLabel", font=title_font, foreground=colors["text"])
        style.configure(
            "Success.TLabel",
            foreground=colors["success"],
            font=(body_font[0], body_font[1], "bold"),
        )
        style.configure(
            "Error.TLabel",
            foreground=colors["error"],
            font=(body_font[0], body_font[1], "bold"),
        )
        style.configure(
            "Warning.TLabel",
            foreground=colors["warning"],
            font=(body_font[0], body_font[1], "bold"),
        )
        style.configure("Info.TLabel", foreground=colors["info"], font=body_font)

        style.configure("TButton", font=body_font, padding=(12, 6))
        style.map(
            "TButton",
            background=[("active", "#f3f2f1"), ("pressed", "#e1dfdd")],
            foreground=[("active", colors["text"]), ("pressed", colors["text"])],
            bordercolor=[("active", "#c8c6c4"), ("pressed", "#a19f9d")],
            relief=[("pressed", "sunken")],
        )

        style.configure("TNotebook", background=colors["bg"])
        style.configure("TNotebook.Tab", font=body_font, padding=(12, 8))
        style.map(
            "TNotebook.Tab",
            background=[("selected", colors["bg"]), ("active", "#f3f2f1")],
            foreground=[("selected", colors["text"]), ("active", colors["text"])],
        )

        style.configure("TLabelframe", font=body_font)
        style.configure("TLabelframe.Label", font=(body_font[0], body_font[1], "bold"))

        style.configure("TLabel", font=body_font)
        style.configure("TCheckbutton", font=body_font)
        style.configure("TRadiobutton", font=body_font)
        style.configure("TCombobox", font=body_font)
        style.configure("TEntry", font=body_font)

        style.map("TCheckbutton", background=[("active", "#f3f2f1")])
        style.map("TRadiobutton", background=[("active", "#f3f2f1")])
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "white"), ("focus", "#f3f2f1")],
        )
        style.map("TEntry", fieldbackground=[("focus", "#f3f2f1")])

        self.root.option_add("*TCombobox*Listbox.font", body_font)
        self.root.option_add("*Text.font", body_font)

    def _setup_keyboard_shortcuts(self) -> None:
        """Setup keyboard shortcuts including Ctrl+S and ESC Emergency Stop."""
        self.root.bind("<Control-s>", lambda e: self.save_user_settings())
        self.root.bind("<Escape>", lambda e: self.emergency_stop())

    def emergency_stop(self) -> None:
        """Trigger Emergency Stop if the bot or reanalysis is running."""
        if self.is_running and self.control_tab is not None:
            self.control_tab._stop_bot()

    def _setup_cleanup(self) -> None:
        """Setup window close cleanup handler."""
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_closing(self) -> None:
        """Gracefully stop background workers, persist settings, and close the window."""
        self.logger.set_gui_callback(None)

        self.analysis_worker.stop_analysis()
        self.bot_worker.stop_bot()

        for worker_thread in (self.analysis_worker.thread, self.bot_worker.thread):
            if worker_thread is not None and worker_thread.is_alive():
                worker_thread.join(timeout=WORKER_JOIN_TIMEOUT_SECONDS)

        self.save_user_settings()
        config.save()
        self.root.destroy()

    def _create_tabs(self, notebook: ttk.Notebook) -> None:
        """Create and add all tabs to notebook."""
        tabs: list[tuple[type[Any], str, str]] = [
            (SetupTab, "Setup", "setup_tab"),
            (ColorsTab, "Color Control", "colors_tab"),
            (PreviewTab, "Preview & Debug", "preview_tab"),
            (ControlTab, "Bot Control", "control_tab"),
        ]

        for tab_class, title, attr_name in tabs:
            tab = tab_class(notebook, self)
            setattr(self, attr_name, tab)
            notebook.add(tab.frame, text=title)

    def setup_ui(self) -> None:
        """Create the main UI layout."""
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self._create_menu_bar()

        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill="both", expand=True)
        self._create_tabs(notebook)

        self._create_status_bar(main_frame)

    def _create_status_bar(self, parent: ttk.Frame) -> None:
        """Create status bar at bottom."""
        self.status_frame = ttk.Frame(parent)
        self.status_frame.pack(fill="x", pady=(5, 0))

        self.status_label = ttk.Label(
            self.status_frame, text="Ready", style="Info.TLabel"
        )
        self.status_label.pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(2, 0))

    def _create_menu_bar(self) -> None:
        """Create menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="Save Settings",
            command=self.save_user_settings,
            accelerator="Ctrl+S",
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_closing)

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)

        font_menu = tk.Menu(view_menu, tearoff=0)
        view_menu.add_cascade(label="Font Size", menu=font_menu)
        font_menu.add_command(
            label="Tiny (80%)", command=lambda: self._change_font_size(0.8)
        )
        font_menu.add_command(
            label="Small (90%)", command=lambda: self._change_font_size(0.9)
        )
        font_menu.add_command(
            label="Normal (100%)", command=lambda: self._change_font_size(1.0)
        )
        font_menu.add_command(
            label="Large (120%)", command=lambda: self._change_font_size(1.2)
        )
        font_menu.add_command(
            label="Extra Large (125%)", command=lambda: self._change_font_size(1.25)
        )
        font_menu.add_command(
            label="Huge (150%)", command=lambda: self._change_font_size(1.5)
        )
        font_menu.add_command(
            label="Giant (200%)", command=lambda: self._change_font_size(2.0)
        )

        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Open Log File", command=self._open_log_file)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(
            label="Keyboard Shortcuts", command=self._show_shortcuts
        )
        help_menu.add_command(label="About", command=self._show_about)

    def _open_log_file(self) -> None:
        """Open today's log file in the OS default viewer."""
        log_file = f"placebot_{datetime.now().strftime('%Y%m%d')}.log"
        if os.path.exists(log_file):
            try:
                if os.name == "nt":
                    os.startfile(log_file)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.call(["open", log_file])
                else:
                    subprocess.call(["xdg-open", log_file])
            except OSError as exc:
                self.logger.error(f"Could not open log file: {exc}")
        else:
            self.update_status("Log file not found", "warning")

    def _show_shortcuts(self) -> None:
        """Show keyboard shortcuts dialog."""
        shortcuts = (
            "Keyboard Shortcuts:\n\n"
            "Ctrl+S - Save Settings\n"
            "ESC - Emergency Stop (when Place Bot window has focus)\n\n"
            "Emergency Stop:\n\n"
            "• Click 'Stop' button in Bot Control tab\n"
            "• Move mouse while bot is running"
        )
        messagebox.showinfo("Keyboard Shortcuts", shortcuts)

    def _show_about(self) -> None:
        """Show about dialog."""
        about_text = (
            "Place Bot v1.6.2\n\n"
            "Pixel Art Automation Tool for Wplace.live\n\n"
            "A Python GUI application for automating\n"
            "pixel art creation on wplace.live canvases."
        )
        messagebox.showinfo("About Place Bot", about_text)

    def _change_font_size(self, scale: float) -> None:
        """Change font size scaling."""
        self.font_scale = scale
        self._setup_styling()

        scaled_font = self.get_scaled_font(9)
        self.root.option_add("*TCombobox*Listbox.font", scaled_font)
        self.root.option_add("*Text.font", scaled_font)

        for tab in (
            self.setup_tab,
            self.colors_tab,
            self.control_tab,
            self.preview_tab,
        ):
            if tab and hasattr(tab, "refresh_fonts"):
                tab.refresh_fonts()

        self.save_user_settings()
        self.update_status(f"Font size changed to {int(scale * 100)}%", "info")

    def get_scaled_font(
        self, base_size: int, weight: str = "normal"
    ) -> tuple[str, int, str]:
        """Get scaled font tuple for tabs to use."""
        scaled_size = int(base_size * self.font_scale)
        return ("Segoe UI", scaled_size, weight)

    def show_spinner(self, widget: Any, text: str = "Loading...") -> None:
        """Show loading spinner on widget."""
        if hasattr(widget, "config"):
            widget.config(text=f"⟳ {text}", state="disabled")

    def hide_spinner(self, widget: Any, original_text: str) -> None:
        """Hide loading spinner and restore widget."""
        if hasattr(widget, "config"):
            widget.config(text=original_text, state="normal")

    def log_message(self, message: str, level: str = "info") -> None:
        """Log message and update status."""
        self.logger.info(message)
        self.update_status(message, level)

    def update_status(self, message: str, status_type: str = "info") -> None:
        """Update status bar message."""
        if hasattr(self, "status_label"):
            style_map = {
                "info": "Info.TLabel",
                "success": "Success.TLabel",
                "error": "Error.TLabel",
                "warning": "Warning.TLabel",
            }
            self.status_label.config(
                text=message, style=style_map.get(status_type, "TLabel")
            )

    def _save_preferences(self) -> None:
        """Save UI preferences to data manager."""
        if self.setup_tab:
            self.data_manager.update_preference(
                "color_tolerance", self.setup_tab.tolerance_var.get()
            )
            self.data_manager.update_preference(
                "click_delay", self.setup_tab.delay_var.get()
            )

        if self.control_tab:
            self.data_manager.update_preference(
                "pixel_limit", self.control_tab.get_pixel_limit_or_last_valid()
            )
            self.data_manager.update_preference(
                "reanalyze_before_start", self.control_tab.reanalyze_var.get()
            )

        self.data_manager.update_preference("font_scale", self.font_scale)

    def _save_color_settings(self) -> None:
        """Save color settings from colors tab."""
        if not self.colors_tab:
            return

        for color in self.data_manager.color_palette:
            color_id = str(color["id"])

            if color["name"] in self.colors_tab.color_vars:
                self.data_manager.set_color_setting(
                    color_id,
                    "enabled",
                    self.colors_tab.color_vars[color["name"]].get(),
                )

            if (
                color.get("premium", False)
                and color["name"] in self.colors_tab.bought_vars
            ):
                self.data_manager.set_color_setting(
                    color_id,
                    "bought",
                    self.colors_tab.bought_vars[color["name"]].get(),
                )

    def save_user_settings(self) -> None:
        """Save user settings including UI state."""
        self._save_preferences()
        self._save_color_settings()

    def load_saved_regions(self) -> None:
        """Load and display saved regions."""
        if self.setup_tab:
            self.setup_tab._load_saved_regions()

    def get_enabled_colors(self) -> list[dict[str, Any]]:
        """Get list of enabled and paintable colors from colors tab."""
        if self.colors_tab:
            return self.colors_tab.get_enabled_colors()
        return self.data_manager.get_paintable_colors()

    def _gui_log_callback(self, message: str) -> None:
        """Thread-safe logger callback: enqueue log line for main-thread UI delivery."""
        self.message_queue.put({"type": "log", "message": message})

    def _handle_log(self, message: dict[str, Any]) -> None:
        """Deliver queued log message to ControlTab on the Tkinter main thread."""
        if self.control_tab:
            self.control_tab.log_message(str(message["message"]))

    def _handle_analysis_complete(self, message: dict[str, Any]) -> None:
        """Handle analysis complete message."""
        if self.setup_tab:
            self.setup_tab.on_analysis_complete(message)
        if self.control_tab:
            self.control_tab.enable_start_button()
        if self.preview_tab:
            self.preview_tab.update_stats()
        self.logger.analysis_complete(message["pixel_count"], message["colors_found"])
        self.update_status(
            f"Analysis complete: {message['pixel_count']} pixels, {message['colors_found']} colors",
            "success",
        )

    def _handle_analysis_error(self, message: dict[str, Any]) -> None:
        """Handle analysis error message and reset pending bot state."""
        if self.setup_tab:
            self.setup_tab.on_analysis_error(message)
        if self.control_tab:
            self.control_tab.on_analysis_error(str(message["error"]))
        self.logger.analysis_error(message["error"])
        self.update_status(f"Analysis failed: {message['error']}", "error")

    def _handle_progress(self, message: dict[str, Any]) -> None:
        """Handle progress message."""
        if self.control_tab:
            self.control_tab.update_progress(message["progress"], message["status"])

    def _handle_bot_complete(self, message: dict[str, Any]) -> None:
        """Handle bot complete message."""
        total_painted = int(message.get("total_painted", 0))
        limit_reached = bool(message.get("limit_reached", False))
        cancelled_by_mouse = bool(message.get("cancelled_by_mouse", False))
        stopped_by_user = bool(message.get("stopped_by_user", False))

        if self.control_tab:
            self.control_tab.on_bot_complete(
                total_painted,
                limit_reached,
                stopped_by_user=stopped_by_user,
                cancelled_by_mouse=cancelled_by_mouse,
            )

        self.logger.bot_complete(total_painted, limit_reached)

        if stopped_by_user:
            status_msg = f"Bot stopped by user: {total_painted} pixels painted"
            self.update_status(status_msg, "warning")
        elif cancelled_by_mouse:
            status_msg = (
                f"Bot canceled by mouse movement: {total_painted} pixels painted"
            )
            self.update_status(status_msg, "warning")
        else:
            status_msg = f"Painting complete: {total_painted} pixels" + (
                " (limit reached)" if limit_reached else ""
            )
            self.update_status(status_msg, "success")

    def _handle_bot_error(self, message: dict[str, Any]) -> None:
        """Handle bot error message."""
        if self.control_tab:
            self.control_tab.on_bot_error(message["error"])
        self.logger.bot_error(message["error"])
        self.update_status(f"Bot error: {message['error']}", "error")

    def _handle_dry_run_complete(self, message: dict[str, Any]) -> None:
        """Handle Dry-Run completion message on the Tkinter main thread."""
        plan = message["plan"]
        report = str(message["report"])
        diff_image_path = message.get("diff_image_path")

        if self.control_tab:
            self.control_tab.on_dry_run_complete(plan, report, diff_image_path)
        if self.preview_tab:
            self.preview_tab.show_dry_run_report(plan, report, diff_image_path)

        eta = format_duration(plan.estimated_seconds)
        self.update_status(
            f"Dry-run ready: {plan.total_planned_pixels} pixels across "
            f"{plan.active_color_switches} colors (ETA {eta}, 0 clicks)",
            "success",
        )

    def process_queue(self) -> None:
        """Process queued worker and log messages on the Tkinter main thread."""
        message_handlers = {
            "log": self._handle_log,
            "analysis_complete": self._handle_analysis_complete,
            "analysis_error": self._handle_analysis_error,
            "progress": self._handle_progress,
            "bot_complete": self._handle_bot_complete,
            "bot_error": self._handle_bot_error,
            "dry_run_complete": self._handle_dry_run_complete,
        }

        try:
            for _ in range(MAX_MESSAGES_PER_CYCLE):
                message = self.message_queue.get_nowait()
                handler = message_handlers.get(message["type"])
                if handler:
                    handler(message)
        except queue.Empty:
            pass

        self.root.after(QUEUE_PROCESS_INTERVAL, self.process_queue)


def _enable_dpi_awareness() -> None:
    """Enable per-monitor DPI awareness on Windows before creating the Tk root window."""
    import sys

    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            logging.getLogger(__name__).debug(
                "Could not configure Windows DPI awareness.", exc_info=True
            )


def main() -> int:
    """Main entry point."""
    try:
        _enable_dpi_awareness()
        root = tk.Tk()
        PlaceBotGUI(root)
        root.mainloop()
    except Exception as exc:
        logging.getLogger(__name__).exception("Unhandled error starting GUI")
        logger = get_logger()
        logger.critical(f"Failed to start GUI application: {exc}")
        logger.error(
            "Please ensure tkinter is properly installed and display is available."
        )
        return 1
    return 0


if __name__ == "__main__":
    main()
