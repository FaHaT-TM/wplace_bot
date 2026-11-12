"""Unit and integration tests for automatic Pixel Limit synchronization with wplace.live counter."""

from __future__ import annotations

import importlib
import queue
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from core.bot_worker import BotWorker
from core.data_manager import DataManager
from core.screen_capture import MonitorInfo
from core.site_counter import (
    MON1_IDLE_COUNTER_RECT,
    MON1_PAINT_COUNTER_RECT,
    MON2_IDLE_COUNTER_RECT,
    MON2_PAINT_COUNTER_RECT,
    SiteCounterReading,
    parse_counter_text,
    read_site_pixel_counter,
    recognize_counter_image,
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
    # Render crisp UI text at 2x and downsample with Lanczos to simulate browser anti-aliasing
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
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        if Path(candidate_font).exists():
            font = ImageFont.truetype(candidate_font, size=target_px)
            break

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = max(2, (width * scale - tw) // 2 - bbox[0])
    ty = max(0, (height * scale - th) // 2 - bbox[1])
    draw.text((tx, ty), text, fill=fg, font=font)
    small = canvas.resize((width, height), Image.Resampling.LANCZOS)
    return np.array(small, dtype=np.uint8)


# ---------------------------------------------------------------------------
# 1. Preset coordinate geometry & text parser
# ---------------------------------------------------------------------------


def test_preset_counter_rectangles_match_dual_monitor_coordinates() -> None:
    """Preset rectangles match Monitor 1 (1680x1050) and Monitor 2 (1920x1080) coordinates."""
    # Monitor 1 idle: (845, 1000) -> (925, 1015)
    assert MON1_IDLE_COUNTER_RECT == (845, 1000, 80, 15)
    # Monitor 1 painting: (1405, 985) -> (1475, 1000)
    assert MON1_PAINT_COUNTER_RECT == (1405, 985, 70, 15)
    # Monitor 2 idle: (2650, 1000) -> (2725, 1010)
    assert MON2_IDLE_COUNTER_RECT == (2650, 1000, 75, 10)
    # Monitor 2 painting: (3205, 980) -> (3275, 990)
    assert MON2_PAINT_COUNTER_RECT == (3205, 980, 70, 10)


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("148/500", (148, 500)),
        ("42 / 1000", (42, 1000)),
        ("0/62", (0, 62)),
        ("Paint 395 / 500", (395, 500)),
        ("(1200/5000)", (1200, 5000)),
        ("", None),
        ("Ready", None),
        ("123/", None),
    ],
)
def test_parse_counter_text_variants(
    raw_text: str, expected: tuple[int, int] | None
) -> None:
    """parse_counter_text extracts (available, maximum) from 'X / Y' counter strings."""
    assert parse_counter_text(raw_text) == expected


# ---------------------------------------------------------------------------
# 2. Pure CV digit + slash recognition on tiny 80x15 and 70x10 crops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counter_str", "w", "h", "fg", "bg", "expected_pair"),
    [
        ("148/500", 80, 15, (255, 255, 255), (25, 95, 215), (148, 500)),
        ("20/62", 70, 15, (25, 25, 30), (242, 244, 248), (20, 62)),
        ("395/960", 80, 15, (255, 255, 255), (30, 30, 35), (395, 960)),
        ("0/500", 75, 12, (255, 255, 255), (40, 100, 220), (0, 500)),
        ("74/120", 70, 11, (20, 20, 20), (250, 250, 250), (74, 120)),
    ],
)
def test_recognize_counter_image_reads_tiny_crops_across_themes(
    counter_str: str,
    w: int,
    h: int,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    expected_pair: tuple[int, int],
) -> None:
    """recognize_counter_image accurately reads light-on-dark and dark-on-light crops."""
    crop_rgb = _render_counter_crop(counter_str, width=w, height=h, fg=fg, bg=bg)
    recognized = recognize_counter_image(crop_rgb, allow_winocr=False)
    parsed = parse_counter_text(recognized)
    assert parsed == expected_pair, (
        f"Expected {expected_pair} for {counter_str!r}, got recognized={recognized!r}"
    )


# ---------------------------------------------------------------------------
# 3. Multi-monitor & phase-aware counter scanning
# ---------------------------------------------------------------------------


