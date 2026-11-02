# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for building standalone Place Bot executable (app.exe)."""

block_cipher = None

HIDDEN_IMPORTS = [
    "core",
    "core.analysis_worker",
    "core.automation",
    "core.bot_worker",
    "core.color_detection",
    "core.config",
    "core.data_manager",
    "core.exceptions",
    "core.image_analysis",
    "core.image_io",
    "core.logger",
    "core.paint_planner",
    "core.pixel_mapping",
    "core.screen_capture",
    "core.session_progress",
    "gui",
    "gui.region_selector",
    "gui.tabs",
    "gui.tabs.colors_tab",
    "gui.tabs.control_tab",
    "gui.tabs.preview_tab",
    "gui.tabs.setup_tab",
    "PIL._tkinter_finder",
]

BUNDLED_DATAS = [
    ("colors.json", "."),
    ("config.json", "."),
]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=BUNDLED_DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
