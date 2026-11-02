"""Unit tests for pixel limit configuration, validation, UI sync, and BotWorker enforcement."""

from __future__ import annotations

import importlib
import queue
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.config import (
    DEFAULT_PIXEL_LIMIT,
    MAX_PIXEL_LIMIT,
    MIN_PIXEL_LIMIT,
    Config,
    clamp_pixel_limit,
    validate_pixel_limit,
)
from core.exceptions import ConfigError


class FakeMouseController:
    """In-memory mouse controller stub for testing BotWorker without OS GUI."""

    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self._pos = types.SimpleNamespace(x=0, y=0)

    def position(self) -> types.SimpleNamespace:
        return self._pos

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        self._pos = types.SimpleNamespace(x=x, y=y)


@dataclass
class FakeStyleWidget:
    """Minimal widget stub tracking config, slider set, and focus calls."""

    style: str = "TEntry"
    state: str = "normal"
    text: str = ""
    focused: bool = False
    scale_value: float = float(DEFAULT_PIXEL_LIMIT)
    command: Any = None

    def config(self, **kwargs: object) -> None:
        if "style" in kwargs:
            self.style = str(kwargs["style"])
        if "state" in kwargs:
            self.state = str(kwargs["state"])
        if "text" in kwargs:
            self.text = str(kwargs["text"])

    def set(self, value: float) -> None:
        self.scale_value = float(value)
        # Real ttk.Scale invokes its command callback when .set() is called
        if self.command is not None:
            self.command(str(self.scale_value))

    def focus_set(self) -> None:
        self.focused = True


class FakeIntVar:
    """Stub mimicking tkinter.IntVar including empty-value TclError behavior."""

    def __init__(self, value: int | str | None, error_cls: type[Exception]) -> None:
        self._value: int | str | None = value
        self._error_cls = error_cls

    def get(self) -> int:
        if self._value == "" or self._value is None:
            raise self._error_cls('expected integer but got ""')
        return int(self._value)

    def set(self, value: int | str | None) -> None:
        self._value = value


# ---------------------------------------------------------------------------
# 1. Config & JSON defaults
# ---------------------------------------------------------------------------


def test_pixel_limit_constants_and_default_config(tmp_path: Path) -> None:
    """Verify pixel limit constants and Config defaults use max 5000."""
    assert MIN_PIXEL_LIMIT == 1
    assert DEFAULT_PIXEL_LIMIT == 50
    assert MAX_PIXEL_LIMIT == 5000

    cfg = Config(config_file=str(tmp_path / "nonexistent.json"))
    assert cfg.get("bot.default_pixel_limit") == 50
    assert cfg.get("bot.max_pixel_limit") == 5000


def test_project_config_json_has_max_5000() -> None:
    """Verify repository config.json sets bot.max_pixel_limit to 5000."""
    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(config_file=str(project_root / "config.json"))
    assert cfg.get("bot.max_pixel_limit") == 5000


def test_config_does_not_mutate_default_config(tmp_path: Path) -> None:
    """Verify modifying an instance does not mutate Config.DEFAULT_CONFIG."""
    cfg = Config(config_file=str(tmp_path / "cfg.json"))
    cfg.set("bot.max_pixel_limit", 1234)
    assert Config.DEFAULT_CONFIG["bot"]["max_pixel_limit"] == 5000


# ---------------------------------------------------------------------------
# 2. validate_pixel_limit & clamp_pixel_limit boundary cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        (1, 1),
        (50, 50),
        (1000, 1000),
        (1001, 1001),
        (2500, 2500),
        (5000, 5000),
        ("1500", 1500),
        (" 5000 ", 5000),
    ],
)
def test_validate_pixel_limit_accepts_valid_range(
    raw_value: object, expected: int
) -> None:
    """Values from 1 through 5000 (inclusive) must pass validation."""
    assert validate_pixel_limit(raw_value) == expected


