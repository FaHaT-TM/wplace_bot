"""Background worker for canvas and palette analysis."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from .exceptions import PaletteDetectionError, RegionError
from .logger import get_logger
from .screen_capture import (
    ScreenCapturer,
    compute_region_scale,
    get_screen,
    validate_region,
)

module_logger = logging.getLogger(__name__)


class AnalysisWorker:
    """Handles canvas and palette analysis in a separate background thread."""

    def __init__(
        self,
        data_manager: Any,
        screen_capturer: ScreenCapturer | None = None,
    ) -> None:
        """Initialize the analysis worker.

        Args:
            data_manager: Shared application data manager holding regions and palette data.
            screen_capturer: Optional screen capture provider for testing or custom backends.
        """
        self.data_manager = data_manager
        self._screen_capturer = screen_capturer
        self.thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.is_running: bool = False
        self.logger = get_logger()

    def start_analysis(self, message_queue: queue.Queue[dict[str, Any]]) -> None:
        """Start analysis in a separate daemon thread if not already running."""
        if self.thread is not None and self.thread.is_alive():
            module_logger.warning(
                "Analysis thread is already running; ignoring duplicate start."
            )
            return

        self._stop_event.clear()
        self.is_running = True
        self.thread = threading.Thread(
            target=self._analyze_worker,
            args=(message_queue,),
            daemon=True,
        )
        self.thread.start()

    def stop_analysis(self) -> None:
        """Signal the running analysis worker to abort."""
        self.is_running = False
        self._stop_event.set()

    def _analyze_worker(self, message_queue: queue.Queue[dict[str, Any]]) -> None:
        """Execute screenshot capture and CV analysis in background thread."""
        try:
            if not self.data_manager.has_regions():
                raise RegionError(
                    "Canvas and palette regions must be selected before analysis."
                )

            if not getattr(self.data_manager, "color_palette", None):
                raise PaletteDetectionError(
                    "Color palette definitions are empty; check colors.json."
                )

            palette_region = validate_region(
                self.data_manager.palette_region, name="Palette region"
            )
            canvas_region = validate_region(
                self.data_manager.canvas_region, name="Canvas region"
            )

            import cv2

            from core import (
                build_pixel_map,
                detect_palette_colors,
                estimate_pixel_size,
                get_preview_positions_from_estimation,
                save_palette_debug_image,
            )

            palette_img_rgb = get_screen(
                palette_region, capturer=self._screen_capturer
            )
            if self._stop_event.is_set():
                return

            canvas_img_rgb = get_screen(
                canvas_region, capturer=self._screen_capturer
            )
            if self._stop_event.is_set():
                return

            canvas_scale = compute_region_scale(canvas_img_rgb.shape, canvas_region)
            canvas_img_bgr = cv2.cvtColor(canvas_img_rgb, cv2.COLOR_RGB2BGR)

            pixel_size = estimate_pixel_size(canvas_img_bgr)
            self.logger.debug(f"Estimated pixel size: {pixel_size}x{pixel_size}")
            if self._stop_event.is_set():
                return

            preview_positions = get_preview_positions_from_estimation(
                canvas_img_bgr, pixel_size
            )
            pixel_map = build_pixel_map(canvas_img_bgr, pixel_size, preview_positions)
            self.logger.debug(f"Built pixel map with {len(pixel_map)} pixels")
            if self._stop_event.is_set():
                return

            color_position_map = detect_palette_colors(
                palette_img_rgb,
                palette_region,
                self.data_manager.color_palette,
            )
            self.logger.debug(f"Detected {len(color_position_map)} colors in palette")
            if self._stop_event.is_set():
                return

            if not color_position_map:
                raise PaletteDetectionError(
                    "No colors were detected in the selected palette region. "
                    "Please make sure the Wplace color palette is visible and reselect the palette region."
                )

            save_palette_debug_image(
                palette_img_rgb, color_position_map, palette_region
            )

            self.data_manager.set_analysis_results(
                pixel_size,
                pixel_map,
                color_position_map,
                canvas_scale=canvas_scale,
            )

            message_queue.put(
                {
                    "type": "analysis_complete",
                    "pixel_size": pixel_size,
                    "pixel_count": len(pixel_map),
                    "colors_found": len(color_position_map),
                }
            )

        except Exception as exc:
            module_logger.exception("Error during canvas/palette analysis")
            message_queue.put({"type": "analysis_error", "error": str(exc)})
        finally:
            self.is_running = False