def test_read_site_pixel_counter_selects_monitor_2_and_falls_back_between_modes(
    tmp_path: Path,
) -> None:
    """When Canvas is on Monitor 2 (1680..3600), Monitor 2 counter rects are prioritized."""
    dual_monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1680, height=1050, is_primary=True),
        MonitorInfo(index=2, left=1680, top=0, width=1920, height=1080, is_primary=False),
    ]

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    # Place Canvas on Monitor 2
    dm.set_canvas_region((2100, 200, 400, 400))
    dm.set_palette_region((2100, 750, 400, 80))

    queried_regions: list[tuple[int, int, int, int]] = []

    class FakeCounterCapturer:
        def monitors(self) -> list[MonitorInfo]:
            return dual_monitors

        def screen_size(self) -> tuple[int, int]:
            return (3600, 1080)

        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            queried_regions.append(region)
            left, _top, w, h = region
            # Suppose Painting Mode box is currently blank blue, while Idle Mode on Mon 2 shows "245/500"
            if abs(left - MON2_IDLE_COUNTER_RECT[0]) <= 10:
                return _render_counter_crop("245/500", width=w, height=max(12, h))
            return np.full((h, w, 3), 40, dtype=np.uint8)

    debug_out = tmp_path / "counter_debug.png"
    reading = read_site_pixel_counter(
        data_manager=dm,
        phase="before_paint",
        capturer=FakeCounterCapturer(),
        debug_image_path=str(debug_out),
        allow_winocr=False,
    )

    assert reading is not None
    assert reading.available == 245
    assert reading.maximum == 500
    assert "Monitor 2" in reading.source_label
    # First queried region in 'before_paint' must be Monitor 2 painting rect!
    assert queried_regions[0][0] >= 1680
    assert debug_out.exists()

    # Now unplug Monitor 2 -> single monitor (1680x1050); Monitor 2 rects must NOT be queried!
    single_monitor = [
        MonitorInfo(index=1, left=0, top=0, width=1680, height=1050, is_primary=True)
    ]
    queried_single: list[tuple[int, int, int, int]] = []

    class SingleMonitorCapturer:
        def monitors(self) -> list[MonitorInfo]:
            return single_monitor

        def screen_size(self) -> tuple[int, int]:
            return (1680, 1050)

        def screenshot(self, region: tuple[int, int, int, int]) -> np.ndarray:
            queried_single.append(region)
            left, _top, w, h = region
            if abs(left - MON1_PAINT_COUNTER_RECT[0]) <= 10:
                return _render_counter_crop("88/300", width=w, height=h)
            return np.full((h, w, 3), 40, dtype=np.uint8)

    reading_single = read_site_pixel_counter(
        data_manager=dm,
        phase="after_paint",
        capturer=SingleMonitorCapturer(),
        debug_image_path=None,
        allow_winocr=False,
    )
    assert reading_single is not None
    assert (reading_single.available, reading_single.maximum) == (88, 300)
    assert all(r[0] + r[2] <= 1680 for r in queried_single)


# ---------------------------------------------------------------------------
# 4. End-to-end BotWorker & ControlTab lifecycle sync (startup, before, stop, after)
# ---------------------------------------------------------------------------


