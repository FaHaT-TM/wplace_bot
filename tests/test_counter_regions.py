"""Tests for manual pixel counter region selection (before/after painting) and persistence."""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from core.data_manager import DataManager
from core.screen_capture import MonitorInfo
from core.site_counter import (
    MON1_IDLE_COUNTER_RECT,
    MON1_PAINT_COUNTER_RECT,
    MON2_IDLE_COUNTER_RECT,
    MON2_PAINT_COUNTER_RECT,
    _build_candidate_regions,
    read_site_pixel_counter,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"


def _render_counter_crop(
    text: str,
    width: int = 80,
    height: int = 15,
    fg: tuple[int, int, int] = (255, 255, 255),
    bg: tuple[int, int, int] = (30, 90, 210),
) -> np.ndarray:
    """Render a realistic small UI counter screenshot crop at target pixel dimensions."""
    scale = 2
    canvas = Image.new("RGB", (width * scale, height * scale), color=bg)
    draw = ImageDraw.Draw(canvas)
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont
    target_px = max(16, (height - 3) * scale)
    try:
        font = ImageFont.load_default(size=target_px)
    except TypeError:
        font = ImageFont.load_default()
    for candidate_font in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate_font, size=target_px)
            break
        except OSError:
            continue
    text_w, text_h = draw.textbbox((0, 0), text, font=font)[2:]
    draw.text(
        ((width * scale - text_w) // 2, (height * scale - text_h) // 2),
        text,
        fill=fg,
        font=font,
    )
    return np.array(canvas.resize((width, height), Image.LANCZOS), dtype=np.uint8)


def _make_data_manager(tmp_path: Path) -> DataManager:
    """Create a DataManager backed by a temporary settings file."""
    return DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "user_settings.json"),
    )


