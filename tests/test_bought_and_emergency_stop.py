"""Unit tests for bought premium color filtering, Emergency Stop, and thread-safe GUI logging."""

from __future__ import annotations

import importlib
import queue
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.data_manager import DataManager

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"


# ---------------------------------------------------------------------------
# Helpers & Stubs
# ---------------------------------------------------------------------------


@dataclass
class StubWidget:
    """Minimal widget stub for headless UI state verification."""

    style: str = "TEntry"
    state: str = "normal"
    text: str = ""
    focused: bool = False
    scale_value: float = 1000.0
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
        if self.command is not None:
            self.command(str(self.scale_value))

    def focus_set(self) -> None:
        self.focused = True


class StubBoolVar:
    """Minimal BooleanVar stub."""

    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = bool(value)


def _install_fake_tkinter(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[str, str]], type[Exception]]:
    """Register headless tkinter modules in sys.modules for GUI unit testing."""

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
        "app",
        "gui.tabs.control_tab",
        "gui.tabs.setup_tab",
        "gui.tabs.colors_tab",
        "gui.tabs.preview_tab",
        "gui.tabs",
        "gui.region_selector",
        "gui",
    ]:
        monkeypatch.delitem(sys.modules, mod_name, raising=False)

    return shown_errors, FakeTclError


# ---------------------------------------------------------------------------
# 1. Bought status filtering for premium colors
# ---------------------------------------------------------------------------


def test_data_manager_is_color_bought_and_paintable_colors(tmp_path: Path) -> None:
    """Default profile has 63 enabled colors, but only 31 free colors are paintable until bought."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )

    black = next(c for c in dm.color_palette if c["id"] == 1)
    white = next(c for c in dm.color_palette if c["id"] == 64)
    medium_gray = next(c for c in dm.color_palette if c["id"] == 32)  # premium

    assert dm.is_color_bought(black) is True
    assert dm.is_color_bought(white) is True
    assert dm.is_color_bought(medium_gray) is False

    paintable = dm.get_paintable_colors()
    assert len(paintable) == 31
    assert all(not c.get("premium", False) for c in paintable)
    assert any(c["id"] == 64 for c in paintable)

    # Mark premium id=32 as bought -> becomes paintable (32 total)
    dm.set_color_setting(32, "bought", True)
    assert dm.is_color_bought(medium_gray) is True
    assert len(dm.get_paintable_colors()) == 32

    # Disable premium id=32 -> excluded again (31 total)
    dm.set_color_setting(32, "enabled", False)
    assert len(dm.get_paintable_colors()) == 31


def test_colors_tab_get_enabled_colors_excludes_unbought_premium(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ColorsTab.get_enabled_colors must filter out premium colors with bought=False."""
    _install_fake_tkinter(monkeypatch)
    colors_tab_mod = importlib.import_module("gui.tabs.colors_tab")
    ColorsTab = colors_tab_mod.ColorsTab

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )

    tab = ColorsTab.__new__(ColorsTab)
    tab.data_manager = dm
    # Simulate "Enable All" in UI: every color_var is True, every premium bought_var is False
    tab.color_vars = {c["name"]: StubBoolVar(True) for c in dm.color_palette}
    tab.bought_vars = {
        c["name"]: StubBoolVar(False)
        for c in dm.color_palette
        if c.get("premium", False)
    }

    result = tab.get_enabled_colors()
    assert len(result) == 31
    assert all(not c.get("premium", False) for c in result)

    # Mark one premium color as bought in UI checkbox (even before debounced save!)
    tab.bought_vars["Medium Gray"].set(True)
    result_after_buy = tab.get_enabled_colors()
    assert len(result_after_buy) == 32
    assert any(c["name"] == "Medium Gray" for c in result_after_buy)