def test_bot_worker_syncs_limit_before_and_after_painting_and_respects_manual_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BotWorker syncs pixel_limit with site counter before & after painting, and ControlTab updates UI."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 300, 300))
    dm.set_palette_region((100, 500, 300, 60))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (10 * i, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}
            for i in range(1, 7)  # 6 unpainted Red pixels
        },
        color_position_map={(237, 28, 36): (120, 520)},
    )

    # Site starts with 3/500 available pixels, and drops by 1 per painted pixel!
    site_state = {"available": 3, "maximum": 500}
    sync_phases_seen: list[str] = []

    def fake_counter_provider(phase: str) -> SiteCounterReading | None:
        sync_phases_seen.append(phase)
        return SiteCounterReading(
            available=site_state["available"],
            maximum=site_state["maximum"],
            raw_text=f"{site_state['available']}/{site_state['maximum']}",
            source_label="Monitor 1 (painting)",
            region=MON1_PAINT_COUNTER_RECT,
        )

    canvas_clicks: list[tuple[int, int]] = []

    class CountingMouse:
        def __init__(self) -> None:
            self.pos = types.SimpleNamespace(x=0, y=0)

        def position(self) -> Any:
            return self.pos

        def moveTo(self, x: int, y: int) -> None:
            self.pos = types.SimpleNamespace(x=x, y=y)

        def click(self, x: int, y: int) -> None:
            self.pos = types.SimpleNamespace(x=x, y=y)
            if (x, y) != (120, 520):
                canvas_clicks.append((x, y))
                site_state["available"] = max(0, site_state["available"] - 1)

    worker = BotWorker(
        dm,
        mouse_controller=CountingMouse(),
        palette_switch_delay=0.0,
        counter_provider=fake_counter_provider,
    )
    q1: queue.Queue[dict[str, Any]] = queue.Queue()

    # Even though settings["pixel_limit"] was 1000, site has 3/500 and manual_override=False -> paints 3!
    worker.start_bot(
        q1,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {
            "pixel_limit": 1000,
            "tolerance": 1,
            "delay": 0,
            "manual_limit_override": False,
        },
    )
    assert worker.thread is not None
    worker.thread.join(timeout=3.0)

    assert len(canvas_clicks) == 3
    assert sync_phases_seen == ["before_paint", "after_paint"]

    msgs1: list[dict[str, Any]] = []
    while not q1.empty():
        msgs1.append(q1.get_nowait())
    sync_msgs = [m for m in msgs1 if m["type"] == "site_limit_synced"]
    assert len(sync_msgs) == 2
    assert sync_msgs[0]["reading"].available == 3
    assert sync_msgs[1]["reading"].available == 0

    # Now recharge site to 10/500, but user manually sets limit=2 (manual_limit_override=True) -> paints 2!
    site_state["available"] = 10
    q2: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        q2,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {
            "pixel_limit": 2,
            "tolerance": 1,
            "delay": 0,
            "manual_limit_override": True,
        },
    )
    assert worker.thread is not None
    worker.thread.join(timeout=3.0)
    # 3 painted in run 1 + 2 painted in run 2 = 5 total canvas clicks
    assert len(canvas_clicks) == 5
    assert site_state["available"] == 8

    # Verify ControlTab UI applies SiteCounterReading cleanly (updates limit var, scale max, and badge)
    fake_tk = types.ModuleType("tkinter")
    fake_ttk = types.ModuleType("tkinter.ttk")
    fake_msgbox = types.ModuleType("tkinter.messagebox")
    fake_tk.END = "end"  # type: ignore[attr-defined]
    fake_tk.TclError = RuntimeError  # type: ignore[attr-defined]
    fake_tk.ttk = fake_ttk  # type: ignore[attr-defined]
    fake_tk.messagebox = fake_msgbox  # type: ignore[attr-defined]
    fake_ttk.Style = lambda: types.SimpleNamespace(configure=lambda *a, **kw: None)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", fake_msgbox)
    monkeypatch.delitem(sys.modules, "gui.tabs.control_tab", raising=False)
    control_mod = importlib.import_module("gui.tabs.control_tab")

    class DummyVar:
        def __init__(self, value: Any) -> None:
            self._v = value

        def get(self) -> Any:
            return self._v

        def set(self, value: Any) -> None:
            self._v = value

    class DummyScale:
        def __init__(self) -> None:
            self.to = 5000
            self.val = 1000

        def config(self, **kw: Any) -> None:
            if "to" in kw:
                self.to = kw["to"]

        def set(self, val: Any) -> None:
            self.val = int(val)

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""
            self.fg = ""

        def config(self, **kw: Any) -> None:
            if "text" in kw:
                self.text = kw["text"]
            if "foreground" in kw:
                self.fg = kw["foreground"]
            if "style" in kw:
                pass

    tab = control_mod.ControlTab.__new__(control_mod.ControlTab)
    tab.data_manager = dm
    tab.max_pixel_limit = 5000
    tab._syncing_pixel_limit = False
    tab._last_valid_pixel_limit = 1000
    tab._manual_limit_override = True
    tab._save_timer = None
    tab.pixel_limit_var = DummyVar(1000)
    tab.pixel_limit_entry = DummyLabel()
    tab.pixel_limit_scale = DummyScale()
    tab.site_sync_label = DummyLabel()
    tab.main_window = types.SimpleNamespace(
        is_running=False,
        save_user_settings=lambda: None,
        root=types.SimpleNamespace(after_cancel=lambda _t: None, after=lambda *_a: None),
    )

    reading_after = SiteCounterReading(
        available=8,
        maximum=500,
        raw_text="8/500",
        source_label="Monitor 1 (painting)",
        region=MON1_PAINT_COUNTER_RECT,
    )
    tab.apply_site_counter_reading(reading_after, phase="after_paint")
    assert tab.pixel_limit_var.get() == 8
    assert tab.pixel_limit_scale.to == 500
    assert tab.pixel_limit_scale.val == 8
    assert tab._manual_limit_override is False
    assert "8 / 500" in tab.site_sync_label.text


def test_bot_worker_emits_stopped_sync_phase_when_halted_by_user(
    tmp_path: Path,
) -> None:
    """Stopping an active painting run triggers a 'stopped' site counter sync."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 300, 300))
    dm.set_palette_region((100, 500, 300, 60))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (10 * i, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}
            for i in range(1, 12)
        },
        color_position_map={(237, 28, 36): (120, 520)},
    )

    phases: list[str] = []
    worker_ref: dict[str, BotWorker] = {}

    def stop_counter_provider(phase: str) -> SiteCounterReading | None:
        phases.append(phase)
        return SiteCounterReading(
            available=50 if phase == "before_paint" else 49,
            maximum=500,
            raw_text="49/500",
            source_label="Monitor 1 (painting mode)",
            region=MON1_PAINT_COUNTER_RECT,
        )

    class StopAfterFirstPixelMouse:
        def __init__(self) -> None:
            self.pos = types.SimpleNamespace(x=0, y=0)
            self.clicks = 0

        def position(self) -> Any:
            return self.pos

        def click(self, x: int, y: int) -> None:
            self.pos = types.SimpleNamespace(x=x, y=y)
            self.clicks += 1
            if self.clicks >= 2:  # 1 palette click + 1 canvas pixel click
                worker_ref["w"].stop_bot()

    worker = BotWorker(
        dm,
        mouse_controller=StopAfterFirstPixelMouse(),
        palette_switch_delay=0.0,
        counter_provider=stop_counter_provider,
    )
    worker_ref["w"] = worker

    q: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        q,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 50, "tolerance": 1, "delay": 100},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=3.0)

    assert phases == ["before_paint", "stopped"]

