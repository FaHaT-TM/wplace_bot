"""Unit tests for dedicated logs/ and debug/ directories, log rotation, and logic fixes."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from core.color_detection import (
    DEFAULT_PALETTE_DEBUG_IMAGE,
    save_palette_debug_image,
)
from core.config import (
    DEFAULT_DEBUG_DIR,
    DEFAULT_LOGS_DIR,
    resolve_writable_path,
)
from core.data_manager import DataManager
from core.image_analysis import DEFAULT_SIZE_DEBUG_IMAGE, estimate_pixel_size
from core.logger import PlaceBotLogger
from core.paint_planner import (
    DEFAULT_DRY_RUN_IMAGE,
    build_paint_plan,
    render_dry_run_diff_image,
)
from core.screen_capture import MonitorInfo
from core.site_counter import (
    DEFAULT_COUNTER_DEBUG_IMAGE,
    MON1_PAINT_COUNTER_RECT,
    read_site_pixel_counter,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"


def test_default_logs_and_debug_relative_paths() -> None:
    """Default log and debug image paths target dedicated logs/ and debug/ subdirectories."""
    assert DEFAULT_LOGS_DIR == "logs"
    assert DEFAULT_DEBUG_DIR == "debug"
    assert Path(DEFAULT_DRY_RUN_IMAGE).as_posix() == "debug/dry_run.png"
    assert Path(DEFAULT_PALETTE_DEBUG_IMAGE).as_posix() == "debug/palette.png"
    assert Path(DEFAULT_COUNTER_DEBUG_IMAGE).as_posix() == "debug/pixel_counter.png"
    assert Path(DEFAULT_SIZE_DEBUG_IMAGE).as_posix() == "debug/size_estimation.png"


def test_logger_writes_utf8_into_logs_subdir_rotates_old_files_and_logs_stop_accurately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PlaceBotLogger creates logs/placebot_YYYYMMDD.log in UTF-8, rotates old logs, and avoids root clutter."""
    app_dir = tmp_path / "Папка Бота"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app_dir / "app.exe"))

    # Pre-seed 9 historical daily logs so max_log_files=7 prunes the 3 oldest once today's log is added
    logs_dir = app_dir / "logs"
    logs_dir.mkdir(parents=True)
    for day in range(1, 10):
        old_log = logs_dir / f"placebot_2025010{day}.log"
        old_log.write_text(f"old log {day}\n", encoding="utf-8")

    messages: list[str] = []
    bot_logger = PlaceBotLogger(max_log_files=7)
    bot_logger.set_gui_callback(messages.append)

    try:
        assert bot_logger.log_file_path is not None
        log_path = Path(bot_logger.log_file_path)
        assert log_path.parent == logs_dir.resolve()
        assert log_path.name.startswith("placebot_") and log_path.suffix == ".log"
        # Root app directory has ZERO .log files
        assert list(app_dir.glob("*.log")) == []

        # Log Unicode text (em-dash, spinner, Cyrillic) and verify UTF-8 file write
        bot_logger.info("Dry-Run — проверка кириллицы ⟳ 148/500")

        # Verify stopped_by_user / cancelled_by_mouse do NOT claim "Painting completed successfully!"
        bot_logger.bot_complete(5, False, stopped_by_user=True)
        bot_logger.bot_complete(3, False, cancelled_by_mouse=True)
        bot_logger.bot_complete(10, False)
    finally:
        bot_logger.close()

    content = log_path.read_text(encoding="utf-8")
    assert "Dry-Run — проверка кириллицы ⟳ 148/500" in content
    assert "stopped by user" in content.lower()
    assert "canceled by mouse" in content.lower()
    assert "Painting completed successfully! Total pixels painted: 10" in content
    assert "Painting completed successfully! Total pixels painted: 5" not in content
    assert "Painting completed successfully! Total pixels painted: 3" not in content

    remaining_logs = sorted(p.name for p in logs_dir.glob("placebot_*.log"))
    assert len(remaining_logs) <= 7


