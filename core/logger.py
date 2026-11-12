"""Centralized thread-safe logging into a dedicated logs/ directory with rotation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_LOGS_DIR, get_config, resolve_writable_path

DEFAULT_MAX_LOG_FILES: int = 7


def _prune_old_log_files(logs_dir: Path, max_files: int) -> None:
    """Keep at most ``max_files`` newest ``placebot_*.log`` files inside ``logs_dir``."""
    if max_files <= 0 or not logs_dir.is_dir():
        return
    try:
        candidates = sorted(
            logs_dir.glob("placebot_*.log"),
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        for stale_file in candidates[max_files:]:
            try:
                stale_file.unlink()
            except OSError:
                continue
    except OSError:
        return


class PlaceBotLogger:
    """Centralized logging system writing UTF-8 daily logs into ``logs/``."""

    def __init__(
        self,
        log_file: str | None = None,
        *,
        max_log_files: int | None = None,
    ) -> None:
        """Initialize console and UTF-8 file handlers inside ``logs/``."""
        self.logger = logging.getLogger("PlaceBot")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        self._close_handlers()

        detailed_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        simple_formatter = logging.Formatter("%(levelname)s: %(message)s")

        cfg = get_config()
        log_to_console = bool(cfg.get("logging.log_to_console", True))
        log_to_file = bool(cfg.get("logging.log_to_file", True))
        effective_max_files = (
            max_log_files
            if max_log_files is not None
            else int(cfg.get("logging.max_log_files", DEFAULT_MAX_LOG_FILES))
        )

        if log_to_console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(simple_formatter)
            self.logger.addHandler(console_handler)

        self.log_file_path: str | None = None
        if log_to_file or log_file is not None:
            resolved_log = (
                resolve_writable_path(log_file)
                if log_file is not None
                else resolve_writable_path(
                    f"{DEFAULT_LOGS_DIR}/placebot_{datetime.now().strftime('%Y%m%d')}.log"
                )
            )
            try:
                target_path = Path(resolved_log)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(
                    target_path, encoding="utf-8"
                )
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(detailed_formatter)
                self.logger.addHandler(file_handler)
                self.log_file_path = str(target_path.resolve())
                if log_file is None:
                    _prune_old_log_files(target_path.parent, effective_max_files)
            except OSError as exc:
                self.logger.warning(
                    "Could not create log file %s: %s", resolved_log, exc
                )

        self.gui_callback: Callable[[str], None] | None = None

    def _close_handlers(self) -> None:
        """Close and detach all active handlers on the underlying logger."""
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            try:
                handler.close()
            except OSError:
                pass

    def close(self) -> None:
        """Flush and close all active log handlers."""
        self.gui_callback = None
        self._close_handlers()

    def set_gui_callback(self, callback: Callable[[str], None] | None) -> None:
        """Set or clear thread-safe callback function for GUI message display."""
        self.gui_callback = callback

    def _log_to_gui(self, level: str, message: str) -> None:
        """Send formatted message to GUI callback if registered."""
        if self.gui_callback is not None:
            self.gui_callback(f"[{level}] {message}")

    def debug(self, message: str) -> None:
        """Log a debug-level message to file."""
        self.logger.debug(message)

    def info(self, message: str) -> None:
        """Log an info-level message to file, console, and GUI."""
        self.logger.info(message)
        self._log_to_gui("INFO", message)

    def warning(self, message: str) -> None:
        """Log a warning-level message to file, console, and GUI."""
        self.logger.warning(message)
        self._log_to_gui("WARNING", message)

    def error(self, message: str) -> None:
        """Log an error-level message to file, console, and GUI."""
        self.logger.error(message)
        self._log_to_gui("ERROR", message)

    def critical(self, message: str) -> None:
        """Log a critical-level message to file, console, and GUI."""
        self.logger.critical(message)
        self._log_to_gui("CRITICAL", message)

    def analysis_start(self) -> None:
        """Log start of canvas and palette analysis."""
        self.info("Starting canvas and palette analysis...")

    def analysis_complete(self, pixel_count: int, colors_found: int) -> None:
        """Log successful completion of canvas and palette analysis."""
        self.info(
            f"Analysis completed successfully. Found {pixel_count} pixels and {colors_found} colors."
        )

    def analysis_error(self, error: str) -> None:
        """Log analysis error."""
        self.error(f"Analysis failed: {error}")

    def bot_start(self, pixel_limit: int) -> None:
        """Log start of live painting run."""
        self.info(f"Starting bot with pixel limit: {pixel_limit}")

    def bot_progress(self, painted: int, total: int, color: str) -> None:
        """Log painting progress update."""
        self.debug(f"Painting progress: {painted}/{total} pixels with {color}")

    def bot_complete(
        self,
        total_painted: int,
        limit_reached: bool,
        *,
        stopped_by_user: bool = False,
        cancelled_by_mouse: bool = False,
    ) -> None:
        """Log completion or interruption of a painting run accurately."""
        if stopped_by_user:
            self.info(
                f"Bot stopped by user. Total pixels painted: {total_painted}"
            )
        elif cancelled_by_mouse:
            self.info(
                f"Bot canceled by mouse movement. Total pixels painted: {total_painted}"
            )
        elif limit_reached:
            self.info(
                f"Bot stopped - pixel limit reached! Total pixels painted: {total_painted}"
            )
        else:
            self.info(
                f"Painting completed successfully! Total pixels painted: {total_painted}"
            )

    def bot_error(self, error: str) -> None:
        """Log unhandled bot worker error."""
        self.error(f"Bot error: {error}")


_logger_instance: PlaceBotLogger | None = None


def get_logger() -> PlaceBotLogger:
    """Get or initialize the global ``PlaceBotLogger`` instance."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = PlaceBotLogger()
    return _logger_instance
