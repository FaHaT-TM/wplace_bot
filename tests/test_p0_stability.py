"""Unit tests for P0 stability: region/screen bounds, HiDPI scaling, empty palette, and dedup painting."""

from __future__ import annotations

import importlib
import queue
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from core.color_detection import detect_palette_colors, save_palette_debug_image
from core.data_manager import DataManager
from core.exceptions import RegionError
from core.pixel_mapping import find_pixels_to_paint_from_map
from core.screen_capture import (
    compute_region_scale,
    get_screen,
    image_offset_to_screen_point,
    validate_point_in_region,
    validate_region,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"


# ---------------------------------------------------------------------------
# 1. Region & coordinate bounds validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_region",
    [
        None,
        (),
        (10, 20, 30),
        (10, 20, 30, 40, 50),
        (-1, 10, 100, 100),
        (10, -5, 100, 100),
        (10, 10, 0, 100),
        (10, 10, 100, 5),  # smaller than MIN_REGION_DIMENSION (10)
        (True, 10, 100, 100),
        ("10", "10", "100", "100"),
    ],
)
def test_validate_region_rejects_malformed_or_tiny_regions(bad_region: object) -> None:
    """Malformed, None, negative, or <10px regions must raise RegionError."""
    with pytest.raises(RegionError):
        validate_region(bad_region)


def test_validate_region_detects_resolution_change_or_unplugged_monitor() -> None:
    """Saved region exceeding current screen dimensions must raise RegionError."""
    assert validate_region((100, 200, 400, 300), screen_size=(1920, 1080)) == (
        100,
        200,
        400,
        300,
    )
    # Exact edge fit is valid
    assert validate_region((1520, 780, 400, 300), screen_size=(1920, 1080)) == (
        1520,
        780,
        400,
        300,
    )
    # 1 pixel past right or bottom edge -> RegionError
    with pytest.raises(RegionError, match="screen"):
        validate_region((1521, 780, 400, 300), screen_size=(1920, 1080))
    with pytest.raises(RegionError, match="screen"):
        validate_region((100, 781, 400, 300), screen_size=(1920, 1080))


def test_get_screen_rejects_none_region() -> None:
    """get_screen(None) must fail fast with RegionError instead of capturing full screen."""
    with pytest.raises(RegionError):
        get_screen(None)


def test_validate_point_in_region_enforces_half_open_bounds() -> None:
    """Points on [left, left+width) x [top, top+height) pass; outside points raise RegionError."""
    region = (100, 200, 50, 40)
    assert validate_point_in_region(100, 200, region) == (100, 200)
    assert validate_point_in_region(149, 239, region) == (149, 239)

    for out_x, out_y in [(99, 200), (150, 200), (100, 199), (100, 240)]:
        with pytest.raises(RegionError):
            validate_point_in_region(out_x, out_y, region)


# ---------------------------------------------------------------------------
# 2. HiDPI / Retina coordinate scaling
# ---------------------------------------------------------------------------


