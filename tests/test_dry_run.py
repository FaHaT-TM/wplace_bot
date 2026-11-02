"""Unit tests for P1 Dry-Run mode: cursor simulation (0 clicks), Cyrillic PNG saving, diff view, and auto-tab focus."""

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

from core.bot_worker import BotWorker
from core.color_detection import save_palette_debug_image
from core.data_manager import DataManager
from core.image_analysis import estimate_pixel_size
from core.paint_planner import (
    build_paint_plan,
    format_duration,
    format_paint_plan_report,
    render_dry_run_diff_image,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"


# ---------------------------------------------------------------------------
# 1. Duration formatting & deterministic PaintPlan calculation
# ---------------------------------------------------------------------------


def test_format_duration_human_readable() -> None:
    """format_duration must format sub-minute, multi-minute, and multi-hour durations cleanly."""
    assert format_duration(0.0) == "0.0s"
    assert format_duration(12.34) == "12.3s"
    assert format_duration(65.0) == "1m 05s"
    assert format_duration(3661.0) == "1h 01m 01s"


def test_build_paint_plan_per_color_counts_limit_dedup_and_eta(tmp_path: Path) -> None:
    """build_paint_plan must compute per-color planned/deferred counts, dedup overlaps, and exact ETA."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 400, 400))
    dm.set_palette_region((100, 600, 300, 80))

    pixel_map: dict[tuple[int, int], dict[str, tuple[int, int, int]]] = {
        (10, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (20, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (30, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (40, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (50, 10): {"preview_color": (36, 28, 237), "pixel_color": (36, 28, 237)},
        (10, 30): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
        (20, 30): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
        (30, 30): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
        (40, 30): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
        (90, 90): {"preview_color": (250, 250, 255), "pixel_color": (0, 0, 0)},
    }
    pixel_map[(90, 90)] = {"preview_color": (250, 250, 250), "pixel_color": (0, 0, 0)}

    color_position_map = {
        (237, 28, 36): (120, 620),
        (255, 255, 255): (160, 620),
        (245, 245, 245): (200, 620),
        (148, 140, 107): (240, 620),
    }
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map=pixel_map,
        color_position_map=color_position_map,
    )

    colors = [
        {"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False},
        {"id": 64, "name": "White", "rgb": [255, 255, 255], "premium": False},
        {"id": 99, "name": "Near White", "rgb": [245, 245, 245], "premium": False},
        {"id": 62, "name": "Stone", "rgb": [148, 140, 107], "premium": True},
    ]

    plan = build_paint_plan(
        data_manager=dm,
        enabled_colors=colors,
        pixel_limit=6,
        tolerance=6,
        click_delay_ms=50,
        palette_switch_delay_s=0.2,
    )

    assert plan.total_detected_pixels == 10
    assert plan.total_matching_unpainted == 9
    assert plan.total_planned_pixels == 6
    assert plan.total_deferred_by_limit == 3
    assert plan.limit_will_be_reached is True
    assert plan.active_color_switches == 2
    assert list(plan.skipped_unbought_colors) == ["Stone"]

    assert [e.color_name for e in plan.color_entries] == ["Red", "White"]
    red_entry, white_entry = plan.color_entries
    assert (
        red_entry.matched_count,
        red_entry.planned_count,
        red_entry.deferred_by_limit_count,
    ) == (4, 4, 0)
    assert (
        white_entry.matched_count,
        white_entry.planned_count,
        white_entry.deferred_by_limit_count,
    ) == (5, 2, 3)

    assert plan.estimated_seconds == pytest.approx(0.65)

    report = format_paint_plan_report(plan)
    assert "DRY-RUN" in report.upper()
    assert "Red" in report
    assert "White" in report
    assert "Stone" in report


# ---------------------------------------------------------------------------
# 2. Visual Diff Preview & Unicode / Non-ASCII directory PNG saving
# ---------------------------------------------------------------------------


def test_render_dry_run_diff_and_debug_images_in_unicode_directory(
    tmp_path: Path,
) -> None:
    """All debug and Dry-Run PNG writers must succeed in directories with non-ASCII characters."""
    unicode_dir = tmp_path / "User_Folder_\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430"
    unicode_dir.mkdir(parents=True)

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(unicode_dir / "settings.json"),
    )
    dm.set_canvas_region((0, 0, 120, 80))
    dm.set_palette_region((0, 100, 120, 40))

    canvas_rgb = np.full((80, 120, 3), 40, dtype=np.uint8)
    pixel_map = {
        (20, 20): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (50, 20): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        (80, 20): {"preview_color": (36, 28, 237), "pixel_color": (36, 28, 237)},
    }
    dm.set_analysis_results(
        pixel_size=12,
        pixel_map=pixel_map,
        color_position_map={(237, 28, 36): (20, 120)},
        canvas_image_rgb=canvas_rgb,
    )

    plan = build_paint_plan(
        data_manager=dm,
        enabled_colors=[{"id": 7, "name": "Red", "rgb": [237, 28, 237], "premium": False}],
        pixel_limit=1,
        tolerance=2,
        click_delay_ms=15,
    )
    plan = build_paint_plan(
        data_manager=dm,
        enabled_colors=[{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        pixel_limit=1,
        tolerance=2,
        click_delay_ms=15,
    )

    out_with_bg = unicode_dir / "diff_with_bg.png"
    saved_path = render_dry_run_diff_image(
        plan=plan,
        pixel_map=pixel_map,
        pixel_size=12,
        canvas_image_rgb=canvas_rgb,
        output_path=str(out_with_bg),
    )
    assert Path(saved_path).exists()
    with Image.open(saved_path) as img:
        rgb_arr = np.array(img.convert("RGB"))
    assert rgb_arr.shape == (80, 120, 3)
    # Planned center (20, 20) should have Red fill (RGB: 237, 28, 36)
    assert tuple(int(c) for c in rgb_arr[20, 20]) == (237, 28, 36)

    # Palette debug image in non-ASCII path
    palette_debug = unicode_dir / "debug_palette.png"
    save_palette_debug_image(
        np.full((40, 120, 3), 128, dtype=np.uint8),
        {(237, 28, 36): (20, 120)},
        (0, 100, 120, 40),
        filename=str(palette_debug),
    )
    assert palette_debug.exists()

    # Size estimation debug image in non-ASCII path (synthetic grid with >=10 squares)
    grid_bgr = np.zeros((160, 160, 3), dtype=np.uint8)
    for row in range(4):
        for col in range(4):
            y0, x0 = 10 + row * 30, 10 + col * 30
            grid_bgr[y0 : y0 + 10, x0 : x0 + 10] = 255
    size_debug = unicode_dir / "debug_size_estimation.png"
    estimate_pixel_size(grid_bgr, debug_filename=str(size_debug))
    assert size_debug.exists()


# ---------------------------------------------------------------------------
# 3. BotWorker dry-run simulates cursor movement (moveTo) with ZERO clicks
# ---------------------------------------------------------------------------


def test_bot_worker_dry_run_moves_cursor_without_ever_clicking(tmp_path: Path) -> None:
    """In dry-run mode, BotWorker must move cursor across palette and pixels with 0 clicks."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 350, 200, 50))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={
            (25, 25): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
            (45, 25): {"preview_color": (255, 255, 255), "pixel_color": (0, 0, 0)},
        },
        color_position_map={
            (237, 28, 36): (120, 370),
            (255, 255, 255): (160, 370),
        },
    )

    class DryRunTrackingMouse:
        def __init__(self) -> None:
            self.pos = types.SimpleNamespace(x=0, y=0)
            self.moves: list[tuple[int, int]] = []

        def position(self) -> Any:
            return self.pos

        def moveTo(self, x: int, y: int) -> None:
            self.pos = types.SimpleNamespace(x=x, y=y)
            self.moves.append((x, y))

        def click(self, x: int, y: int) -> None:
            raise AssertionError(
                f"mouse.click({x}, {y}) must NEVER be called during dry-run!"
            )

    mouse = DryRunTrackingMouse()
    diff_path = tmp_path / "dry_run_worker.png"
    worker = BotWorker(dm, mouse_controller=mouse, palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    worker.start_bot(
        msg_queue,
        [
            {"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False},
            {"id": 64, "name": "White", "rgb": [255, 255, 255], "premium": False},
        ],
        {
            "pixel_limit": 100,
            "tolerance": 1,
            "delay": 0,
            "dry_run": True,
            "diff_output_path": str(diff_path),
        },
    )
    assert worker.thread is not None
    worker.thread.join(timeout=3.0)

    # Cursor moved to Red swatch (120, 370) -> Red pixel (125, 125) -> White swatch (160, 370) -> White pixel (145, 125)
    assert mouse.moves == [
        (120, 370),
        (125, 125),
        (160, 370),
        (145, 125),
    ]
    assert dm.painted_positions == set()

    messages: list[dict[str, Any]] = []
    while not msg_queue.empty():
        messages.append(msg_queue.get_nowait())

    progress_msgs = [m for m in messages if m["type"] == "progress"]
    assert len(progress_msgs) >= 2

    complete_msgs = [m for m in messages if m["type"] == "dry_run_complete"]
    assert len(complete_msgs) == 1
    msg = complete_msgs[0]
    assert msg["plan"].total_planned_pixels == 2
    assert msg["simulated_pixels"] == 2
    assert msg["stopped_by_user"] is False
    assert msg["cancelled_by_mouse"] is False
    assert isinstance(msg["diff_image_rgb"], np.ndarray)
    assert diff_path.exists()


def test_bot_worker_dry_run_stops_immediately_on_user_mouse_move(tmp_path: Path) -> None:
    """Moving the physical mouse during Dry-Run cancels cursor simulation and still emits diff report."""
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
            (30, 10): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},
        },
        color_position_map={(237, 28, 36): (120, 370)},
    )

    class UserInterruptingMouse:
        def __init__(self) -> None:
            self.pos = types.SimpleNamespace(x=0, y=0)
            self.moves: list[tuple[int, int]] = []

        def position(self) -> Any:
            return self.pos

        def moveTo(self, x: int, y: int) -> None:
            self.moves.append((x, y))
            if len(self.moves) == 2:
                # Simulate user yanking physical mouse away right after first pixel move
                self.pos = types.SimpleNamespace(x=x + 80, y=y + 80)
            else:
                self.pos = types.SimpleNamespace(x=x, y=y)

        def click(self, x: int, y: int) -> None:
            raise AssertionError("click must never happen in dry-run")

    mouse = UserInterruptingMouse()
    worker = BotWorker(dm, mouse_controller=mouse, palette_switch_delay=0.0)
    q: queue.Queue[dict[str, Any]] = queue.Queue()
    worker.start_bot(
        q,
        [{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        {
            "pixel_limit": 10,
            "tolerance": 1,
            "delay": 0,
            "dry_run": True,
            "diff_output_path": str(tmp_path / "cancel_diff.png"),
        },
    )
    assert worker.thread is not None
    worker.thread.join(timeout=3.0)

    # Stopped after swatch + 1st pixel move (did NOT visit 2nd or 3rd pixel!)
    assert len(mouse.moves) == 2
    msgs: list[dict[str, Any]] = []
    while not q.empty():
        msgs.append(q.get_nowait())
    done = [m for m in msgs if m["type"] == "dry_run_complete"]
    assert len(done) == 1
    assert done[0]["cancelled_by_mouse"] is True
    assert done[0]["simulated_pixels"] == 1


# ---------------------------------------------------------------------------
# 4. ControlTab, PreviewTab & auto-switching to Preview & Debug tab
# ---------------------------------------------------------------------------


def test_control_tab_and_preview_tab_dry_run_ui_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-Run completion displays in-memory diff image in PreviewTab and switches active tab."""
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
    monkeypatch.delitem(sys.modules, "gui.tabs.preview_tab", raising=False)
    monkeypatch.delitem(sys.modules, "app", raising=False)

    control_mod = importlib.import_module("gui.tabs.control_tab")
    preview_mod = importlib.import_module("gui.tabs.preview_tab")
    monkeypatch.setattr(
        preview_mod.ImageTk,
        "PhotoImage",
        lambda pil_img: types.SimpleNamespace(size=pil_img.size),
    )

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((100, 100, 200, 200))
    dm.set_palette_region((100, 350, 200, 50))
    dm.set_analysis_results(
        pixel_size=10,
        pixel_map={(25, 25): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)}},
        color_position_map={(237, 28, 36): (120, 370)},
    )

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
            self.image: Any = None
            self.lines: list[str] = []

        def config(self, **kwargs: Any) -> None:
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "text" in kwargs:
                self.text = kwargs["text"]
            if "image" in kwargs:
                self.image = kwargs["image"]

        def insert(self, _pos: Any, value: str) -> None:
            self.lines.append(value)

        def delete(self, *_args: Any) -> None:
            self.lines.clear()

        def see(self, _pos: Any) -> None:
            pass

        def focus_set(self) -> None:
            pass

    started_calls: list[dict[str, Any]] = []
    reanalyzed: list[bool] = []
    selected_tabs: list[Any] = []

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
    tab.reanalyze_var = DummyVar(True)
    tab.dry_run_var = DummyVar(False)
    tab.start_btn = DummyWidget()
    tab.dry_run_btn = DummyWidget()
    tab.stop_btn = DummyWidget()
    tab.status_label = DummyWidget()
    tab.progress_var = DummyVar(0.0)
    tab.log_text = DummyWidget()
    tab.message_queue = queue.Queue()
    tab.bot_worker = types.SimpleNamespace(
        start_bot=lambda q, colors, settings: started_calls.append(settings),
        stop_bot=lambda: None,
        thread=None,
    )

    preview_frame_sentinel = object()
    preview = preview_mod.PreviewTab.__new__(preview_mod.PreviewTab)
    preview.frame = preview_frame_sentinel
    preview.data_manager = dm
    preview.image_var = DummyVar("")
    preview.image_label = DummyWidget()
    preview.stats_text = DummyWidget()
    preview._last_dry_run_report = None
    preview._last_dry_run_image_path = None
    preview._last_dry_run_image_rgb = None

    main_win = types.SimpleNamespace(
        is_running=False,
        save_user_settings=lambda: None,
        root=types.SimpleNamespace(after_cancel=lambda _t: None),
        notebook=types.SimpleNamespace(select=lambda frame: selected_tabs.append(frame)),
        setup_tab=types.SimpleNamespace(
            _analyze_regions=lambda: reanalyzed.append(True),
            tolerance_var=DummyVar(2),
            delay_var=DummyVar(15),
        ),
        colors_tab=types.SimpleNamespace(color_vars={"Red": DummyVar(True)}),
        control_tab=tab,
        preview_tab=preview,
        get_enabled_colors=lambda: [
            {"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}
        ],
    )
    tab.main_window = main_win
    preview.main_window = main_win

    # Trigger Dry-Run with Reanalyze enabled -> runs analysis first, then starts dry-run
    tab._start_dry_run()
    assert reanalyzed == [True]
    assert tab._pending_dry_run_after_analysis is True

    tab.enable_start_button()
    assert len(started_calls) == 1
    assert started_calls[0]["dry_run"] is True

    plan = build_paint_plan(
        data_manager=dm,
        enabled_colors=[{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        pixel_limit=500,
        tolerance=2,
        click_delay_ms=15,
    )
    report = format_paint_plan_report(plan)
    diff_rgb = np.full((60, 80, 3), 120, dtype=np.uint8)

    # Verify PreviewTab renders directly from in-memory diff_image_rgb even without disk file!
    preview.show_dry_run_report(
        plan, report, diff_image_path=None, diff_image_rgb=diff_rgb
    )
    assert preview.image_var.get() == "Dry-Run Diff"
    assert preview.image_label.image is not None
    assert preview.image_label.text == ""
    assert "Red" in "".join(preview.stats_text.lines)

    # Verify PlaceBotGUI._handle_dry_run_complete switches notebook to PreviewTab!
    app_mod = importlib.import_module("app")
    gui_instance = app_mod.PlaceBotGUI.__new__(app_mod.PlaceBotGUI)
    gui_instance.control_tab = tab
    gui_instance.preview_tab = preview
    gui_instance.notebook = main_win.notebook
    gui_instance.status_label = DummyWidget()
    gui_instance._handle_dry_run_complete(
        {
            "type": "dry_run_complete",
            "plan": plan,
            "report": report,
            "diff_image_path": None,
            "diff_image_rgb": diff_rgb,
            "simulated_pixels": 1,
            "stopped_by_user": False,
            "cancelled_by_mouse": False,
        }
    )
    assert selected_tabs == [preview_frame_sentinel]
