"""Unit tests for P1 Dry-Run mode: paint planning, per-color counts, ETA, diff image, and zero mouse clicks."""

from __future__ import annotations

import importlib
import queue
import sys
import types
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from core.bot_worker import BotWorker
from core.data_manager import DataManager
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

    # Build synthetic pixel_map (BGR):
    # - 4 unpainted Red pixels [36, 28, 237]
    # - 1 already-painted Red pixel (preview == pixel == [36, 28, 237]) -> ignored
    # - 4 unpainted White pixels [255, 255, 255]
    # - 1 pixel at (90, 90) with BGR (250, 250, 250) matching BOTH White [255,255,255]
    #   and Light Gray-ish [245,245,245] at tolerance=6 -> must only be claimed ONCE by White!
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
        (90, 90): {"preview_color": (250, 250, 250), "pixel_color": (0, 0, 0)},
    }
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
        {"id": 62, "name": "Stone", "rgb": [148, 140, 107], "premium": True},  # unbought!
    ]

    # Set pixel_limit=6, tolerance=6, click_delay_ms=50, palette_switch_delay_s=0.2:
    # - Red: matched=4, planned=4, deferred=0
    # - White: matched=5 (includes (90,90)), planned=2 (capped at limit 6!), deferred=3
    # - Near White: (90,90) was already matched/claimed in unpainted set -> matched=0!
    # - Stone: unbought premium -> listed in skipped_unbought_colors
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
    assert (red_entry.matched_count, red_entry.planned_count, red_entry.deferred_by_limit_count) == (
        4,
        4,
        0,
    )
    assert (
        white_entry.matched_count,
        white_entry.planned_count,
        white_entry.deferred_by_limit_count,
    ) == (5, 2, 3)

    # Exact ETA check:
    # Red: 0.2s swatch switch + 4 * 0.05s delays = 0.40s
    # White: 0.2s swatch switch + (2 - 1) * 0.05s delays (stops immediately on 6th pixel!) = 0.25s
    # Total = 0.65s
    assert plan.estimated_seconds == pytest.approx(0.65)

    report = format_paint_plan_report(plan)
    assert "DRY-RUN" in report.upper()
    assert "Red" in report
    assert "White" in report
    assert "Stone" in report
    assert "0.7s" in report or "0.6s" in report


# ---------------------------------------------------------------------------
# 2. Visual Diff Preview image rendering
# ---------------------------------------------------------------------------


