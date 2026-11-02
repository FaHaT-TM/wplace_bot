"""Unit tests for multi-monitor region selection, capture, validation, and painting."""

from __future__ import annotations

import importlib
import queue
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from core.color_detection import save_palette_debug_image
from core.data_manager import DataManager
from core.exceptions import RegionError
from core.screen_capture import (
    MonitorInfo,
    PyAutoGUIScreenCapturer,
    compute_virtual_screen_bounds,
    find_monitors_for_region,
    format_monitor_summary,
    get_screen,
    is_region_covered_by_monitors,
    parse_xrandr_monitors,
    validate_point_in_region,
    validate_region,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"


# ---------------------------------------------------------------------------
# 1. Monitor topology parsing & virtual desktop geometry
# ---------------------------------------------------------------------------


def test_parse_xrandr_monitors_parses_multi_screen_and_negative_offsets() -> None:
    """xrandr parser must extract connected monitors with positive and negative offsets."""
    sample_xrandr = """\
Screen 0: minimum 8 x 8, current 6400 x 1440, maximum 32767 x 32767
DP-1 connected primary 2560x1440+0+0 (normal left inverted right x axis y axis) 597mm x 336mm
   2560x1440     59.95*+
HDMI-1 connected 1920x1080+2560+180 (normal left inverted right x axis y axis) 510mm x 290mm
   1920x1080     60.00*+
eDP-1 connected 1920x1080-1920+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.00*+
DP-2 disconnected (normal left inverted right x axis y axis)
"""
    monitors = parse_xrandr_monitors(sample_xrandr)
    assert len(monitors) == 3

    primary = next(m for m in monitors if m.is_primary)
    assert (primary.left, primary.top, primary.width, primary.height) == (
        0,
        0,
        2560,
        1440,
    )
    assert primary.name == "DP-1"

    left_mon = next(m for m in monitors if m.name == "eDP-1")
    assert (left_mon.left, left_mon.top, left_mon.width, left_mon.height) == (
        -1920,
        0,
        1920,
        1080,
    )

    right_mon = next(m for m in monitors if m.name == "HDMI-1")
    assert (right_mon.left, right_mon.top, right_mon.width, right_mon.height) == (
        2560,
        180,
        1920,
        1080,
    )


def test_compute_virtual_screen_bounds_with_negative_and_positive_monitors() -> None:
    """Virtual screen bounds must span from minimum (left, top) to maximum (right, bottom)."""
    monitors = [
        MonitorInfo(index=1, left=0, top=0, width=2560, height=1440, is_primary=True),
        MonitorInfo(index=2, left=-1920, top=-200, width=1920, height=1080),
        MonitorInfo(index=3, left=2560, top=360, width=1920, height=1080),
    ]
    assert compute_virtual_screen_bounds(monitors) == (-1920, -200, 6400, 1640)


# ---------------------------------------------------------------------------
# 2. Multi-monitor region & point validation (including L-shaped dead zones)
# ---------------------------------------------------------------------------


def test_validate_region_accepts_secondary_monitors_and_rejects_dead_zones() -> None:
    """Regions on left/right/primary monitors pass; dead-void or unplugged regions fail."""
    monitors = [
        MonitorInfo(
            index=1, left=0, top=0, width=2560, height=1440, is_primary=True, name="DP-1"
        ),
        MonitorInfo(
            index=2, left=-1920, top=0, width=1920, height=1080, is_primary=False, name="eDP-1"
        ),
        MonitorInfo(
            index=3, left=2560, top=360, width=1920, height=1080, is_primary=False, name="HDMI-1"
        ),
    ]

    # Left monitor (negative X)
    left_region = (-1800, 100, 600, 500)
    assert validate_region(left_region, monitors=monitors) == left_region
    assert format_monitor_summary(left_region, monitors) == "Monitor 2"

    # Right monitor (X > primary width)
    right_region = (2700, 400, 800, 600)
    assert validate_region(right_region, monitors=monitors) == right_region
    assert format_monitor_summary(right_region, monitors) == "Monitor 3"

    # Contiguous span across Monitor 2 and Monitor 1 where both cover y=100..600
    spanning_region = (-200, 100, 400, 500)
    assert is_region_covered_by_monitors(spanning_region, monitors) is True
    assert validate_region(spanning_region, monitors=monitors) == spanning_region
    assert [m.index for m in find_monitors_for_region(spanning_region, monitors)] == [
        1,
        2,
    ]
    assert format_monitor_summary(spanning_region, monitors) == "Monitors 1, 2"

    # Dead zone above Monitor 3 (x=2560..2800, y=100..360 has NO physical display!)
    dead_zone_region = (2400, 100, 400, 400)
    assert is_region_covered_by_monitors(dead_zone_region, monitors) is False
    with pytest.raises(RegionError, match="screen|monitor"):
        validate_region(dead_zone_region, monitors=monitors)

    # Dead zone below Monitor 2 (x=-1800..-1200, y=900..1200 exceeds height 1080 of Monitor 2!)
    below_left_monitor = (-1800, 900, 600, 300)
    with pytest.raises(RegionError, match="screen|monitor"):
        validate_region(below_left_monitor, monitors=monitors)

    # Disconnected 4th monitor region
    unplugged_region = (5000, 100, 400, 400)
    with pytest.raises(RegionError, match="screen|monitor"):
        validate_region(unplugged_region, monitors=monitors)

    # Point validation on negative-coordinate left monitor
    assert validate_point_in_region(-1500, 250, left_region, monitors=monitors) == (
        -1500,
        250,
    )
    with pytest.raises(RegionError):
        validate_point_in_region(-1500, 1200, (-1800, 100, 600, 1200), monitors=monitors)


# ---------------------------------------------------------------------------
# 3. Multi-monitor screenshot capture via ImageGrab(all_screens=True)
# ---------------------------------------------------------------------------


def test_screenshot_capturer_uses_imagegrab_all_screens_and_converts_rgb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyAutoGUIScreenCapturer must pass bbox and all_screens=True to ImageGrab.grab."""
    captured_calls: list[dict[str, Any]] = []

    def fake_grab(
        bbox: tuple[int, int, int, int] | None = None,
        include_layered_windows: bool = False,
        all_screens: bool = False,
        **kwargs: Any,
    ) -> Image.Image:
        captured_calls.append(
            {"bbox": bbox, "all_screens": all_screens, "kwargs": kwargs}
        )
        # Return RGBA image to ensure converter normalizes to 3-channel RGB
        return Image.new("RGBA", (200, 150), (12, 34, 56, 255))

    monkeypatch.setattr("PIL.ImageGrab.grab", fake_grab)

    capturer = PyAutoGUIScreenCapturer()
    arr = capturer.screenshot((-1500, 100, 200, 150))

    assert len(captured_calls) == 1
    assert captured_calls[0]["bbox"] == (-1500, 100, -1300, 250)
    assert captured_calls[0]["all_screens"] is True
    assert arr.shape == (150, 200, 3)
    assert tuple(int(v) for v in arr[0, 0]) == (12, 34, 56)


def test_screenshot_capturer_falls_back_to_pyautogui_when_imagegrab_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ImageGrab.grab raises OSError/NotImplementedError, fallback to pyautogui.screenshot."""

    def failing_grab(*args: Any, **kwargs: Any) -> Image.Image:
        raise OSError("ImageGrab unsupported on this display server")

    monkeypatch.setattr("PIL.ImageGrab.grab", failing_grab)

    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.screenshot = lambda region=None: Image.new(  # type: ignore[attr-defined]
        "RGB", (region[2], region[3]), (90, 80, 70)
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    capturer = PyAutoGUIScreenCapturer()
    arr = capturer.screenshot((100, 200, 60, 40))
    assert arr.shape == (40, 60, 3)
    assert tuple(int(v) for v in arr[0, 0]) == (90, 80, 70)


def test_get_screen_validates_against_capturer_monitors() -> None:
    """get_screen must accept regions on secondary monitors exposed by capturer.monitors()."""
    monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(index=2, left=-1920, top=0, width=1920, height=1080),
    ]

    class MultiScreenCapturer:
        def monitors(self) -> list[MonitorInfo]:
            return monitors

        def screen_size(self) -> tuple[int, int]:
            return (1920, 1080)

        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            return np.zeros((region[3], region[2], 3), dtype=np.uint8)

    img = get_screen((-1600, 120, 300, 200), capturer=MultiScreenCapturer())
    assert img.shape == (200, 300, 3)

    with pytest.raises(RegionError):
        get_screen((-2500, 120, 300, 200), capturer=MultiScreenCapturer())


# ---------------------------------------------------------------------------
# 4. End-to-end cross-monitor AnalysisWorker + BotWorker (negative + right screens)
# ---------------------------------------------------------------------------


def test_cross_monitor_canvas_on_left_hidpi_and_palette_on_right_monitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canvas on left 2x monitor (-1920..0) and Palette on right 1x monitor (1920..3840) work end-to-end."""
    monkeypatch.chdir(tmp_path)
    from core.analysis_worker import AnalysisWorker
    from core.bot_worker import BotWorker

    monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(index=2, left=-1920, top=0, width=1920, height=1080),
        MonitorInfo(index=3, left=1920, top=0, width=1920, height=1080),
    ]

    canvas_region = (-1500, 100, 300, 200)   # On Monitor 2 (left, negative X)
    palette_region = (2100, 800, 200, 60)    # On Monitor 3 (right, X > 1920)

    # Synthetic 1x palette image with neutral [50, 50, 50] background and Red [237, 28, 36] swatch
    palette_img = np.full((60, 200, 3), 50, dtype=np.uint8)
    palette_img[15:46, 35:66] = [237, 28, 36]

    # Synthetic 2x HiDPI canvas image (600x400 for 300x200 logical region)
    canvas_img = np.zeros((400, 600, 3), dtype=np.uint8)

    class TripleMonitorCapturer:
        def monitors(self) -> list[MonitorInfo]:
            return monitors

        def screen_size(self) -> tuple[int, int]:
            return (1920, 1080)

        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            if region == palette_region:
                return palette_img.copy()
            if region == canvas_region:
                return canvas_img.copy()
            raise AssertionError(f"Unexpected region: {region}")

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region(canvas_region)
    dm.set_palette_region(palette_region)

    analysis_worker = AnalysisWorker(dm, screen_capturer=TripleMonitorCapturer())
    analysis_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    analysis_worker.start_analysis(analysis_queue)
    assert analysis_worker.thread is not None
    analysis_worker.thread.join(timeout=3.0)

    analysis_msg = analysis_queue.get_nowait()
    assert analysis_msg["type"] == "analysis_complete"
    assert analysis_msg["colors_found"] == 1
    assert dm.canvas_scale == (2.0, 2.0)
    assert dm.color_position_map is not None
    swatch_x, swatch_y = dm.color_position_map[(237, 28, 36)]
    assert (swatch_x, swatch_y) == (2150, 830)

    # Verify debug image can be saved with secondary monitor region
    debug_file = tmp_path / "debug_palette_right.png"
    save_palette_debug_image(
        palette_img, dm.color_position_map, palette_region, filename=str(debug_file)
    )
    assert debug_file.exists()

    # Populate a target unpainted Red pixel at physical offset (200, 120) -> logical (+100, +60) -> (-1400, 160)
    dm.pixel_map = {
        (200, 120): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
    }

    clicks: list[tuple[int, int]] = []
    cursor = types.SimpleNamespace(x=0, y=0)

    class MultiMonitorMouse:
        def position(self) -> types.SimpleNamespace:
            return cursor

        def monitors(self) -> list[MonitorInfo]:
            return monitors

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))
            cursor.x, cursor.y = x, y

    bot = BotWorker(dm, mouse_controller=MultiMonitorMouse(), palette_switch_delay=0.0)
    bot_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    bot.start_bot(
        bot_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert bot.thread is not None
    bot.thread.join(timeout=2.0)

    messages: list[dict[str, Any]] = []
    while not bot_queue.empty():
        messages.append(bot_queue.get_nowait())
    bot_msg = messages[-1]
    assert bot_msg["type"] == "bot_complete"
    assert bot_msg["total_painted"] == 1
    # Clicked swatch on right Monitor 3 (2150, 830), then pixel on left Monitor 2 (-1400, 160)!
    assert clicks == [(2150, 830), (-1400, 160)]


def test_bot_worker_aborts_when_secondary_monitor_is_disconnected(
    tmp_path: Path,
) -> None:
    """If Monitor 2 is unplugged before Start, BotWorker aborts with bot_error and 0 clicks."""
    from core.bot_worker import BotWorker

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((-1500, 100, 300, 200))
    dm.set_palette_region((100, 800, 200, 60))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={(20, 20): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}},
        color_position_map={(237, 28, 36): (150, 830)},
    )

    # Now only Primary Monitor 1 is connected; Monitor 2 (-1920..0) was unplugged!
    remaining_monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)
    ]
    clicks: list[tuple[int, int]] = []
    cursor = types.SimpleNamespace(x=0, y=0)

    class SingleMonitorMouse:
        def position(self) -> types.SimpleNamespace:
            return cursor

        def monitors(self) -> list[MonitorInfo]:
            return remaining_monitors

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))

    bot = BotWorker(dm, mouse_controller=SingleMonitorMouse(), palette_switch_delay=0.0)
    bot_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    bot.start_bot(
        bot_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert bot.thread is not None
    bot.thread.join(timeout=2.0)

    msg = bot_queue.get_nowait()
    assert msg["type"] == "bot_error"
    assert clicks == []


# ---------------------------------------------------------------------------
# 5. GUI RegionSelector & SetupTab multi-monitor behavior
# ---------------------------------------------------------------------------


def test_region_selector_spans_virtual_desktop_and_rejects_dead_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RegionSelector must span all monitors (including negative X) and block dead-zone selections."""
    fake_tk = types.ModuleType("tkinter")
    created_geometries: list[str] = []
    created_texts: list[tuple[Any, ...]] = []
    destroyed: list[bool] = []

    class FakeToplevel:
        def attributes(self, *args: Any) -> None:
            pass

        def overrideredirect(self, flag: bool) -> None:
            self.borderless = flag

        def geometry(self, spec: str) -> None:
            created_geometries.append(spec)

        def configure(self, **kwargs: Any) -> None:
            pass

        def bind(self, *args: Any) -> None:
            pass

        def lift(self) -> None:
            pass

        def focus_force(self) -> None:
            pass

        def focus_set(self) -> None:
            pass

        def winfo_screenwidth(self) -> int:
            return 1920

        def winfo_screenheight(self) -> int:
            return 1080

        def destroy(self) -> None:
            destroyed.append(True)

    class FakeCanvas:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def pack(self, **kwargs: Any) -> None:
            pass

        def bind(self, *args: Any) -> None:
            pass

        def focus_set(self) -> None:
            pass

        def create_text(self, *args: Any, **kwargs: Any) -> int:
            created_texts.append((args, kwargs))
            return len(created_texts)

        def create_rectangle(self, *args: Any, **kwargs: Any) -> int:
            return 1

        def delete(self, _item: Any) -> None:
            pass

    fake_tk.Toplevel = FakeToplevel  # type: ignore[attr-defined]
    fake_tk.Canvas = FakeCanvas  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.delitem(sys.modules, "gui.region_selector", raising=False)

    rs_mod = importlib.import_module("gui.region_selector")

    monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(index=2, left=-1920, top=200, width=1920, height=1080),
    ]

    results: list[Any] = []
    selector = rs_mod.RegionSelector(results.append, monitors=monitors)

    # Virtual desktop spans x=-1920..1920 (width 3840), y=0..1280 (height 1280) -> "3840x1280-1920+0"
    assert created_geometries == ["3840x1280-1920+0"]

    # 1) Dragging across dead zone (-500, 50) -> (-100, 300): y=50..200 on Monitor 2 does not exist!
    selector.on_click(types.SimpleNamespace(x=1420, y=50, x_root=-500, y_root=50))
    selector.on_release(types.SimpleNamespace(x=1820, y=300, x_root=-100, y_root=300))
    assert results == []
    assert destroyed == []

    # 2) Valid drag on left Monitor 2 (-1500, 300) -> (-1100, 600)
    selector.on_click(types.SimpleNamespace(x=420, y=300, x_root=-1500, y_root=300))
    selector.on_release(types.SimpleNamespace(x=820, y=600, x_root=-1100, y_root=600))
    assert results == [(-1500, 300, 400, 300)]
    assert destroyed == [True]


