"""Custom exception hierarchy for Place Bot."""


class PlaceBotError(Exception):
    """Base exception for all Place Bot domain errors."""


class ConfigError(PlaceBotError):
    """Raised when configuration or user setting values are invalid."""


class RegionError(PlaceBotError):
    """Raised when canvas or palette screen regions are missing or invalid."""


class CaptureError(PlaceBotError):
    """Raised when screen capture fails."""


class PaletteDetectionError(PlaceBotError):
    """Raised when palette color detection fails."""


class PaintError(PlaceBotError):
    """Raised when automated pixel painting encounters an error."""