def _install_fake_tkinter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register headless tkinter modules in sys.modules for GUI unit testing."""
    fake_tk = types.ModuleType("tkinter")
    fake_ttk = types.ModuleType("tkinter.ttk")
    fake_msgbox = types.ModuleType("tkinter.messagebox")
    fake_tk.END = "end"  # type: ignore[attr-defined]
    fake_tk.TclError = RuntimeError  # type: ignore[attr-defined]
    fake_tk.ttk = fake_ttk  # type: ignore[attr-defined]
    fake_tk.messagebox = fake_msgbox  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", fake_msgbox)
    for mod_name in [
        "gui.tabs.setup_tab",
        "gui.tabs",
        "gui.region_selector",
        "gui",
    ]:
        monkeypatch.delitem(sys.modules, mod_name, raising=False)


# ---------------------------------------------------------------------------
# 1. DataManager persistence of manual counter regions
# ---------------------------------------------------------------------------


def test_data_manager_counter_regions_default_to_none(tmp_path: Path) -> None:
    """Fresh DataManager has no manual counter regions (presets are used)."""
    dm = _make_data_manager(tmp_path)
    assert dm.counter_idle_region is None
    assert dm.counter_paint_region is None


def test_data_manager_set_counter_regions_persists_to_settings(tmp_path: Path) -> None:
    """Setting counter regions persists both regions in user_settings.json."""
    dm = _make_data_manager(tmp_path)
    dm.set_counter_idle_region((845, 1000, 80, 15))
    dm.set_counter_paint_region((1405, 985, 70, 15))

    assert dm.counter_idle_region == (845, 1000, 80, 15)
    assert dm.counter_paint_region == (1405, 985, 70, 15)

    raw = json.loads((tmp_path / "user_settings.json").read_text(encoding="utf-8"))
    prefs = raw["preferences"]
    assert prefs["last_counter_idle_region"] == [845, 1000, 80, 15]
    assert prefs["last_counter_paint_region"] == [1405, 985, 70, 15]

    reloaded = _make_data_manager(tmp_path)
    assert tuple(reloaded.counter_idle_region) == (845, 1000, 80, 15)
    assert tuple(reloaded.counter_paint_region) == (1405, 985, 70, 15)


def test_data_manager_counter_regions_saved_by_save_user_settings(
    tmp_path: Path,
) -> None:
    """save_user_settings() writes counter regions, covering program exit."""
    dm = _make_data_manager(tmp_path)
    dm.counter_idle_region = (2650, 1000, 75, 10)
    dm.counter_paint_region = (3205, 980, 70, 10)
    dm.save_user_settings()

    raw = json.loads((tmp_path / "user_settings.json").read_text(encoding="utf-8"))
    prefs = raw["preferences"]
    assert prefs["last_counter_idle_region"] == [2650, 1000, 75, 10]
    assert prefs["last_counter_paint_region"] == [3205, 980, 70, 10]


def test_data_manager_rejects_too_small_counter_region(tmp_path: Path) -> None:
    """Counter regions below the minimum counter dimension are rejected."""
    dm = _make_data_manager(tmp_path)
    dm.set_counter_idle_region((100, 100, 2, 2))
    assert dm.counter_idle_region is None

    dm.set_counter_paint_region((100, 100, 6, 6))
    assert dm.counter_paint_region == (100, 100, 6, 6)


# ---------------------------------------------------------------------------
# 2. Phase-aware candidate regions with manual override
# ---------------------------------------------------------------------------


def test_build_candidate_regions_uses_custom_idle_region_for_startup_and_manual() -> None:
    """A manual before-painting region replaces the idle presets entirely."""
    custom = (900, 1000, 70, 15)
    for phase in ("startup", "manual"):
        regions = _build_candidate_regions(
            phase, preferred_monitor=None, custom_idle_region=custom
        )
        assert regions == [
            ("Custom counter region (before painting)", custom)
        ]


def test_build_candidate_regions_uses_custom_paint_region_for_paint_phases() -> None:
    """A manual after-painting region replaces the paint presets entirely."""
    custom = (1500, 985, 70, 15)
    for phase in ("before_paint", "stopped", "after_paint"):
        regions = _build_candidate_regions(
            phase, preferred_monitor=None, custom_paint_region=custom
        )
        assert regions == [
            ("Custom counter region (after painting)", custom)
        ]


def test_build_candidate_regions_custom_region_does_not_leak_between_modes() -> None:
    """The idle custom region must not affect paint phases (and vice versa)."""
    custom_idle = (900, 1000, 70, 15)
    custom_paint = (1500, 985, 70, 15)

    idle_regions = _build_candidate_regions(
        "startup",
        preferred_monitor=None,
        custom_idle_region=custom_idle,
        custom_paint_region=custom_paint,
    )
    assert idle_regions[0][1] == custom_idle

    paint_regions = _build_candidate_regions(
        "before_paint",
        preferred_monitor=None,
        custom_idle_region=custom_idle,
        custom_paint_region=custom_paint,
    )
    assert paint_regions[0][1] == custom_paint


def test_read_site_pixel_counter_uses_manual_regions_from_data_manager(
    tmp_path: Path,
) -> None:
    """read_site_pixel_counter queries the manual region and never the presets."""
    dual_monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1680, height=1050, is_primary=True),
        MonitorInfo(index=2, left=1680, top=0, width=1920, height=1080, is_primary=False),
    ]
    dm = _make_data_manager(tmp_path)
    dm.set_counter_idle_region((900, 1000, 70, 15))
    dm.set_counter_paint_region((1500, 985, 70, 15))

    queried_regions: list[tuple[int, int, int, int]] = []

    class FakeCounterCapturer:
        def monitors(self) -> list[MonitorInfo]:
            return dual_monitors

        def screen_size(self) -> tuple[int, int]:
            return (3600, 1080)

        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            queried_regions.append(region)
            left, _top, w, h = region
            if abs(left - 900) <= 10:
                return _render_counter_crop("320/800", width=w, height=h)
            return np.full((h, w, 3), 40, dtype=np.uint8)

    # Startup phase -> manual idle region only.
    reading = read_site_pixel_counter(
        data_manager=dm,
        phase="startup",
        capturer=FakeCounterCapturer(),
        debug_image_path=None,
        allow_winocr=False,
    )
    assert reading is not None
    assert (reading.available, reading.maximum) == (320, 800)
    assert reading.region[0] == 900
    assert reading.source_label == "Custom counter region (before painting)"

    queried_lefts = {region[0] for region in queried_regions}
    assert queried_lefts <= {900, 894}
    preset_lefts = {
        MON1_IDLE_COUNTER_RECT[0],
        MON1_IDLE_COUNTER_RECT[0] - 6,
        MON2_IDLE_COUNTER_RECT[0],
        MON2_IDLE_COUNTER_RECT[0] - 6,
        MON1_PAINT_COUNTER_RECT[0],
        MON2_PAINT_COUNTER_RECT[0],
    }
    assert queried_lefts.isdisjoint(preset_lefts)

    # Paint phase -> manual paint region only.
    queried_regions.clear()

    class FakePaintCapturer(FakeCounterCapturer):
        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            queried_regions.append(region)
            left, _top, w, h = region
            if abs(left - 1500) <= 10:
                return _render_counter_crop("210/500", width=w, height=h)
            return np.full((h, w, 3), 40, dtype=np.uint8)

    reading_paint = read_site_pixel_counter(
        data_manager=dm,
        phase="before_paint",
        capturer=FakePaintCapturer(),
        debug_image_path=None,
        allow_winocr=False,
    )
    assert reading_paint is not None
    assert reading_paint.region[0] == 1500
    assert reading_paint.source_label == "Custom counter region (after painting)"
    queried_lefts = {region[0] for region in queried_regions}
    assert queried_lefts <= {1500, 1494}


# ---------------------------------------------------------------------------
# 3. SetupTab UI handlers
# ---------------------------------------------------------------------------


class _DummyStatus:
    """Minimal status label stub."""

    def __init__(self) -> None:
        self.text = "Not selected"
        self.fg = "red"

    def config(self, **kw: Any) -> None:
        if "text" in kw:
            self.text = kw["text"]
        if "foreground" in kw:
            self.fg = kw["foreground"]


def _make_setup_tab(tmp_path: Path) -> tuple[Any, DataManager, list[str]]:
    """Build a SetupTab instance with stubbed window and monitor query."""
    setup_mod = importlib.import_module("gui.tabs.setup_tab")
    dm = _make_data_manager(tmp_path)
    tab = setup_mod.SetupTab.__new__(setup_mod.SetupTab)
    tab.data_manager = dm
    tab.counter_idle_status = _DummyStatus()
    tab.counter_paint_status = _DummyStatus()
    tab.canvas_status = _DummyStatus()
    tab.palette_status = _DummyStatus()
    tab.analyze_btn = types.SimpleNamespace(config=lambda **_kw: None)
    log_messages: list[str] = []

    def _query_monitors() -> list[MonitorInfo]:
        return []

    def _check_ready_for_analysis() -> None:
        return None

    tab._query_monitors = _query_monitors  # type: ignore[method-assign]
    tab._check_ready_for_analysis = _check_ready_for_analysis  # type: ignore[method-assign]
    tab.main_window = types.SimpleNamespace(
        root=types.SimpleNamespace(
            withdraw=lambda: None,
            deiconify=lambda: None,
            winfo_screenwidth=lambda: 1680,
            winfo_screenheight=lambda: 1050,
        ),
        log_message=log_messages.append,
        control_tab=None,
    )
    return tab, dm, log_messages


def test_setup_tab_selects_and_persists_counter_idle_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting the before-painting counter region saves it immediately."""
    _install_fake_tkinter(monkeypatch)
    tab, dm, log_messages = _make_setup_tab(tmp_path)

    tab._on_counter_idle_region_selected((845, 1000, 80, 15))

    assert dm.counter_idle_region == (845, 1000, 80, 15)
    assert "Selected" in tab.counter_idle_status.text
    assert tab.counter_idle_status.fg == "green"
    assert any("before painting" in msg for msg in log_messages)