@pytest.mark.parametrize(
    "invalid_value",
    [
        0,
        -1,
        -100,
        5001,
        10000,
        "",
        "   ",
        "abc",
        "12.5",
        None,
        True,
        False,
        15.5,
    ],
)
def test_validate_pixel_limit_rejects_out_of_bounds_and_bad_types(
    invalid_value: object,
) -> None:
    """Values outside 1..5000 or non-integers must raise ConfigError."""
    with pytest.raises(ConfigError):
        validate_pixel_limit(invalid_value)


def test_clamp_pixel_limit_normalizes_stored_preferences() -> None:
    """clamp_pixel_limit clamps out-of-range integers and falls back on bad types."""
    assert clamp_pixel_limit(2500) == 2500
    assert clamp_pixel_limit(5000) == 5000
    assert clamp_pixel_limit(9000) == 5000
    assert clamp_pixel_limit(0) == 1
    assert clamp_pixel_limit("bad") == DEFAULT_PIXEL_LIMIT
    assert clamp_pixel_limit(True) == DEFAULT_PIXEL_LIMIT


# ---------------------------------------------------------------------------
# 3. BotWorker enforcement for >1000 up to 5000 & Emergency Stop
# ---------------------------------------------------------------------------


def _make_fake_data_manager(total_spots: int) -> object:
    """Build a lightweight DataManager-compatible stub with `total_spots` unpainted pixels."""
    target_rgb = (237, 28, 36)
    target_bgr = (36, 28, 237)
    wrong_bgr = (255, 255, 255)
    pixel_map = {
        ((idx % 100) * 5 + 10, (idx // 100) * 5 + 10): {
            "preview_color": target_bgr,
            "pixel_color": wrong_bgr,
        }
        for idx in range(total_spots)
    }
    return types.SimpleNamespace(
        canvas_region=(100, 200, 800, 800),
        palette_region=(100, 1000, 400, 60),
        color_position_map={target_rgb: (120, 1020)},
        pixel_map=pixel_map,
    )


def test_bot_worker_paints_above_1000_up_to_5000_limit() -> None:
    """BotWorker must accept and paint e.g. 1500 pixels when limit is 1500."""
    from core.bot_worker import BotWorker

    dm = _make_fake_data_manager(total_spots=2000)
    mouse = FakeMouseController()
    worker = BotWorker(dm, mouse_controller=mouse, palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict] = queue.Queue()
    enabled_colors = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]

    worker.start_bot(
        msg_queue,
        enabled_colors,
        {"pixel_limit": 1500, "tolerance": 5, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=5.0)
    assert not worker.thread.is_alive()

    messages = []
    while not msg_queue.empty():
        messages.append(msg_queue.get_nowait())

    complete_msgs = [m for m in messages if m["type"] == "bot_complete"]
    assert len(complete_msgs) == 1
    assert complete_msgs[0]["total_painted"] == 1500
    assert complete_msgs[0]["limit_reached"] is True
    # 1 palette click + 1500 canvas clicks
    assert len(mouse.clicks) == 1501


def test_bot_worker_accepts_exact_5000_limit() -> None:
    """BotWorker must accept pixel_limit == 5000 and stop at 5000."""
    from core.bot_worker import BotWorker

    dm = _make_fake_data_manager(total_spots=5100)
    mouse = FakeMouseController()
    worker = BotWorker(dm, mouse_controller=mouse, palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict] = queue.Queue()
    enabled_colors = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]

    worker.start_bot(
        msg_queue,
        enabled_colors,
        {"pixel_limit": 5000, "tolerance": 5, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=5.0)
    assert not worker.thread.is_alive()

    messages = []
    while not msg_queue.empty():
        messages.append(msg_queue.get_nowait())

    complete_msgs = [m for m in messages if m["type"] == "bot_complete"]
    assert len(complete_msgs) == 1
    assert complete_msgs[0]["total_painted"] == 5000
    assert complete_msgs[0]["limit_reached"] is True
    assert len(mouse.clicks) == 5001


def test_bot_worker_rejects_limit_above_5000() -> None:
    """BotWorker must reject pixel_limit > 5000 with bot_error and 0 clicks."""
    from core.bot_worker import BotWorker

    dm = _make_fake_data_manager(total_spots=100)
    mouse = FakeMouseController()
    worker = BotWorker(dm, mouse_controller=mouse, palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict] = queue.Queue()
    enabled_colors = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]

    worker.start_bot(
        msg_queue,
        enabled_colors,
        {"pixel_limit": 5001, "tolerance": 5, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    msg = msg_queue.get_nowait()
    assert msg["type"] == "bot_error"
    assert "5000" in msg["error"]
    assert len(mouse.clicks) == 0


def test_bot_worker_emergency_stop_halts_immediately_with_high_limit() -> None:
    """Emergency Stop (stop_bot) must halt BotWorker even when pixel_limit is 5000."""
    from core.bot_worker import BotWorker

    dm = _make_fake_data_manager(total_spots=5000)
    mouse = FakeMouseController()
    worker = BotWorker(dm, mouse_controller=mouse, palette_switch_delay=0.0)

    orig_click = mouse.click

    def stop_after_five_canvas_clicks(x: int, y: int) -> None:
        orig_click(x, y)
        # 1 palette click + 5 pixel clicks = 6 total clicks
        if len(mouse.clicks) >= 6:
            worker.stop_bot()

    mouse.click = stop_after_five_canvas_clicks  # type: ignore[method-assign]

    msg_queue: queue.Queue[dict] = queue.Queue()
    enabled_colors = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]

    worker.start_bot(
        msg_queue,
        enabled_colors,
        {"pixel_limit": 5000, "tolerance": 5, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    messages = []
    while not msg_queue.empty():
        messages.append(msg_queue.get_nowait())

    complete_msgs = [m for m in messages if m["type"] == "bot_complete"]
    assert len(complete_msgs) == 1
    assert complete_msgs[0]["total_painted"] == 5
    assert complete_msgs[0]["limit_reached"] is False


# ---------------------------------------------------------------------------
# 4. ControlTab Entry <-> Slider sync & 5000 boundary behavior (headless)
# ---------------------------------------------------------------------------


def _load_headless_control_tab(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[tuple[str, str]], type[Exception]]:
    """Import ControlTab with a lightweight headless tkinter stub."""

    class FakeTclError(Exception):
        pass

    shown_errors: list[tuple[str, str]] = []

    fake_messagebox = types.ModuleType("tkinter.messagebox")
    fake_messagebox.showerror = lambda title, msg: shown_errors.append((title, msg))  # type: ignore[attr-defined]
    fake_messagebox.showwarning = lambda title, msg: None  # type: ignore[attr-defined]
    fake_messagebox.showinfo = lambda title, msg: None  # type: ignore[attr-defined]
    fake_messagebox.askyesno = lambda title, msg: False  # type: ignore[attr-defined]

    fake_ttk = types.ModuleType("tkinter.ttk")
    fake_tk = types.ModuleType("tkinter")
    fake_tk.TclError = FakeTclError  # type: ignore[attr-defined]
    fake_tk.END = "end"  # type: ignore[attr-defined]
    fake_tk.ttk = fake_ttk  # type: ignore[attr-defined]
    fake_tk.messagebox = fake_messagebox  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", fake_messagebox)
    for mod_name in [
        "gui.tabs.control_tab",
        "gui.tabs.setup_tab",
        "gui.tabs.colors_tab",
        "gui.tabs.preview_tab",
        "gui.tabs",
        "gui.region_selector",
        "gui",
    ]:
        monkeypatch.delitem(sys.modules, mod_name, raising=False)

    module = importlib.import_module("gui.tabs.control_tab")
    return module.ControlTab, shown_errors, FakeTclError


def test_control_tab_entry_accepts_values_above_1000_up_to_5000_and_rejects_above_5000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typing 1500..5000 updates slider & saves; typing 5001 warns and blocks start cleanly."""
    control_tab_cls, shown_errors, fake_tcl_error = _load_headless_control_tab(monkeypatch)

    saved_calls: list[int] = []
    started_settings: list[dict[str, Any]] = []

    tab = control_tab_cls.__new__(control_tab_cls)
    tab.max_pixel_limit = MAX_PIXEL_LIMIT
    tab._syncing_pixel_limit = False
    tab._last_valid_pixel_limit = DEFAULT_PIXEL_LIMIT
    tab.pixel_limit_var = FakeIntVar(DEFAULT_PIXEL_LIMIT, fake_tcl_error)
    tab.pixel_limit_entry = FakeStyleWidget()
    tab.pixel_limit_scale = FakeStyleWidget(command=tab._update_pixel_limit_from_slider)
    tab.start_btn = FakeStyleWidget(state="normal")
    tab.status_label = FakeStyleWidget(text="Ready")
    tab.reanalyze_var = types.SimpleNamespace(get=lambda: False)
    tab.data_manager = types.SimpleNamespace(has_analysis_data=lambda: True)
    tab.message_queue = queue.Queue()
    tab.bot_worker = types.SimpleNamespace(
        start_bot=lambda q, colors, settings: started_settings.append(settings)
    )
    tab.main_window = types.SimpleNamespace(
        is_running=False,
        root=types.SimpleNamespace(
            after=lambda ms, fn: (fn(), "timer-1")[1],
            after_cancel=lambda timer_id: None,
        ),
        save_user_settings=lambda: saved_calls.append(tab.get_pixel_limit_or_last_valid()),
        get_enabled_colors=list,
        setup_tab=types.SimpleNamespace(
            tolerance_var=types.SimpleNamespace(get=lambda: 5),
            delay_var=types.SimpleNamespace(get=lambda: 20),
        ),
    )
    tab.log_message = lambda msg: None

    # 1. Enter 2500 (> old 1000 limit): must be accepted, synced to slider, and saved!
    tab.pixel_limit_var.set(2500)
    tab._on_pixel_limit_entry_change()
    assert tab.pixel_limit_var.get() == 2500
    assert tab.pixel_limit_scale.scale_value == 2500.0
    assert tab.pixel_limit_entry.style == "TEntry"
    assert tab.get_pixel_limit_or_last_valid() == 2500
    assert saved_calls[-1] == 2500

    # 2. Enter 5000 (exact upper bound): must be accepted and start bot cleanly
    tab.pixel_limit_var.set(5000)
    tab._on_pixel_limit_entry_change()
    assert tab.pixel_limit_scale.scale_value == 5000.0
    assert tab.pixel_limit_entry.style == "TEntry"
    tab._start_bot()
    assert len(started_settings) == 1
    assert started_settings[0]["pixel_limit"] == 5000
    tab.main_window.is_running = False
    tab.start_btn.config(state="normal")

    # 3. Enter 5001 (above max 5000): shows Warning style, preserves last valid 5000,
    #    and blocks _start_bot with error mentioning 5000 WITHOUT locking is_running!
    tab.pixel_limit_var.set(5001)
    tab._on_pixel_limit_entry_change()
    assert tab.pixel_limit_entry.style == "Warning.TEntry"
    assert tab.get_pixel_limit_or_last_valid() == 5000

    tab._start_bot()
    assert len(started_settings) == 1  # Not started again
    assert tab.main_window.is_running is False
    assert tab.start_btn.state == "normal"
    assert len(shown_errors) == 1
    assert "5000" in shown_errors[-1][1]

    # 4. Empty string during editing: safe fallback without TclError crash
    tab.pixel_limit_var.set("")
    tab._on_pixel_limit_entry_change()
    assert tab.pixel_limit_entry.style == "Warning.TEntry"
    assert tab.get_pixel_limit_or_last_valid() == 5000
    tab._start_bot()
    assert len(shown_errors) == 2
    assert "5000" in shown_errors[-1][1]
    assert tab.main_window.is_running is False
