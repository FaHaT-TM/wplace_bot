# Place Bot — Pixel Art Automation Tool for Wplace.live

A desktop GUI automation tool for painting template-guided pixel art on [wplace.live](https://wplace.live) canvases. Place Bot captures user-selected screen regions, detects the active canvas grid and available palette swatches, previews the exact painting plan in **Dry-Run** mode, and automates mouse clicks with multi-monitor awareness, persistent session resume, and instant emergency stop controls.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Releases](https://img.shields.io/badge/Releases-Download%20ZIP-success.svg)](https://github.com/FaHaT-TM/wplace_bot/releases)

---

## Key Features

- **Complete 63-Color Wplace Palette** — Includes all active colors (including paintable `White`, `id: 64`), named color profiles, and strict enforcement of `bought` status for premium colors so unowned swatches are never clicked.
- **Extended Pixel Limit (`1–5,000`) & Automatic Site Counter Sync** — Reads the on-screen `available / total` pixel counter on `wplace.live` across dual monitors, with a dedicated **`Auto-sync site pixel counter`** switch (plus manual **`Sync with Site`** button, always active). App startup and manual sync check only the first counter regions; before painting, on stop, and after painting check only the second (painting-mode) counter regions, while preserving manual `Pixel Limit` edits between syncs.
- **Multi-Monitor & HiDPI Support** — Virtual-desktop interactive region selector supporting secondary monitors, negative screen offsets, per-monitor DPI scaling (`100%`–`200%+`), and 2D monitor-coverage validation that blocks clicks on dead zones or disconnected displays.
- **Dry-Run Preview Mode (`0` Mouse Clicks)** — Visually traces planned palette swatches and canvas pixel coordinates with the cursor (`moveTo` without clicking), updates the live progress bar, reports exact per-color pixel counts and `ETA`, and automatically opens the **Preview & Debug** tab with a visual diff overlay (`debug/dry_run.png`).
- **Persistent Painting Progress & Safe Resume** — Atomically saves painted coordinates and analysis state to `session_progress.json`. Resume directly after `Stop`, `<Escape>`, mouse cancellation, reaching `Pixel Limit`, or restarting the application via **`Resume Session`**, or clear state via **`Reset Progress`**. Automatically invalidates stale progress if screen regions change.
- **Multi-Layered Emergency Stop** — Halts immediately via the **`Stop`** button, **`ESC`** key, physical mouse movement detection, or moving the cursor to a screen corner (PyAutoGUI fail-safe).
- **Thread-Safe Modular Architecture** — Strict separation between Tkinter UI (`gui/`), background workers, and computer-vision/planning logic (`core/`).

---

## Quick Start

**Prerequisites on Canvas**: Open [wplace.live](https://wplace.live) with your template overlay visible on the canvas (for example, using Blue Marble with palette-converted colors) and keep the Wplace color palette visible on screen.

### Option 1: Portable Windows Release (Recommended)

1. Download **`wplace_bot.zip`** from the latest [GitHub Release](https://github.com/FaHaT-TM/wplace_bot/releases).
2. Extract the archive into any writable folder (keep `app.exe`, `colors.json`, and `config.json` together).
3. Launch **`app.exe`**.
4. Select your Canvas and Palette regions in the **Setup** tab, run **Dry Run (Preview)** to verify alignment, and click **Start Painting**.

### Option 2: Run from Source

**Requirements**: Python 3.10+ on Windows (primary target) or Linux/macOS desktop session.

1. Clone the repository:
   ```bash
   git clone https://github.com/FaHaT-TM/wplace_bot.git
   cd wplace_bot
   ```
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   # Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   # Linux / macOS:
   source .venv/bin/activate

   pip install -r requirements.txt
   ```
3. Launch the GUI application:
   ```bash
   python app.py
   ```

### Option 3: Build Standalone `app.exe` & Release ZIP Locally

On Windows with Python 3.10+ and dependencies installed:

```bash
python scripts/build_release.py --require-windows-exe
```

This compiles `dist/app.exe` via PyInstaller (`app.spec`) and packages `dist/wplace_bot.zip` containing `app.exe`, `colors.json`, `config.json`, documentation, and core source modules.

---

## Usage Guide

### 1. Setup Tab
- Verify detected displays in the **Display Topology** indicator.
- Click **"Select Canvas"** and drag across the target template area on your browser canvas.
- Click **"Select Palette"** and drag across the visible Wplace color swatches.
- Configure **Color Tolerance** (default `1`) and **Click Delay** (in milliseconds).
- Click **"Analyze Canvas & Palette"** to detect grid cell size and map palette coordinates.

### 2. Color Control Tab
- Create, switch, copy, rename, or delete **color profiles**.
- Enable or disable specific colors for the current run.
- Mark owned premium colors as **"Bought"** (unbought premium colors are automatically excluded from painting).

### 3. Bot Control Tab
- Set **Pixel Limit** (`1`–`5000`) via the numeric entry or slider.
- Click **"Dry Run (Preview)"** (or enable **Dry-run mode**) to inspect the exact paint plan and `ETA` with zero mouse clicks.
- Click **"Start Painting"** to begin live automation (optionally reanalyzing before starting).
- If interrupted, click **"Resume Session"** to continue from the exact unpainted remainder without reanalyzing, or **"Reset Progress"** to clear saved progress.
- Press **"Stop"**, hit **`ESC`**, or move the mouse to cancel immediately.

### 4. Preview & Debug Tab
- Inspect **Dry-Run Diff** (green borders = planned within pixel limit, amber borders = deferred beyond pixel limit, gray = unchanged), **Size Estimation**, and **Palette Detection** debug overlays.
- Review grid statistics and per-color Dry-Run breakdown.

---

## Configuration & Runtime Files

When running either `python app.py` or standalone `app.exe`, files are read from and saved next to the executable (or working directory):

| File | Tracked in Git | Purpose |
| :--- | :---: | :--- |
| `config.json` | Yes | Application-level defaults (`ui`, `bot.max_pixel_limit = 5000`, `logging`, `analysis`). |
| `colors.json` | Yes | Wplace 63-color palette definitions (`id`, `name`, `rgb`, `premium`). |
| `user_settings.json` | No (auto-created) | Active color profile, `bought` flags, saved regions, tolerance, click delay, and pixel limit. |
| `session_progress.json` | No (auto-created) | Atomic session checkpoint storing painted coordinates and analysis snapshot for safe resume. |
| `logs/placebot_YYYYMMDD.log` | No (auto-created) | UTF-8 daily diagnostic logs inside a dedicated `logs/` folder (auto-rotated to `max_log_files`). |
| `debug/*.png` | No (auto-created) | Debug & preview images (`dry_run.png`, `palette.png`, `pixel_counter.png`, `size_estimation.png`). |

---

## Project Architecture

```text
wplace_bot/
├── app.py                        # Main Tkinter GUI entry point (DPI-aware)
├── main.py                       # Legacy CLI workflow utility
├── app.spec                      # Reproducible PyInstaller specification for app.exe
├── colors.json                   # Wplace color palette definitions (63 active colors)
├── config.json                   # Application configuration defaults
├── TASKS.md                      # Completed milestones & planned project roadmap
├── requirements.txt              # Runtime & packaging dependencies
├── requirements-dev.txt          # Development & testing dependencies
├── core/                         # UI-independent domain logic & workers
│   ├── __init__.py               # PEP 562 lazy public exports
│   ├── analysis_worker.py        # Background canvas/palette analysis thread
│   ├── automation.py             # Low-level click helper
│   ├── bot_worker.py             # Live painting & zero-click Dry-Run worker
│   ├── color_detection.py        # Palette swatch detection & HiDPI coordinate mapping
│   ├── config.py                 # Config loader, path resolver, & pixel limit validator
│   ├── data_manager.py           # Profiles, preferences, & session progress state
│   ├── exceptions.py             # Custom exception hierarchy (PlaceBotError, RegionError, ...)
│   ├── image_analysis.py         # OpenCV grid estimation & pixel map builder
│   ├── logger.py                 # Centralized file/console/queue-safe GUI logger
│   ├── paint_planner.py          # Deterministic paint planner, ETA & Dry-Run diff renderer
│   ├── pixel_mapping.py          # Target-vs-current color matching
│   ├── screen_capture.py         # Multi-monitor detection, bounds validation & capture
│   ├── session_progress.py       # Atomic JSON session checkpointing & SHA-256 fingerprinting
│   └── site_counter.py           # Multi-monitor on-screen wplace.live pixel counter reader
├── gui/                          # Tkinter user interface layer
│   ├── region_selector.py        # Virtual-desktop drag-to-select overlay
│   └── tabs/                     # Modular notebook tabs (Setup, Colors, Control, Preview)
├── scripts/
│   └── build_release.py          # PyInstaller builder & wplace_bot.zip release packager
└── tests/                        # Automated pytest suite
```

### Running Tests & Static Checks

```bash
pip install -r requirements.txt -r requirements-dev.txt ruff mypy
pytest -v
ruff check core gui scripts app.py tests
mypy --python-version 3.10 --ignore-missing-imports core gui scripts app.py tests
```

---

## Project Roadmap & Task Tracker

The full milestone log and upcoming backlog are maintained in [`TASKS.md`](TASKS.md) and mirrored below.

### Completed Tasks (`v1.0.0` – `v1.0.2`)
- [x] **Structured Exception Hierarchy & Bounds Safety** — Custom `PlaceBotError` hierarchy, half-open coordinate validation, and safe handling of `None` or out-of-bounds regions.
- [x] **HiDPI & Multi-Monitor Virtual Desktop Support** — Per-Monitor DPI scaling (`100%`–`200%+`), Windows/macOS/Linux monitor enumeration, negative screen offsets, cross-monitor region selection, and dead-zone / unplugged-display blocking.
- [x] **Extended Pixel Limit (`1–5,000`) & 63-Color Palette** — Synchronized slider/entry validation up to `5000` pixels, paintable `White` (`id: 64`), clean `'Default'` profile, and two-layer `bought` enforcement for premium colors.
- [x] **Instant Multi-Layered Emergency Stop & Thread-Safe Logging** — Interruptible `threading.Event` waits, `Stop` button, `<Escape>` shortcut, mouse-movement cancellation, PyAutoGUI corner fail-safe, and `queue.Queue` GUI logging.
- [x] **Zero-Click Cursor-Tracing Dry-Run & Visual Diff Preview** — Deterministic `PaintPlan` with per-color pixel counts and `ETA`, `0`-click cursor tracing (`moveTo`), and automatic **Preview & Debug** diff overlay (`debug/dry_run.png`).
- [x] **Persistent Painting Progress & Safe Session Resume** — Atomic `session_progress.json` checkpoints with SHA-256 template fingerprinting, **`Resume Session`**, **`Reset Progress`**, and region-change invalidation.
- [x] **Dedicated `logs/` & `debug/` Runtime Directories** — Organized UTF-8 rotated logs (`logs/placebot_YYYYMMDD.log`) and debug images (`debug/dry_run.png`, `debug/palette.png`, `debug/pixel_counter.png`, `debug/size_estimation.png`) with `Tools` menu folder shortcuts, keeping the application root clean.
- [x] **Unicode-Safe PNG I/O & Portable Windows Release (`app.exe`)** — Cyrillic/non-ASCII path support via `PIL.Image`, PyInstaller `app.spec`, `scripts/build_release.py`, `windows-latest` CI/CD, and 107 automated `pytest` tests.
- [x] **Automatic `wplace.live` Pixel Counter Sync (`available / total`)** — Dual-monitor screen counter reader (`core/site_counter.py`, OpenCV/Pillow + Windows `winocr`) syncing on startup, before painting, on stop, and after painting, plus a dedicated **`Auto-sync site pixel counter`** switch and the always-active **`Sync with Site`** button. Each lifecycle phase queries exactly one counter mode: startup/manual check the first counter regions, while painting, stop, and after-painting phases check the second (painting-mode) regions.

### Planned Tasks & Improvements
- [ ] **Priority 1 — UX & Ergonomics**
  - Interactive **`Select Counter`** region picker in `Setup` for arbitrary monitor resolutions and window layouts.
  - Optional global out-of-focus Emergency Stop hotkey (e.g., `F8` / `Ctrl+Shift+S`) when the browser window is focused.
  - Interactive mouse-wheel zoom and click-drag pan in **Preview & Debug**.
  - Compact Always-on-Top **Mini-HUD** mode for single-monitor workflows.
- [ ] **Priority 2 — Smart Painting & Verification**
  - Configurable painting order strategies (`By Color Frequency`, `Rarest First`, `Outline First`, `Top-to-Bottom`, `Center-Out`).
  - Post-run canvas verification screenshot to highlight and retry any missed pixels.
  - Optional perceptual `CIELAB (ΔE)` color matching mode alongside RGB channel tolerance.
  - Passive local charge-refill timer and optional sound notification when charges reach a target threshold.
  - Per-color unpainted pixel badges and JSON profile import/export in **Color Control**.
- [ ] **Priority 3 — Code Health & CI Quality Gates**
  - Refactor or retire legacy `main.py` CLI prototype and `core/automation.py`.
  - Complete strict `ruff` and `mypy` coverage across remaining legacy GUI modules and enforce both in GitHub Actions CI.
  - Startup self-check diagnostics for Linux Wayland sessions and macOS Screen Recording / Accessibility permissions.

---

## Limitations & Responsibility

- **Personal & Educational Use Only**: Place Bot is provided strictly for personal experimentation and accessibility/automation study. You are solely responsible for how you use this software and for complying with the [wplace.live](https://wplace.live) Terms of Service and community rules.
- **No Protection Evasion**: This project intentionally operates purely through standard desktop screen capture and local OS mouse input. It does **not** bypass CAPTCHAs, spoof browser fingerprints, rotate accounts/proxies, or interact with private site APIs, and such features will not be added.
- **Respect Platform Limits & Delays**: Always use reasonable click delays and pixel limits that match your legitimate in-browser painting capacity. Lowering delays excessively may cause misclicks or violate platform rate rules.
- **No Credential Storage**: Place Bot never requests, collects, transmits, or stores account passwords, session cookies, or authentication tokens. All settings (`user_settings.json`, `session_progress.json`) remain strictly on your local machine.
- **Screen Geometry Dependency**: Saved session resume (`Resume Session`) assumes the browser window and canvas pan/zoom position have not moved since the last analysis. If you pan, zoom, or move the browser window, run a fresh analysis (`Start Painting` with *Reanalyze before starting* enabled).

---

## License

Released under the [MIT License](LICENSE). Original base concept by `ciguliaz`; maintained and extended in [FaHaT-TM/wplace_bot](https://github.com/FaHaT-TM/wplace_bot).
