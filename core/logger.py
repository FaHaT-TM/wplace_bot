import logging
import os
from datetime import datetime
from typing import Optional

class PlaceBotLogger:
    """Centralized logging system for Place Bot"""
    
    def __init__(self, log_file: Optional[str] = None):
        self.logger = logging.getLogger('PlaceBot')
        self.logger.setLevel(logging.DEBUG)
        
        # Clear existing handlers
        self.logger.handlers.clear()
        
        # Create formatters
        detailed_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        simple_formatter = logging.Formatter('%(levelname)s: %(message)s')
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(simple_formatter)
        self.logger.addHandler(console_handler)
        
        # File handler
        if log_file is None:
            from .config import resolve_writable_path

            log_file = resolve_writable_path(
                f"placebot_{datetime.now().strftime('%Y%m%d')}.log"
            )
        
        try:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(detailed_formatter)
            self.logger.addHandler(file_handler)
        except Exception as e:
            self.logger.warning(f"Could not create log file {log_file}: {e}")
        
        # GUI callback for displaying messages
        self.gui_callback = None
    
    def set_gui_callback(self, callback):
        """Set callback function for GUI message display"""
        self.gui_callback = callback
    
    def _log_to_gui(self, level: str, message: str):
        """Send message to GUI if callback is set"""
        if self.gui_callback:
            self.gui_callback(f"[{level}] {message}")
    
    def debug(self, message: str):
        """Log debug message"""
        self.logger.debug(message)
    
    def info(self, message: str):
        """Log info message"""
        self.logger.info(message)
        self._log_to_gui("INFO", message)
    
    def warning(self, message: str):
        """Log warning message"""
        self.logger.warning(message)
        self._log_to_gui("WARNING", message)
    
    def error(self, message: str):
        """Log error message"""
        self.logger.error(message)
        self._log_to_gui("ERROR", message)
    
    def critical(self, message: str):
        """Log critical message"""
        self.logger.critical(message)
        self._log_to_gui("CRITICAL", message)
    
    def analysis_start(self):
        """Log analysis start"""
        self.info("Starting canvas and palette analysis...")
    
    def analysis_complete(self, pixel_count: int, colors_found: int):
        """Log analysis completion"""
        self.info(f"Analysis completed successfully. Found {pixel_count} pixels and {colors_found} colors.")
    
    def analysis_error(self, error: str):
        """Log analysis error"""
        self.error(f"Analysis failed: {error}")
    
    def bot_start(self, pixel_limit: int):
        """Log bot start"""
        self.info(f"Starting bot with pixel limit: {pixel_limit}")
    
    def bot_progress(self, painted: int, total: int, color: str):
        """Log bot progress"""
        self.debug(f"Painting progress: {painted}/{total} pixels with {color}")
    
    def bot_complete(self, total_painted: int, limit_reached: bool):
        """Log bot completion"""
        if limit_reached:
            self.info(f"Bot stopped - pixel limit reached! Total pixels painted: {total_painted}")
        else:
            self.info(f"Painting completed successfully! Total pixels painted: {total_painted}")
    
    def bot_error(self, error: str):
        """Log bot error"""
        self.error(f"Bot error: {error}")

# Global logger instance
_logger_instance = None

def get_logger() -> PlaceBotLogger:
    """Get the global logger instance"""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = PlaceBotLogger()
    return _logger_instance