def test_setup_tab_preserves_secondary_monitor_regions_and_resets_unplugged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SetupTab._load_saved_regions must keep valid Monitor 2 regions and reset unplugged Monitor 3 regions."""
    fake_tk = types.ModuleType("tkinter")
    fake_ttk = types.ModuleType("tkinter.ttk")
    fake_tk.ttk = fake_ttk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
    monkeypatch.delitem(sys.modules, "gui.tabs.setup_tab", raising=False)

    setup_mod = importlib.import_module("gui.tabs.setup_tab")

    monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(index=2, left=1920, top=0, width=1920, height=1080),
    ]
    monkeypatch.setattr(setup_mod, "detect_monitors", lambda: monitors)

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.canvas_region = (2100, 150, 500, 400)   # Connected Monitor 2 -> keep!
    dm.palette_region = (-1500, 200, 300, 80)  # Unplugged left monitor -> reset!

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""
            self.fg = ""
            self.state = ""

        def config(self, **kwargs: Any) -> None:
            if "text" in kwargs:
                self.text = kwargs["text"]
            if "foreground" in kwargs:
                self.fg = kwargs["foreground"]
            if "state" in kwargs:
                self.state = kwargs["state"]

    tab = setup_mod.SetupTab.__new__(setup_mod.SetupTab)
    tab.data_manager = dm
    tab.main_window = types.SimpleNamespace(
        root=types.SimpleNamespace(
            winfo_screenwidth=lambda: 1920,
            winfo_screenheight=lambda: 1080,
        ),
        log_message=lambda _m: None,
    )
    tab.canvas_status = DummyLabel()
    tab.palette_status = DummyLabel()
    tab.monitor_status = DummyLabel()
    tab.analyze_btn = DummyLabel()

    tab._load_saved_regions()

    assert dm.canvas_region == (2100, 150, 500, 400)
    assert "Monitor 2" in tab.canvas_status.text
    assert dm.palette_region is None
    assert tab.palette_status.fg == "orange"