def test_render_dry_run_diff_image_with_and_without_canvas_screenshot(
    tmp_path: Path,
) -> None:
    """render_dry_run_diff_image must output a valid annotated PNG with planned & deferred pixels."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "settings.json"),
    )
    dm.set_canvas_region((0, 0, 120, 80))
    dm.set_palette_region((0, 100, 120, 40))

    canvas_rgb = np.full((80, 120, 3), 40, dtype=np.uint8)
    pixel_map = {
        (20, 20): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},   # Planned
        (50, 20): {"preview_color": (36, 28, 237), "pixel_color": (0, 0, 0)},   # Deferred (limit=1)
        (80, 20): {"preview_color": (36, 28, 237), "pixel_color": (36, 28, 237)},  # Already painted
    }
    dm.set_analysis_results(
        pixel_size=12,
        pixel_map=pixel_map,
        color_position_map={(237, 28, 36): (20, 120)},
        canvas_image_rgb=canvas_rgb,
    )

    plan = build_paint_plan(
        data_manager=dm,
        enabled_colors=[{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        pixel_limit=1,
        tolerance=2,
        click_delay_ms=15,
    )

    out_with_bg = tmp_path / "diff_with_bg.png"
    saved_path = render_dry_run_diff_image(
        plan=plan,
        pixel_map=pixel_map,
        pixel_size=12,
        canvas_image_rgb=canvas_rgb,
        output_path=str(out_with_bg),
    )
    assert Path(saved_path).exists()
    loaded_bgr = cv2.imread(saved_path)
    assert loaded_bgr is not None
    assert loaded_bgr.shape == (80, 120, 3)
    # Planned center (20, 20) should have Red fill (BGR: 36, 28, 237)
    assert tuple(int(c) for c in loaded_bgr[20, 20]) == (36, 28, 237)

    # Also test fallback rendering when canvas_image_rgb is None
    out_synthetic = tmp_path / "diff_synthetic.png"
    saved_synth = render_dry_run_diff_image(
        plan=plan,
        pixel_map=pixel_map,
        pixel_size=12,
        canvas_image_rgb=None,
        output_path=str(out_synthetic),
    )
    assert Path(saved_synth).exists()
    loaded_synth = cv2.imread(saved_synth)
    assert loaded_synth is not None
    assert loaded_synth.shape[0] >= 30 and loaded_synth.shape[1] >= 90


# ---------------------------------------------------------------------------
# 3. BotWorker dry-run mode guarantees ZERO mouse calls
# ---------------------------------------------------------------------------


def test_bot_worker_dry_run_never_touches_mouse_controller(tmp_path: Path) -> None:
    """In dry-run mode, BotWorker must NEVER call mouse.position() or mouse.click()."""
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

    class ForbiddenMouse:
        def position(self) -> Any:
            raise AssertionError("mouse.position() must NOT be called during dry-run!")

        def click(self, x: int, y: int) -> None:
            raise AssertionError(
                f"mouse.click({x}, {y}) must NOT be called during dry-run!"
            )

    diff_path = tmp_path / "dry_run_worker.png"
    worker = BotWorker(dm, mouse_controller=ForbiddenMouse(), palette_switch_delay=0.2)
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
            "delay": 20,
            "dry_run": True,
            "diff_output_path": str(diff_path),
        },
    )
    assert worker.thread is not None
    worker.thread.join(timeout=3.0)

    msg = msg_queue.get_nowait()
    assert msg["type"] == "dry_run_complete"
    assert msg["plan"].total_planned_pixels == 2
    assert "Red" in msg["report"]
    assert "White" in msg["report"]
    assert diff_path.exists()


# ---------------------------------------------------------------------------
# 4. ControlTab & PreviewTab headless Dry-Run workflow
# ---------------------------------------------------------------------------


def test_control_tab_and_preview_tab_dry_run_ui_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ControlTab._start_dry_run (with reanalysis) and PreviewTab.show_dry_run_report work cleanly."""
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

    control_mod = importlib.import_module("gui.tabs.control_tab")
    preview_mod = importlib.import_module("gui.tabs.preview_tab")

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
            self.lines: list[str] = []

        def config(self, **kwargs: Any) -> None:
            if "state" in kwargs:
                self.state = kwargs["state"]
            if "text" in kwargs:
                self.text = kwargs["text"]

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
    tab.main_window = types.SimpleNamespace(
        is_running=False,
        save_user_settings=lambda: None,
        root=types.SimpleNamespace(after_cancel=lambda _t: None),
        setup_tab=types.SimpleNamespace(
            _analyze_regions=lambda: reanalyzed.append(True),
            tolerance_var=DummyVar(2),
            delay_var=DummyVar(15),
        ),
        colors_tab=types.SimpleNamespace(color_vars={"Red": DummyVar(True)}),
        control_tab=tab,
        get_enabled_colors=lambda: [
            {"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}
        ],
    )

    # Trigger Dry-Run with Reanalyze enabled -> runs analysis first, then starts dry-run
    tab._start_dry_run()
    assert reanalyzed == [True]
    assert tab._pending_dry_run_after_analysis is True
    assert started_calls == []

    # Simulate analysis_complete arriving -> dispatches BotWorker with dry_run=True!
    tab.enable_start_button()
    assert len(started_calls) == 1
    assert started_calls[0]["dry_run"] is True

    # Build plan & report and verify both ControlTab and PreviewTab handlers
    plan = build_paint_plan(
        data_manager=dm,
        enabled_colors=[{"id": 7, "name": "Red", "rgb": [237, 28, 36], "premium": False}],
        pixel_limit=500,
        tolerance=2,
        click_delay_ms=15,
    )
    report = format_paint_plan_report(plan)

    tab.on_dry_run_complete(plan, report)
    assert tab.main_window.is_running is False
    assert tab.stop_btn.state == "disabled"
    assert "0 clicks" in tab.status_label.text.lower() or "dry-run" in tab.status_label.text.lower()
    assert any("Red" in line for line in tab.log_text.lines)

    # Verify PreviewTab updates image selection and stats text
    preview = preview_mod.PreviewTab.__new__(preview_mod.PreviewTab)
    preview.data_manager = dm
    preview.main_window = tab.main_window
    preview.image_var = DummyVar("")
    preview.image_label = DummyWidget()
    preview.stats_text = DummyWidget()
    preview._last_dry_run_report = None
    preview._last_dry_run_image_path = None

    preview.show_dry_run_report(plan, report, diff_image_path=None)
    assert preview.image_var.get() == "Dry-Run Diff"
    joined_stats = "".join(preview.stats_text.lines)
    assert "Red" in joined_stats
    assert "DRY-RUN" in joined_stats.upper()
