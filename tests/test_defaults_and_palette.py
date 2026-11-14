"""Unit tests for default settings, White (id=64) palette entry, and white pixel painting."""

from __future__ import annotations

import json
import queue
import types
from pathlib import Path

from core.config import Config
from core.data_manager import DataManager
from core.pixel_mapping import find_pixels_to_paint_from_map

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLORS_JSON_PATH = PROJECT_ROOT / "colors.json"
CONFIG_JSON_PATH = PROJECT_ROOT / "config.json"


def test_colors_json_includes_paintable_white_id_64() -> None:
    """colors.json must define id=64 White (non-ignored, free) alongside ignored id=0 and id=5."""
    raw = json.loads(COLORS_JSON_PATH.read_text(encoding="utf-8"))
    by_id = {entry["id"]: entry for entry in raw["color_palette"]}

    assert by_id[0]["name"] == "Transparent"
    assert by_id[0].get("ignore") is True

    assert by_id[5]["name"] == "White"
    assert by_id[5].get("ignore") is True

    assert 64 in by_id
    assert by_id[64] == {
        "id": 64,
        "premium": False,
        "name": "White",
        "rgb": [255, 255, 255],
    }


def test_data_manager_loads_63_active_colors_including_white(tmp_path: Path) -> None:
    """DataManager must filter ignored entries (0, 5) and keep 63 colors including id=64 White."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "missing_user_settings.json"),
    )

    assert len(dm.color_palette) == 63
    ids = {c["id"] for c in dm.color_palette}
    assert 0 not in ids
    assert 5 not in ids
    assert 64 in ids

    white_color = next(c for c in dm.color_palette if c["id"] == 64)
    assert white_color["name"] == "White"
    assert white_color["rgb"] == [255, 255, 255]
    assert white_color["premium"] is False


def test_data_manager_clean_default_profile_and_preferences(tmp_path: Path) -> None:
    """Fresh DataManager must initialize Default profile (all 63 enabled, premium unbought) and new preferences."""
    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(tmp_path / "missing_user_settings.json"),
    )

    # Profile checks (clean 'Default' based on user's '123' template)
    assert dm.get_active_profile() == "Default"
    assert dm.get_profile_names() == ["Default"]

    default_colors = dm.user_settings["color_profiles"]["Default"]["colors"]
    assert len(default_colors) == 63

    for color in dm.color_palette:
        cid = str(color["id"])
        assert cid in default_colors
        if color.get("premium", False):
            assert default_colors[cid] == {"enabled": True, "bought": False}
            assert dm.get_color_setting(cid, "enabled") is True
            assert dm.get_color_setting(cid, "bought") is False
        else:
            assert default_colors[cid] == {"enabled": True}
            assert dm.get_color_setting(cid, "enabled") is True

    assert len(dm.get_enabled_colors()) == 63

    # Preferences checks
    prefs = dm.user_settings["preferences"]
    assert prefs["color_tolerance"] == 1
    assert prefs["click_delay"] == 15
    assert prefs["pixel_limit"] == 1000
    assert prefs["auto_save_regions"] is True
    assert prefs["reanalyze_before_start"] is True
    assert prefs["font_scale"] == 1.25
    assert prefs["auto_sync_site_limit"] is True

    # Regions must NOT be pre-populated by default
    assert "last_canvas_region" not in prefs
    assert "last_palette_region" not in prefs
    assert dm.canvas_region is None
    assert dm.palette_region is None
    assert dm.has_regions() is False


def test_config_json_retains_max_5000_and_config_defaults() -> None:
    """config.json must keep max_pixel_limit=5000 and its own bot defaults unchanged."""
    cfg = Config(config_file=str(CONFIG_JSON_PATH))
    assert cfg.get("bot.max_pixel_limit") == 5000
    assert cfg.get("bot.default_pixel_limit") == 50
    assert cfg.get("bot.default_tolerance") == 5
    assert cfg.get("bot.default_delay") == 50


def test_data_manager_persists_and_backfills_missing_preferences(tmp_path: Path) -> None:
    """Existing partial user_settings.json keeps user values and receives missing preference defaults."""
    settings_path = tmp_path / "user_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "color_profiles": {"Default": {"colors": {"1": {"enabled": False}}}},
                "active_color_profile": "Default",
                "preferences": {"color_tolerance": 7},
            }
        ),
        encoding="utf-8",
    )

    dm = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(settings_path),
    )
    assert dm.get_preference("color_tolerance") == 7
    assert dm.get_preference("click_delay") == 15
    assert dm.get_preference("pixel_limit") == 1000
    assert dm.get_preference("font_scale") == 1.25
    assert dm.get_preference("auto_sync_site_limit") is True
    assert dm.get_color_setting("1", "enabled") is False

    dm.set_canvas_region((10, 20, 300, 400))
    reloaded = DataManager(
        colors_file=str(COLORS_JSON_PATH),
        settings_file=str(settings_path),
    )
    assert reloaded.canvas_region == [10, 20, 300, 400]


def test_white_pixels_can_be_mapped_and_painted() -> None:
    """BotWorker and pixel mapping must paint White (id=64, [255, 255, 255]) over non-white pixels."""
    from core.bot_worker import BotWorker

    white_bgr = (255, 255, 255)
    black_bgr = (0, 0, 0)
    pixel_map = {
        (10, 10): {"preview_color": white_bgr, "pixel_color": black_bgr},  # Needs white paint
        (20, 10): {"preview_color": white_bgr, "pixel_color": white_bgr},  # Already white -> skip
    }

    spots = find_pixels_to_paint_from_map(pixel_map, white_bgr, tolerance=1)
    assert spots == [(10, 10)]

    clicks: list[tuple[int, int]] = []
    pos = types.SimpleNamespace(x=0, y=0)

    class StubMouse:
        def position(self) -> types.SimpleNamespace:
            return pos

        def click(self, x: int, y: int) -> None:
            clicks.append((x, y))
            pos.x, pos.y = x, y

    dm = types.SimpleNamespace(
        canvas_region=(100, 200, 500, 500),
        palette_region=(100, 800, 400, 60),
        color_position_map={(255, 255, 255): (350, 820)},
        pixel_map=pixel_map,
    )
    worker = BotWorker(dm, mouse_controller=StubMouse(), palette_switch_delay=0.0)
    msg_queue: queue.Queue[dict[str, object]] = queue.Queue()
    white_entry = {"id": 64, "premium": False, "name": "White", "rgb": [255, 255, 255]}

    worker.start_bot(
        msg_queue,  # type: ignore[arg-type]
        [white_entry],
        {"pixel_limit": 1000, "tolerance": 1, "delay": 0},
    )
    assert worker.thread is not None
    worker.thread.join(timeout=2.0)

    # 1 click on White swatch (350, 820) + 1 click on canvas (100+10, 200+10) = (110, 210)
    assert clicks == [(350, 820), (110, 210)]