def test_setup_tab_selects_and_persists_counter_paint_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting the after-painting counter region saves it immediately."""
    _install_fake_tkinter(monkeypatch)
    tab, dm, log_messages = _make_setup_tab(tmp_path)

    tab._on_counter_paint_region_selected((1405, 985, 70, 15))

    assert dm.counter_paint_region == (1405, 985, 70, 15)
    assert "Selected" in tab.counter_paint_status.text
    assert tab.counter_paint_status.fg == "green"
    assert any("after painting" in msg for msg in log_messages)


def test_setup_tab_canceled_counter_selection_keeps_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canceled selection (None) does not store a region."""
    _install_fake_tkinter(monkeypatch)
    tab, dm, log_messages = _make_setup_tab(tmp_path)

    tab._on_counter_idle_region_selected(None)
    tab._on_counter_paint_region_selected(None)

    assert dm.counter_idle_region is None
    assert dm.counter_paint_region is None
    assert tab.counter_idle_status.text == "Not selected"
    assert any("canceled" in msg for msg in log_messages)


def test_setup_tab_load_saved_regions_restores_counter_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_load_saved_regions validates and displays previously saved counter regions."""
    _install_fake_tkinter(monkeypatch)
    tab, dm, _logs = _make_setup_tab(tmp_path)
    dm.set_counter_idle_region((845, 1000, 80, 15))
    dm.set_counter_paint_region((1405, 985, 70, 15))

    tab._load_saved_regions()

    assert "Loaded" in tab.counter_idle_status.text
    assert "Loaded" in tab.counter_paint_status.text
    assert dm.counter_idle_region == (845, 1000, 80, 15)
    assert dm.counter_paint_region == (1405, 985, 70, 15)


def test_setup_tab_load_saved_regions_drops_invalid_counter_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counter regions that fail validation are cleared on load."""
    _install_fake_tkinter(monkeypatch)
    tab, dm, _logs = _make_setup_tab(tmp_path)
    dm.counter_idle_region = (5000, 5000, 70, 15)
    dm.counter_paint_region = (6000, 6000, 70, 15)

    tab._load_saved_regions()

    assert dm.counter_idle_region is None
    assert dm.counter_paint_region is None
    assert "disconnected" in tab.counter_idle_status.text
    assert "disconnected" in tab.counter_paint_status.text
