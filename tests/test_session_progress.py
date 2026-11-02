"""Unit tests for P1 persistent session progress saving and safe resume across stops and restarts."""

from __future__ import annotations

import importlib
import queue
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from core.bot_worker import BotWorker
from core.data_manager import DataManager
from core.screen_capture import MonitorInfo
from core.session_progress import (
    SessionProgress,
    compute_template_fingerprint,
    load_session_progress,
    save_session_progress,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"


def _make_stub_mouse(
    clicks: list[tuple[int, int]],
    monitors: list[MonitorInfo] | None = None,
) -> Any:
    cursor = types.SimpleNamespace(x=0, y=0)

    class StubMouse:
        def position(self) -> types.SimpleNamespace:
            return cursor

        def monitors(self) -> list[MonitorInfo]:
            if monitors is not None:
                return monitors
            return [
                MonitorInfo(
                    index=1, left=0, top=0, width=1920, height=1080, is_primary=True
                )
            ]

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))
            cursor.x, cursor.y = x, y

    return StubMouse()


# ---------------------------------------------------------------------------
# 1. Stop / limit mid-run persists progress; next run paints ONLY remaining
# ---------------------------------------------------------------------------


def test_stop_or_limit_persists_progress_and_next_run_skips_painted_pixels(
    tmp_path: Path,
) -> None:
    """Pixels painted before stopping must be persisted and skipped on subsequent runs."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 300, 300))
    dm.set_palette_region((100, 500, 200, 60))

    # 6 unpainted Red pixels at (10,10)..(60,10)
    pixel_map = {
        (x, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}
        for x in (10, 20, 30, 40, 50, 60)
    }
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map=pixel_map,
        color_position_map={(237, 28, 36): (120, 520)},
    )
    red_list = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]

    # Run 1: paint first 4 pixels (limit=4)
    clicks_run1: list[tuple[int, int]] = []
    worker1 = BotWorker(
        dm, mouse_controller=_make_stub_mouse(clicks_run1), palette_switch_delay=0.0
    )
    q1: queue.Queue[dict[str, Any]] = queue.Queue()
    worker1.start_bot(q1, red_list, {"pixel_limit": 4, "tolerance": 1, "delay": 0})
    assert worker1.thread is not None
    worker1.thread.join(timeout=2.0)

    assert clicks_run1 == [
        (120, 520),  # Red swatch
        (110, 110),
        (120, 110),
        (130, 110),
        (140, 110),
    ]
    assert len(dm.painted_positions) == 4
    assert dm.has_resumable_progress() is True
    assert Path(dm.progress_file).exists()

    # Run 2: resume on same DataManager with limit=10 -> paints ONLY (50,10) and (60,10)!
    clicks_run2: list[tuple[int, int]] = []
    worker2 = BotWorker(
        dm, mouse_controller=_make_stub_mouse(clicks_run2), palette_switch_delay=0.0
    )
    q2: queue.Queue[dict[str, Any]] = queue.Queue()
    worker2.start_bot(q2, red_list, {"pixel_limit": 10, "tolerance": 1, "delay": 0})
    assert worker2.thread is not None
    worker2.thread.join(timeout=2.0)

    assert clicks_run2 == [
        (120, 520),  # Red swatch
        (150, 110),
        (160, 110),
    ]
    assert len(dm.painted_positions) == 6


# ---------------------------------------------------------------------------
# 2. Full application restart restores session & resumes safely from exact pixel
# ---------------------------------------------------------------------------


def test_application_restart_restores_session_and_resumes_exact_remaining_pixels(
    tmp_path: Path,
) -> None:
    """A new DataManager instance after restart must restore progress and resume cleanly."""
    settings_path = str(tmp_path / "settings.json")

    dm1 = DataManager(colors_file=str(COLORS_JSON_PATH), settings_file=settings_path)
    dm1.set_canvas_region((200, 150, 400, 300))
    dm1.set_palette_region((200, 500, 300, 60))

    # 3 Red pixels + 3 White pixels = 6 total
    pixel_map = {
        (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (20, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (30, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (10, 30): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
        (20, 30): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
        (30, 30): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
    }
    color_pos = {
        (237, 28, 36): (220, 520),
        (255, 255, 255): (260, 520),
    }
    dm1.set_analysis_results(
        pixel_size=12,
        pixel_map=pixel_map,
        color_position_map=color_pos,
        canvas_scale=(1.0, 1.0),
    )

    colors = [
        {"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False},
        {"id": 64, "name": "White", "rgb": [255, 255, 255], "premium": False},
    ]

    # Paint 4 pixels before closing the app (all 3 Red + 1st White pixel at (10, 30))
    clicks_before_restart: list[tuple[int, int]] = []
    w1 = BotWorker(
        dm1,
        mouse_controller=_make_stub_mouse(clicks_before_restart),
        palette_switch_delay=0.0,
    )
    q1: queue.Queue[dict[str, Any]] = queue.Queue()
    w1.start_bot(q1, colors, {"pixel_limit": 4, "tolerance": 1, "delay": 0})
    assert w1.thread is not None
    w1.thread.join(timeout=2.0)

    assert len(dm1.painted_positions) == 4

    # Simulate complete app restart: construct fresh DataManager reading disk files
    dm2 = DataManager(colors_file=str(COLORS_JSON_PATH), settings_file=settings_path)
    assert dm2.has_analysis_data() is True
    assert dm2.has_resumable_progress() is True
    assert dm2.pixel_size == 12
    assert len(dm2.painted_positions) == 4

    # Resume on dm2 -> should ONLY click White swatch (260, 520) and remaining 2 White pixels!
    clicks_after_restart: list[tuple[int, int]] = []
    w2 = BotWorker(
        dm2,
        mouse_controller=_make_stub_mouse(clicks_after_restart),
        palette_switch_delay=0.0,
    )
    q2: queue.Queue[dict[str, Any]] = queue.Queue()
    w2.start_bot(q2, colors, {"pixel_limit": 10, "tolerance": 1, "delay": 0})
    assert w2.thread is not None
    w2.thread.join(timeout=2.0)

    assert clicks_after_restart == [
        (260, 520),  # White swatch only (Red is 100% done!)
        (220, 180),  # (200+20, 150+30)
        (230, 180),  # (200+30, 150+30)
    ]
    assert len(dm2.painted_positions) == 6


# ---------------------------------------------------------------------------
# 3. Safety guards: region change invalidates stale progress; unplugged monitor aborts
# ---------------------------------------------------------------------------


def test_region_change_invalidates_stale_progress_and_unplugged_monitor_blocks_resume(
    tmp_path: Path,
) -> None:
    """Changing regions clears old progress; resuming on an unplugged monitor aborts safely."""
    settings_path = str(tmp_path / "settings.json")
    dual_monitors = [
        MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(index=2, left=1920, top=0, width=1920, height=1080),
    ]

    dm = DataManager(colors_file=str(COLORS_JSON_PATH), settings_file=settings_path)
    dm.set_canvas_region((2100, 100, 300, 300))
    dm.set_palette_region((2100, 500, 200, 60))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
            (20, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        },
        color_position_map={(237, 28, 36): (2120, 520)},
    )
    red_list = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]

    clicks1: list[tuple[int, int]] = []
    w1 = BotWorker(
        dm,
        mouse_controller=_make_stub_mouse(clicks1, monitors=dual_monitors),
        palette_switch_delay=0.0,
    )
    q1: queue.Queue[dict[str, Any]] = queue.Queue()
    w1.start_bot(q1, red_list, {"pixel_limit": 1, "tolerance": 1, "delay": 0})
    assert w1.thread is not None
    w1.thread.join(timeout=2.0)
    assert len(dm.painted_positions) == 1

    # Simulate app restart AFTER Monitor 2 was unplugged (only Monitor 1 remains!)
    dm_restarted = DataManager(
        colors_file=str(COLORS_JSON_PATH), settings_file=settings_path
    )
    single_monitor = [
        MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)
    ]
    clicks_unplugged: list[tuple[int, int]] = []
    w_unplugged = BotWorker(
        dm_restarted,
        mouse_controller=_make_stub_mouse(clicks_unplugged, monitors=single_monitor),
        palette_switch_delay=0.0,
    )
    q_err: queue.Queue[dict[str, Any]] = queue.Queue()
    w_unplugged.start_bot(
        q_err, red_list, {"pixel_limit": 5, "tolerance": 1, "delay": 0}
    )
    assert w_unplugged.thread is not None
    w_unplugged.thread.join(timeout=2.0)

    err_msg = q_err.get_nowait()
    assert err_msg["type"] == "bot_error"
    assert clicks_unplugged == []

    # Now user reselects a new Canvas region on Monitor 1 -> stale progress must be cleared!
    dm_restarted.set_canvas_region((100, 100, 300, 300))
    assert dm_restarted.painted_positions == set()
    assert dm_restarted.has_resumable_progress() is False
    assert not Path(dm_restarted.progress_file).exists()


# ---------------------------------------------------------------------------
# 4. Corrupted session_progress.json & non-mutating Dry-Run over saved session
# ---------------------------------------------------------------------------


def test_corrupted_progress_file_loads_cleanly_and_dry_run_preserves_progress(
    tmp_path: Path,
) -> None:
    """Corrupted JSON must not crash startup; Dry-Run must plan remaining pixels without mutating progress."""
    settings_path = tmp_path / "settings.json"
    progress_path = tmp_path / "session_progress.json"
    progress_path.write_text("{corrupted json...", encoding="utf-8")

    assert load_session_progress(str(progress_path)) is None

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH), settings_file=str(settings_path)
    )
    assert dm.has_resumable_progress() is False

    # Setup valid session with 1 painted out of 3 pixels
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 350, 200, 50))
    pixel_map = {
        (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (20, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (30, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
    }
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map=pixel_map,
        color_position_map={(237, 28, 36): (120, 370)},
    )
    dm.record_painted_pixel((10, 10), (36, 28, 237))
    dm.save_progress_checkpoint(status="stopped_by_user")

    # Run Dry-Run -> plans ONLY remaining 2 pixels and keeps painted_positions == {(10, 10)}!
    red_list = [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}]
    worker = BotWorker(dm, palette_switch_delay=0.0)
    q_dry: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        q_dry,
        red_list,
        {
            "pixel_limit": 10,
            "tolerance": 1,
            "delay": 15,
            "dry_run": True,
            "diff_output_path": str(tmp_path / "dry_resume.png"),
        },
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    dry_msgs: list[dict[str, Any]] = []
    while not q_dry.empty():
        dry_msgs.append(q_dry.get_nowait())
    done_msgs = [m for m in dry_msgs if m["type"] == "dry_run_complete"]
    assert len(done_msgs) == 1
    msg = done_msgs[0]
    assert msg["plan"].total_planned_pixels == 2
    assert dm.painted_positions == {(10, 10)}

    # Also verify fingerprint determinism and round-trip save/load
    fp1 = compute_template_fingerprint(pixel_map)
    fp2 = compute_template_fingerprint(dict(reversed(list(pixel_map.items()))))
    assert fp1 == fp2

    sp = SessionProgress(
        version=1,
        canvas_region=(100, 100, 200, 200),
        palette_region=(100, 350, 200, 50),
        pixel_size=10,
        canvas_scale=(1.0, 1.0),
        template_fingerprint=fp1,
        painted_positions={(10, 10)},
        pixel_map=pixel_map,
        color_position_map={(237, 28, 36): (120, 370)},
        status="stopped_by_user",
        updated_at="2026-11-02T12:00:00+00:00",
    )
    rt_path = tmp_path / "roundtrip.json"
    save_session_progress(str(rt_path), sp)
    loaded = load_session_progress(str(rt_path))
    assert loaded is not None
    assert loaded.painted_positions == {(10, 10)}
    assert loaded.template_fingerprint == fp1


# ---------------------------------------------------------------------------
# 5. ControlTab Resume & Reset Progress headless UI workflow
# ---------------------------------------------------------------------------


def test_control_tab_resume_bypasses_reanalysis_and_reset_clears_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ControlTab._resume_bot must resume directly without reanalysis; _reset_progress clears state."""
    fake_tk = types.ModuleType("tkinter")
    fake_ttk = types.ModuleType("tkinter.ttk")
    fake_msgbox = types.ModuleType("tkinter.messagebox")
    fake_tk.END = "end"  # type: ignore[attr-defined]
    fake_tk.TclError = RuntimeError  # type: ignore[attr-defined]
    fake_tk.ttk = fake_ttk  # type: ignore[attr-defined]
    fake_tk.messagebox = fake_msgbox  # type: ignore[attr-defined]
    fake_msgbox.askyesno = lambda *_a, **_kw: True  # type: ignore[attr-defined]
    fake_ttk.Style = lambda: types.SimpleNamespace(configure=lambda *a, **kw: None)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", fake_msgbox)
    monkeypatch.delitem(sys.modules, "gui.tabs.control_tab", raising=False)

    control_mod = importlib.import_module("gui.tabs.control_tab")

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 350, 200, 50))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
            (20, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        },
        color_position_map={(237, 28, 36): (120, 370)},
    )
    dm.record_painted_pixel((10, 10), (36, 28, 237))
    dm.save_progress_checkpoint(status="stopped_by_user")

    class DummyVar:
        def __init__(self, value: Any) -> None:
            self._v = value

        def get(self) -> Any:
            return self._v

        def set(self, value: Any) -> None:
            self._v = value

    class DummyWidget:
        def __init__(self) -> None:
            self.state = "normal"
            self.text = ""
            self.lines: list[str] = []

        def config(self, **kwargs: Any) -> None:
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "text" in kwargs:
                self.text = kwargs["text"]

        def insert(self, _pos: Any, value: str) -> None:
            self.lines.append(value)

        def see(self, _pos: Any) -> None:
            pass

        def focus_set(self) -> None:
            pass

    started_settings: list[dict[str, Any]] = []
    reanalyze_calls: list[bool] = []

    tab = control_mod.ControlTab.__new__(control_mod.ControlTab)
    tab.data_manager = dm
    tab.max_pixel_limit = 5000
    tab._syncing_pixel_limit = False
    tab._last_valid_pixel_limit = 500
    tab._save_timer = None
    tab._pending_start_after_analysis = False
    tab._pending_dry_run_after_analysis = False
    tab.pixel_limit_var = DummyVar(500)
    tab.pixel_limit_entry = DummyWidget()
    tab.reanalyze_var = DummyVar(True)  # Even with reanalyze_var=True, _resume_bot resumes directly!
    tab.dry_run_var = DummyVar(False)
    tab.start_btn = DummyWidget()
    tab.resume_btn = DummyWidget()
    tab.reset_progress_btn = DummyWidget()
    tab.dry_run_btn = DummyWidget()
    tab.stop_btn = DummyWidget()
    tab.status_label = DummyWidget()
    tab.session_info_label = DummyWidget()
    tab.progress_var = DummyVar(0.0)
    tab.log_text = DummyWidget()
    tab.message_queue = queue.Queue()
    tab.bot_worker = types.SimpleNamespace(
        start_bot=lambda q, colors, settings: started_settings.append(settings),
        stop_bot=lambda: None,
        thread=None,
    )
    tab.main_window = types.SimpleNamespace(
        is_running=False,
        save_user_settings=lambda: None,
        root=types.SimpleNamespace(after_cancel=lambda _t: None),
        setup_tab=types.SimpleNamespace(
            _analyze_regions=lambda: reanalyze_calls.append(True),
            tolerance_var=DummyVar(1),
            delay_var=DummyVar(15),
        ),
        get_enabled_colors=lambda: [
            {"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}
        ],
    )

    tab.refresh_session_status()
    assert "1" in tab.session_info_label.text
    assert tab.resume_btn.state == "normal"

    # Clicking Resume starts BotWorker directly WITHOUT triggering reanalysis!
    tab._resume_bot()
    assert reanalyze_calls == []
    assert len(started_settings) == 1
    assert started_settings[0]["dry_run"] is False

    # Simulate completion and then Reset Progress
    tab.on_bot_complete(total_painted=1, limit_reached=False)
    tab._reset_progress()
    assert dm.has_resumable_progress() is False
    assert tab.resume_btn.state == "disabled"