def test_bot_worker_skips_unbought_premium_colors_defense_in_depth(
    tmp_path: Path,
) -> None:
    """BotWorker must skip unbought premium colors even if passed in enabled_colors."""
    from core.bot_worker import BotWorker

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 200, 400, 400))
    dm.set_palette_region((100, 700, 400, 60))

    free_red = {"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}
    unbought_gray = {
        "id": 32,
        "name": "Medium Gray",
        "rgb": [170, 170, 170],
        "premium": True,
    }
    bought_olive = {"id": 41, "name": "Olive", "rgb": [90, 148, 74], "premium": True}
    dm.set_color_setting(41, "bought", True)  # 41 is bought, 32 is NOT bought

    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (255, 255, 255)},
            (20, 10): {
                "preview_color": (170, 170, 170),
                "pixel_color": (255, 255, 255),
            },
            (30, 10): {"preview_color": (74, 148, 90), "pixel_color": (255, 255, 255)},
        },
        color_position_map={
            (237, 28, 36): (110, 710),
            (170, 170, 170): (150, 710),  # Locked swatch on screen!
            (90, 148, 74): (190, 710),
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

    worker = BotWorker(dm, mouse_controller=StubMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    worker.start_bot(
        msg_queue,
        [free_red, unbought_gray, bought_olive],
        {"pixel_limit": 100, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    # Must NEVER click locked swatch (150, 710) or canvas spot (120, 210)!
    assert (150, 710) not in clicks
    assert (120, 210) not in clicks
    # Must click free Red (110, 710)->(110, 210) and bought Olive (190, 710)->(130, 210)
    assert clicks == [(110, 710), (110, 210), (190, 710), (130, 210)]


# ---------------------------------------------------------------------------
# 2. Emergency Stop (interruptible sleep, mouse movement during click, FailSafe)
# ---------------------------------------------------------------------------


def test_bot_worker_stop_bot_interrupts_long_delay_immediately() -> None:
    """stop_bot() must wake threading.Event.wait immediately instead of sleeping full delay."""
    from core.bot_worker import BotWorker

    dm = types.SimpleNamespace(
        canvas_region=(0, 0, 500, 500),
        palette_region=(0, 600, 400, 50),
        color_position_map={(237, 28, 36): (20, 620)},
        pixel_map={
            (i * 5, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}
            for i in range(10)
        },
    )
    pos = types.SimpleNamespace(x=0, y=0)
    first_canvas_clicked = threading.Event()

    class StubMouse:
        def position(self) -> types.SimpleNamespace:
            return pos

        def click(self, x: int, y: int) -> None:
            pos.x, pos.y = x, y
            if y < 600:
                first_canvas_clicked.set()

    worker = BotWorker(dm, mouse_controller=StubMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    start_time = time.monotonic()
    # 5000 ms delay per click! Without Event.wait, stopping would hang 5 full seconds.
    worker.start_bot(
        msg_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 5000},
    )
    assert first_canvas_clicked.wait(timeout=1.0)
    worker.stop_bot()

    assert worker.thread is not None
    worker.thread.join(timeout=1.0)
    elapsed = time.monotonic() - start_time

    assert not worker.thread.is_alive()
    assert elapsed < 0.8

    messages = []
    while not msg_queue.empty():
        messages.append(msg_queue.get_nowait())
    complete = next(m for m in messages if m["type"] == "bot_complete")
    assert complete["stopped_by_user"] is True
    assert complete["total_painted"] == 1


def test_bot_worker_detects_mouse_movement_even_during_click() -> None:
    """Moving mouse during click must not be masked by overwriting last_bot_mouse_pos."""
    from core.bot_worker import BotWorker

    dm = types.SimpleNamespace(
        canvas_region=(100, 100, 500, 500),
        palette_region=(100, 700, 400, 50),
        color_position_map={(237, 28, 36): (120, 720)},
        pixel_map={
            (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
            (20, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
            (30, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        },
    )
    pos = types.SimpleNamespace(x=0, y=0)
    clicks: list[tuple[int, int]] = []

    class UserJerksMouseDuringClick:
        def position(self) -> types.SimpleNamespace:
            return pos

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))
            if len(clicks) == 2:  # First canvas click -> user jerks mouse +80px away!
                pos.x, pos.y = x + 80, y + 80
            else:
                pos.x, pos.y = x, y

    worker = BotWorker(
        dm, mouse_controller=UserJerksMouseDuringClick(), palette_switch_delay=0.0
    )
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        msg_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    # Stopped right after first canvas click (1 palette + 1 canvas = 2 clicks)
    assert len(clicks) == 2
    messages = []
    while not msg_queue.empty():
        messages.append(msg_queue.get_nowait())
    complete = next(m for m in messages if m["type"] == "bot_complete")
    assert complete["cancelled_by_mouse"] is True
    assert complete["total_painted"] == 1


def test_bot_worker_handles_failsafe_exception_as_emergency_stop() -> None:
    """PyAutoGUI FailSafeException must stop gracefully with cancelled_by_mouse=True."""
    from core.bot_worker import BotWorker

    class FailSafeException(Exception):
        pass

    dm = types.SimpleNamespace(
        canvas_region=(100, 100, 500, 500),
        palette_region=(100, 700, 400, 50),
        color_position_map={(237, 28, 36): (120, 720)},
        pixel_map={
            (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        },
    )
    pos = types.SimpleNamespace(x=0, y=0)

    class FailSafeMouse:
        def position(self) -> types.SimpleNamespace:
            return pos

        def click(self, x: int, y: int) -> None:
            raise FailSafeException("PyAutoGUI fail-safe triggered from mouse moving to a corner")

    worker = BotWorker(dm, mouse_controller=FailSafeMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        msg_queue,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {"pixel_limit": 10, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    msg = msg_queue.get_nowait()
    assert msg["type"] == "bot_complete"
    assert msg["cancelled_by_mouse"] is True
    assert msg["total_painted"] == 0


# ---------------------------------------------------------------------------
# 3. ControlTab Stop button & Reanalyze error state recovery
# ---------------------------------------------------------------------------


def test_control_tab_stop_button_and_reanalyze_error_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ControlTab must block reanalyze without regions, recover on analysis error, and stop bot."""
    shown_errors, _fake_tcl_error = _install_fake_tkinter(monkeypatch)
    control_tab_mod = importlib.import_module("gui.tabs.control_tab")
    ControlTab = control_tab_mod.ControlTab

    stop_calls = {"bot": 0, "analysis": 0}
    start_calls: list[dict[str, Any]] = []

    tab = ControlTab.__new__(ControlTab)
    tab.max_pixel_limit = 5000
    tab._syncing_pixel_limit = False
    tab._last_valid_pixel_limit = 1000
    tab._save_timer = None
    tab._pending_start_after_analysis = False
    tab.pixel_limit_var = types.SimpleNamespace(get=lambda: 1000, set=lambda v: None)
    tab.pixel_limit_entry = StubWidget()
    tab.pixel_limit_scale = StubWidget()
    tab.start_btn = StubWidget(state="normal")
    tab.stop_btn = StubWidget(state="disabled")
    tab.status_label = StubWidget(text="Ready")
    tab.reanalyze_var = StubBoolVar(True)

    has_regions = {"val": False}
    tab.data_manager = types.SimpleNamespace(
        has_analysis_data=lambda: False,
        has_regions=lambda: has_regions["val"],
    )
    tab.message_queue = queue.Queue()
    tab.bot_worker = types.SimpleNamespace(
        start_bot=lambda q, c, s: start_calls.append(s),
        stop_bot=lambda: stop_calls.__setitem__("bot", stop_calls["bot"] + 1),
    )
    tab.main_window = types.SimpleNamespace(
        is_running=False,
        analysis_worker=types.SimpleNamespace(
            stop_analysis=lambda: stop_calls.__setitem__(
                "analysis", stop_calls["analysis"] + 1
            )
        ),
        root=types.SimpleNamespace(
            after=lambda ms, fn: "t1",
            after_cancel=lambda tid: None,
        ),
        save_user_settings=lambda: None,
        get_enabled_colors=list,
        setup_tab=types.SimpleNamespace(
            _analyze_regions=lambda: None,
            tolerance_var=types.SimpleNamespace(get=lambda: 1),
            delay_var=types.SimpleNamespace(get=lambda: 15),
        ),
    )
    tab.log_message = lambda msg: None

    # A) Reanalyze checked, but regions NOT selected -> error shown, is_running stays False!
    tab._start_bot()
    assert len(shown_errors) == 1
    assert tab.main_window.is_running is False
    assert tab.start_btn.state == "normal"
    assert tab.stop_btn.state == "disabled"

    # B) Regions selected -> Start triggers reanalysis, enables stop_btn!
    has_regions["val"] = True
    tab._start_bot()
    assert tab.main_window.is_running is True
    assert tab._pending_start_after_analysis is True
    assert tab.start_btn.state == "disabled"
    assert tab.stop_btn.state == "normal"

    # C) Analysis fails -> resets is_running & _pending_start_after_analysis!
    tab.on_analysis_error("Capture failed")
    assert tab.main_window.is_running is False
    assert tab._pending_start_after_analysis is False
    assert tab.stop_btn.state == "disabled"
    assert tab.start_btn.state == "normal"

    # D) Subsequent manual analysis completion must NOT auto-start bot!
    tab.enable_start_button()
    assert len(start_calls) == 0

    # E) Clicking Stop during active run calls stop_bot() and stop_analysis()
    tab._start_bot()
    assert tab.stop_btn.state == "normal"
    tab._stop_bot()
    assert stop_calls["bot"] == 1
    assert stop_calls["analysis"] == 1
    assert tab._pending_start_after_analysis is False


# ---------------------------------------------------------------------------
# 4. Thread-safe GUI logging via message_queue
# ---------------------------------------------------------------------------


def test_gui_log_callback_enqueues_from_worker_thread_instead_of_direct_tk_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logging from a worker thread must enqueue to message_queue and only touch UI in process_queue."""
    _install_fake_tkinter(monkeypatch)
    app_mod = importlib.import_module("app")
    PlaceBotGUI = app_mod.PlaceBotGUI

    ui_log_calls: list[tuple[str, str]] = []

    gui = PlaceBotGUI.__new__(PlaceBotGUI)
    gui.message_queue = queue.Queue()
    gui.root = types.SimpleNamespace(after=lambda ms, fn: None)
    gui.control_tab = types.SimpleNamespace(
        log_message=lambda msg: ui_log_calls.append(
            (threading.current_thread().name, msg)
        )
    )

    def worker_fn() -> None:
        gui._gui_log_callback("[INFO] background worker log")

    t = threading.Thread(target=worker_fn, name="WorkerThread-Test")
    t.start()
    t.join(timeout=2.0)

    # Worker thread must NOT have invoked control_tab.log_message directly!
    assert ui_log_calls == []
    assert not gui.message_queue.empty()

    # Main thread drains queue via process_queue() -> delivers log safely on MainThread
    gui.process_queue()
    assert len(ui_log_calls) == 1
    thread_name, delivered_msg = ui_log_calls[0]
    assert thread_name == threading.main_thread().name
    assert delivered_msg == "[INFO] background worker log"
