"""Tests for frozen executable path resolution, PyInstaller spec completeness, and release zip."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest


def test_frozen_executable_path_resolution_and_meipass_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frozen app.exe resolves writable files next to exe and falls back to _MEIPASS resources."""
    from core.config import (
        Config,
        get_app_dir,
        resolve_resource_path,
        resolve_writable_path,
    )
    from core.data_manager import DataManager

    exe_dir = tmp_path / "portable_dir"
    exe_dir.mkdir()
    fake_exe = exe_dir / "app.exe"
    fake_exe.write_bytes(b"MZ-fake-pe")

    meipass_dir = tmp_path / "_MEI12345"
    meipass_dir.mkdir()

    # Copy valid colors.json and config.json into _MEIPASS only first
    repo_colors = Path("colors.json").read_text(encoding="utf-8")
    (meipass_dir / "colors.json").write_text(repo_colors, encoding="utf-8")
    (meipass_dir / "config.json").write_text(
        json.dumps({"bot": {"max_pixel_limit": 4321}}), encoding="utf-8"
    )

    # Simulate running from a completely unrelated working directory (e.g. C:\Windows\System32)
    other_cwd = tmp_path / "system32_cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass_dir), raising=False)

    assert get_app_dir() == exe_dir.resolve()
    assert Path(resolve_resource_path("colors.json")) == meipass_dir / "colors.json"
    assert Path(resolve_writable_path("user_settings.json")) == (
        exe_dir.resolve() / "user_settings.json"
    )

    # Config loads fallback from _MEIPASS, but saves next to app.exe
    cfg = Config()
    assert cfg.get("bot.max_pixel_limit") == 4321
    cfg.save()
    assert (exe_dir / "config.json").exists()

    # External config.json next to app.exe now takes precedence over _MEIPASS
    (exe_dir / "config.json").write_text(
        json.dumps({"bot": {"max_pixel_limit": 5000}}), encoding="utf-8"
    )
    assert Path(resolve_resource_path("config.json")) == exe_dir.resolve() / "config.json"
    cfg_reloaded = Config()
    assert cfg_reloaded.get("bot.max_pixel_limit") == 5000

    # DataManager loads colors from _MEIPASS fallback and writes settings/progress next to app.exe
    dm = DataManager()
    assert len(dm.color_palette) == 63
    dm.save_user_settings()
    assert (exe_dir / "user_settings.json").exists()
    assert Path(dm.progress_file) == exe_dir.resolve() / "session_progress.json"


def test_pyinstaller_spec_covers_all_lazy_core_and_gui_modules() -> None:
    """app.spec must include every lazy-imported core submodule so frozen app.exe never misses one."""
    import core

    spec_path = Path("app.spec")
    assert spec_path.exists(), "app.spec must exist in repository root"
    spec_text = spec_path.read_text(encoding="utf-8")

    lazy_modules = {
        f"core{mod_rel}" for mod_rel, _attr in core._LAZY_EXPORTS.values()
    }
    for mod_name in sorted(lazy_modules):
        assert mod_name in spec_text, f"Missing hidden import {mod_name!r} in app.spec"

    for gui_mod in (
        "gui.region_selector",
        "gui.tabs.setup_tab",
        "gui.tabs.colors_tab",
        "gui.tabs.control_tab",
        "gui.tabs.preview_tab",
    ):
        assert gui_mod in spec_text, f"Missing GUI module {gui_mod!r} in app.spec"


def test_build_release_creates_clean_zip_with_configs_sources_and_exe(
    tmp_path: Path,
) -> None:
    """create_release_zip packages app.exe, configs, docs, and core/gui sources cleanly."""
    from scripts.build_release import create_release_zip

    repo_root = Path.cwd()
    fake_exe = tmp_path / "app.exe"
    fake_exe.write_bytes(b"MZ-windows-executable-payload")
    out_zip = tmp_path / "wplace_bot.zip"

    created = create_release_zip(
        project_root=repo_root,
        output_zip=out_zip,
        exe_path=fake_exe,
    )
    assert created == out_zip
    assert out_zip.exists()

    with zipfile.ZipFile(out_zip, "r") as zf:
        names = set(zf.namelist())
        expected_entries = {
            "wplace_bot/app.exe",
            "wplace_bot/colors.json",
            "wplace_bot/config.json",
            "wplace_bot/README.md",
            "wplace_bot/LICENSE",
            "wplace_bot/requirements.txt",
            "wplace_bot/app.py",
            "wplace_bot/main.py",
            "wplace_bot/core/__init__.py",
            "wplace_bot/core/bot_worker.py",
            "wplace_bot/core/data_manager.py",
            "wplace_bot/core/paint_planner.py",
            "wplace_bot/core/screen_capture.py",
            "wplace_bot/core/session_progress.py",
            "wplace_bot/gui/region_selector.py",
            "wplace_bot/gui/tabs/control_tab.py",
        }
        assert expected_entries.issubset(names)

        # Ensure runtime/private/dev artifacts are never leaked into release zip
        for forbidden_substr in (
            "user_settings.json",
            "session_progress.json",
            "__pycache__",
            ".venv",
            ".git/",
            "debug_",
            ".log",
        ):
            assert not any(forbidden_substr in entry for entry in names), (
                f"Forbidden entry matching {forbidden_substr!r} found in zip"
            )

        assert zf.read("wplace_bot/app.exe") == b"MZ-windows-executable-payload"


def test_readme_tailored_to_fahat_tm_project() -> None:
    """README.md points to FaHaT-TM/wplace_bot and documents modern features and limitations."""
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "https://github.com/FaHaT-TM/wplace_bot" in readme
    assert "https://github.com/FaHaT-TM/wplace_bot/releases" in readme
    assert "ciguliaz/wplace_bot" not in readme
    assert "ko-fi.com/ciguliaz" not in readme
    assert "Python 3.10+" in readme
    assert "5000" in readme or "5,000" in readme
    assert "Dry-Run" in readme
    assert "Resume Session" in readme
    assert "Limitations & Responsibility" in readme