def test_hidpi_2x_scaling_in_palette_detection_and_bot_worker(tmp_path: Path) -> None:
    """2x Retina screenshots must map centroids and canvas pixels back to 1x logical screen points."""
    from core.bot_worker import BotWorker

    # Logical palette region: left=100, top=800, width=200, height=50
    palette_region = (100, 800, 200, 50)
    # Physical 2x screenshot: height=100, width=400
    palette_img_rgb = np.zeros((100, 400, 3), dtype=np.uint8)
    # Draw Red [237, 28, 36] swatch from x=180..220, y=30..70 (physical center ~ (200, 50))
    palette_img_rgb[30:71, 180:221] = [237, 28, 36]

    scale = compute_region_scale(palette_img_rgb.shape, palette_region)
    assert scale == (2.0, 2.0)

    known_colors = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]
    color_map = detect_palette_colors(palette_img_rgb, palette_region, known_colors)

    assert (237, 28, 36) in color_map
    sx, sy = color_map[(237, 28, 36)]
    # Physical (200, 50) / 2.0 = logical offset (100, 25) -> screen (200, 825)
    assert (sx, sy) == (200, 825)
    # Verify debug image writer handles scaled coordinates cleanly
    debug_path = tmp_path / "debug_palette.png"
    save_palette_debug_image(
        palette_img_rgb, color_map, palette_region, filename=str(debug_path)
    )
    assert debug_path.exists()

    # Now test BotWorker with 2x canvas_scale:
    # Logical canvas_region=(100, 100, 300, 300), physical image 600x600 -> pixel at (400, 200)
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 300, 300))
    dm.set_palette_region(palette_region)
    dm.set_analysis_results(
        pixel_size=20,
        pixel_map={
            (400, 200): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        },
        color_position_map=color_map,
        canvas_scale=(2.0, 2.0),
    )

    clicks: list[tuple[int, int]] = []
    pos = types.SimpleNamespace(x=0, y=0)

    class StubMouse:
        def position(self) -> types.SimpleNamespace:
            return pos

        def size(self) -> tuple[int, int]:
            return (1920, 1080)

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))
            pos.x, pos.y = x, y

    worker = BotWorker(dm, mouse_controller=StubMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        msg_queue,
        known_colors,
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    # 1 palette click at (200, 825) + 1 scaled canvas click at (100 + 400/2, 100 + 200/2) = (300, 200)
    assert clicks == [(200, 825), (300, 200)]


def test_image_offset_to_screen_point_1x_and_fractional_dpi() -> None:
    """Verify 1.0x and 1.25x DPI coordinate conversion."""
    region = (50, 100, 400, 300)
    assert image_offset_to_screen_point(25, 40, region, (1.0, 1.0)) == (75, 140)
    assert image_offset_to_screen_point(125, 250, region, (1.25, 1.25)) == (150, 300)


# ---------------------------------------------------------------------------
# 3. Out-of-bounds click protection & screen resolution change in BotWorker
# ---------------------------------------------------------------------------


def test_bot_worker_skips_out_of_bounds_canvas_coordinates(tmp_path: Path) -> None:
    """BotWorker must never click coordinates that fall outside canvas_region."""
    from core.bot_worker import BotWorker

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 400, 200, 50))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (-10, 20): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},  # OOB left
            (250, 20): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},  # OOB right
            (50, 50): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},   # Valid!
        },
        color_position_map={(237, 28, 36): (120, 420)},
    )

    clicks: list[tuple[int, int]] = []
    pos = types.SimpleNamespace(x=0, y=0)

    class StubMouse:
        def position(self) -> types.SimpleNamespace:
            return pos

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))
            pos.x, pos.y = x, y

    worker = BotWorker(dm, mouse_controller=StubMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        msg_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    # Palette click (120, 420) + ONLY valid interior point (150, 150)
    assert clicks == [(120, 420), (150, 150)]


def test_bot_worker_aborts_if_region_exceeds_new_screen_resolution(
    tmp_path: Path,
) -> None:
    """If screen resolution shrank below saved region, BotWorker emits bot_error with 0 clicks."""
    from core.bot_worker import BotWorker

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    # Region was on a 2560x1440 monitor, now laptop screen is 1366x768
    dm.set_canvas_region((1800, 200, 500, 500))
    dm.set_palette_region((1800, 800, 400, 60))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={(10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}},
        color_position_map={(237, 28, 36): (1850, 820)},
    )

    clicks: list[tuple[int, int]] = []
    pos = types.SimpleNamespace(x=0, y=0)

    class SmallScreenMouse:
        def position(self) -> types.SimpleNamespace:
            return pos

        def size(self) -> tuple[int, int]:
            return (1366, 768)

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))

    worker = BotWorker(dm, mouse_controller=SmallScreenMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        msg_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    msg = msg_queue.get_nowait()
    assert msg["type"] == "bot_error"
    assert "screen" in msg["error"].lower()
    assert clicks == []


# ---------------------------------------------------------------------------
# 4. Empty palette & missing region protection in AnalysisWorker & BotWorker
# ---------------------------------------------------------------------------


def test_analysis_worker_fails_cleanly_on_empty_detected_palette(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AnalysisWorker must emit analysis_error when 0 palette swatches are detected."""
    monkeypatch.chdir(tmp_path)
    from core.analysis_worker import AnalysisWorker

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 400, 200, 50))

    class BlankScreenCapturer:
        def screen_size(self) -> tuple[int, int]:
            return (1920, 1080)

        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            # Uniform gray [50, 50, 50] matches zero colors in colors.json at tolerance=3
            return np.full((region[3], region[2], 3), 50, dtype=np.uint8)

    worker = AnalysisWorker(dm, screen_capturer=BlankScreenCapturer())
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_analysis(msg_queue)
    assert worker.thread is not None
    worker.thread.join(timeout=3.0)

    msg = msg_queue.get_nowait()
    assert msg["type"] == "analysis_error"
    assert "palette" in msg["error"].lower()


def test_bot_worker_fails_cleanly_on_empty_color_position_map(tmp_path: Path) -> None:
    """BotWorker must emit bot_error when color_position_map is empty."""
    from core.bot_worker import BotWorker

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 400, 200, 50))
    dm.set_analysis_results(pixel_size=10, pixel_map={}, color_position_map={})

    pos = types.SimpleNamespace(x=0, y=0)
    mouse = types.SimpleNamespace(position=lambda: pos, click=lambda x, y: None)

    worker = BotWorker(dm, mouse_controller=mouse, palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        msg_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    msg = msg_queue.get_nowait()
    assert msg["type"] == "bot_error"
    assert "palette" in msg["error"].lower()


# ---------------------------------------------------------------------------
# 5. Color tolerance boundary & no duplicate repainting across close colors
# ---------------------------------------------------------------------------


def test_color_tolerance_exact_boundary_and_no_duplicate_repaint(
    tmp_path: Path,
) -> None:
    """Boundary diff==tolerance matches; close colors must not repaint the same coordinate twice."""
    from core.bot_worker import BotWorker

    pixel_map = {
        (10, 10): {"preview_color": (105, 100, 100), "pixel_color": (0, 0, 0)},  # diff=5 -> match tol=5
        (20, 10): {"preview_color": (106, 100, 100), "pixel_color": (0, 0, 0)},  # diff=6 -> miss tol=5
    }
    assert find_pixels_to_paint_from_map(pixel_map, (100, 100, 100), tolerance=5) == [
        (10, 10)
    ]

    # Close palette colors at tolerance=10:
    # Stone RGB [148, 140, 107] (BGR 107, 140, 148)
    # Tan   RGB [156, 132, 107] (BGR 107, 132, 156)
    # Preview BGR (107, 136, 152) is within 4 of BOTH Stone and Tan!
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_color_setting(62, "bought", True)  # Stone
    dm.set_color_setting(57, "bought", True)  # Tan
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 400, 200, 50))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (30, 30): {"preview_color": (107, 136, 152), "pixel_color": (0, 0, 0)},
        },
        color_position_map={
            (148, 140, 107): (120, 420),
            (156, 132, 107): (160, 420),
        },
    )

    clicks: list[tuple[int, int]] = []
    pos = types.SimpleNamespace(x=0, y=0)

    class StubMouse:
        def position(self) -> types.SimpleNamespace:
            return pos

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))
            pos.x, pos.y = x, y

    stone = {"id": 62, "name": "Stone", "rgb": [148, 140, 107], "premium": True}
    tan = {"id": 57, "name": "Tan", "rgb": [156, 132, 107], "premium": True}

    worker = BotWorker(dm, mouse_controller=StubMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        msg_queue,
        [stone, tan],
        {"pixel_limit": 100, "tolerance": 10, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    # (130, 130) must be clicked ONLY ONCE (by Stone), never repainted by Tan!
    assert clicks == [(120, 420), (130, 130)]


# ---------------------------------------------------------------------------
# 6. RegionSelector uses global root coordinates (event.x_root, event.y_root)
# ---------------------------------------------------------------------------


def test_region_selector_uses_root_screen_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RegionSelector must return global x_root/y_root coordinates rather than widget-local x/y."""
    fake_tk = types.ModuleType("tkinter")
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.delitem(sys.modules, "gui.region_selector", raising=False)
    monkeypatch.delitem(sys.modules, "gui", raising=False)

    rs_mod = importlib.import_module("gui.region_selector")
    RegionSelector = rs_mod.RegionSelector

    selected: list[Any] = []
    selector = RegionSelector.__new__(RegionSelector)
    selector.callback = selected.append
    selector.start_x = None
    selector.start_y = None
    selector.start_root_x = None
    selector.start_root_y = None
    selector.rect_id = None
    selector.canvas = types.SimpleNamespace(
        delete=lambda _id: None,
        create_rectangle=lambda *a, **kw: 1,
        create_text=lambda *a, **kw: 2,
    )
    selector.overlay = types.SimpleNamespace(destroy=lambda: None)

    # Simulate multi-monitor / top-bar offset: widget (50, 40) corresponds to root (1970, 70)
    press_event = types.SimpleNamespace(x=50, y=40, x_root=1970, y_root=70)
    release_event = types.SimpleNamespace(x=250, y=190, x_root=2170, y_root=220)

    selector.on_click(press_event)
    selector.on_release(release_event)

    assert selected == [(1970, 70, 200, 150)]