def test_all_debug_images_saved_into_debug_subdir_without_root_clutter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All default debug PNG outputs are written into debug/ and loaded by PreviewTab."""
    app_dir = tmp_path / "Рабочая Папка"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app_dir / "app.exe"))

    # 1. Size estimation -> debug/size_estimation.png
    grid_bgr = np.full((90, 90, 3), 255, dtype=np.uint8)
    for idx in range(0, 90, 15):
        grid_bgr[idx, :, :] = 0
        grid_bgr[:, idx, :] = 0
    estimate_pixel_size(grid_bgr)

    # 2. Palette detection -> debug/palette.png
    palette_rgb = np.full((40, 80, 3), 240, dtype=np.uint8)
    save_palette_debug_image(
        palette_rgb,
        {(237, 28, 36): (115, 215)},
        (100, 200, 80, 40),
    )

    # 3. Dry-Run diff -> debug/dry_run.png
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(app_dir / "user_settings.json"),
        progress_file=str(app_dir / "session_progress.json"),
    )
    dm.set_canvas_region((100, 100, 90, 90))
    dm.set_palette_region((100, 200, 80, 40))
    dm.set_analysis_results(
        pixel_size=15,
        pixel_map={(15, 15): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}},
        color_position_map={(237, 28, 36): (115, 215)},
    )
    plan = build_paint_plan(
        data_manager=dm,
        enabled_colors=[{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        pixel_limit=10,
        tolerance=1,
        click_delay_ms=15,
    )
    assert dm.pixel_map is not None
    render_dry_run_diff_image(plan=plan, pixel_map=dm.pixel_map, pixel_size=15)

    # 4. Site pixel counter -> debug/pixel_counter.png
    from tests.test_site_counter_sync import _render_counter_crop

    class CounterCap:
        def monitors(self) -> list[MonitorInfo]:
            return [MonitorInfo(index=1, left=0, top=0, width=1680, height=1050, is_primary=True)]

        def screen_size(self) -> tuple[int, int]:
            return (1680, 1050)

        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            if abs(region[0] - MON1_PAINT_COUNTER_RECT[0]) <= 10:
                return _render_counter_crop("120/500", width=region[2], height=region[3])
            return np.full((region[3], region[2], 3), 40, dtype=np.uint8)

    reading = read_site_pixel_counter(
        data_manager=dm,
        phase="after_paint",
        capturer=CounterCap(),
        allow_winocr=False,
    )
    assert reading is not None and (reading.available, reading.maximum) == (120, 500)

    debug_dir = app_dir.resolve() / "debug"
    assert (debug_dir / "size_estimation.png").is_file()
    assert (debug_dir / "palette.png").is_file()
    assert (debug_dir / "dry_run.png").is_file()
    assert (debug_dir / "pixel_counter.png").is_file()
    # Zero .png files polluting root app_dir!
    assert list(app_dir.glob("*.png")) == []

    # Verify PreviewTab loads all 4 debug images from debug/ cleanly
    fake_tk = types.ModuleType("tkinter")
    fake_ttk = types.ModuleType("tkinter.ttk")
    fake_tk.TclError = RuntimeError  # type: ignore[attr-defined]
    fake_tk.ttk = fake_ttk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
    monkeypatch.delitem(sys.modules, "gui.tabs.preview_tab", raising=False)
    preview_mod = importlib.import_module("gui.tabs.preview_tab")

    displayed_sizes: list[tuple[int, int]] = []
    ptab = preview_mod.PreviewTab.__new__(preview_mod.PreviewTab)
    ptab._last_dry_run_image_rgb = None
    ptab._last_dry_run_image_path = None
    ptab.image_label = types.SimpleNamespace(config=lambda **_kw: None)
    ptab._display_pil_image = lambda pil_img: displayed_sizes.append(pil_img.size)  # type: ignore[method-assign]

    for label in (
        "Dry-Run Diff",
        "Site Pixel Counter",
        "Size Estimation",
        "Palette Detection",
    ):
        ptab.image_var = types.SimpleNamespace(get=lambda lbl=label: lbl)
        ptab._load_debug_image()

    assert len(displayed_sizes) == 4
    assert Path(resolve_writable_path(DEFAULT_DRY_RUN_IMAGE)) == debug_dir / "dry_run.png"
