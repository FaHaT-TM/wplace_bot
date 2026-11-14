# Place Bot — Project Tasks & Roadmap

This document tracks all **completed engineering milestones** and **planned improvements** for [FaHaT-TM/wplace_bot](https://github.com/FaHaT-TM/wplace_bot). It is mirrored in [`README.md`](README.md#project-roadmap--task-tracker).

---

## Completed Tasks

### Core Stability, Safety & Palette Accuracy (`v1.0.0` — PR #1 & PR #2)
- [x] **Structured Exception Hierarchy & Input Validation** — Added `PlaceBotError`, `CaptureError`, `PaletteDetectionError`, `PaintError`, `RegionError`, and `ConfigError` with clear GUI feedback and full stack-trace logging.
- [x] **Coordinate Bounds & Screen-Change Protection** — Enforced half-open region bounds checks before screen capture and mouse clicks; safely aborts if regions are `None`, malformed, or exceed current monitor geometry.
- [x] **HiDPI / Per-Monitor DPI Scaling (`100%`–`200%+`)** — Enabled Windows Per-Monitor DPI awareness (`SetProcessDpiAwareness`) and implemented physical-to-logical coordinate scaling (`compute_region_scale`, `image_offset_to_screen_point`).
- [x] **Extended Pixel Limit (`1–5,000`)** — Increased maximum `Pixel Limit` from `1000` to `5000` with synchronized slider and numeric input validation (`validate_pixel_limit`, `clamp_pixel_limit`).
- [x] **Complete 63-Color Palette & Premium `Bought` Enforcement** — Added paintable `White` (`id: 64`), clean `'Default'` color profile defaults, and two-layer filtering (`ColorsTab` + `BotWorker` defense-in-depth) so unbought premium swatches are never clicked.
- [x] **Instant Multi-Layered Emergency Stop & Thread-Safe Logging** — Replaced blocking `time.sleep` loops with interruptible `threading.Event.wait`, added `<Escape>` shortcut, physical mouse-movement cancellation, PyAutoGUI corner fail-safe handling, and `queue.Queue`-backed GUI log delivery.

### Multi-Monitor, Dry-Run, Session Resume & Packaging (`v1.0.0`–`v1.0.2` — PR #2 & PR #3)
- [x] **Multi-Monitor Virtual Desktop Support** — Added native display enumeration on Windows (`EnumDisplayMonitors`), macOS (`CoreGraphics`), and Linux (`xrandr`), negative coordinate support, cross-monitor region selection (`ImageGrab.grab(all_screens=True)`), and dead-zone / unplugged-monitor validation.
- [x] **Zero-Click Cursor-Tracing Dry-Run & Visual Diff Preview** — Implemented deterministic `PaintPlan` (`build_paint_plan`) with per-color pixel counts and `ETA`, live `0`-click cursor tracing (`moveTo`) across palette swatches and canvas targets, and automatic switch to **Preview & Debug** with a color-coded diff overlay (`debug/dry_run.png`).
- [x] **Persistent Painting Progress & Safe Session Resume** — Implemented atomic `session_progress.json` checkpoints with SHA-256 template fingerprinting, **`Resume Session`** (skipping redundant reanalysis), **`Reset Progress`**, and automatic invalidation when screen regions change.
- [x] **Unicode-Safe Image Saving & Dedicated `logs/` + `debug/` Directories** — Replaced `cv2.imwrite` with `PIL.Image` + `resolve_writable_path` (`core/image_io.py`), moved daily UTF-8 rotated logs into `logs/placebot_YYYYMMDD.log`, moved all debug/preview PNGs into `debug/` (`dry_run.png`, `palette.png`, `pixel_counter.png`, `size_estimation.png`), and added `Tools` menu shortcuts (`Open Logs Folder`, `Open Debug Folder`).
- [x] **Automatic `wplace.live` Pixel Limit Synchronization (`available / total`)** — Built dual-monitor on-screen pixel counter reader (`core/site_counter.py`) combining multi-threshold OpenCV + Pillow glyph/topology recognition with native Windows 10/11 `winocr` (`Windows.Media.Ocr`), auto-syncing at **app startup**, **before painting**, **on stop**, and **after painting**, plus manual **`Sync with Site`** button and **`Site Pixel Counter`** debug view. Added a dedicated **`Auto-sync site pixel counter`** switch (persisted in `user_settings.json`) that gates automatic syncs, and phase-specific region filtering: **startup** and **manual** syncs check only the first counter regions on each monitor, while **before painting**, **on stop**, and **after painting** check only the second (painting-mode) counter regions.
- [x] **Standalone Windows `app.exe` Build & Automated CI/CD Release** — Created reproducible PyInstaller specification (`app.spec`), release packager (`scripts/build_release.py`), `windows-latest` GitHub Actions workflow, and 107 automated `pytest` tests.

---

## Planned Tasks & Future Improvements

### Priority 1 — UX, Screen Flexibility & Control Ergonomics
- [ ] **Interactive `Select Counter` Region Picker** — Add an optional drag-to-select counter region button in the **Setup** tab (persisted per monitor profile alongside `Canvas` and `Palette`) so users on arbitrary resolutions (`1920x1080`, `2560x1440`, `4K`, Ultrawide) or non-maximized browser windows can calibrate counter sync without changing presets.
- [ ] **Optional Global Out-of-Focus Emergency Stop Hotkey** — Register a configurable global hotkey (e.g., `F8` or `Ctrl+Shift+S` via a clean background listener) so painting can be stopped with a single keystroke even when the browser window—not Place Bot—has focus.
- [ ] **Interactive Zoom & Pan in `Preview & Debug`** — Add mouse-wheel zoom, click-drag pan, and pixel coordinate tooltips on `Dry-Run Diff`, `Site Pixel Counter`, `Size Estimation`, and `Palette Detection` images for inspecting dense templates and small counter crops.
- [ ] **Compact Always-on-Top Mini-HUD Mode** — Add an optional collapsible mini-window (`Start`, `Dry Run`, `Stop`, progress bar, and live `Site Counter: X / Y`) so Place Bot never covers the browser canvas on single-monitor setups.

### Priority 2 — Smart Painting Strategies & Verification
- [ ] **Configurable Painting Order Strategies** — Allow choosing between `By Color Frequency` (fewer palette switches), `Rarest Colors First`, `Outline / Contours First`, `Top-to-Bottom`, and `Center-Out` inside `PaintPlan`.
- [ ] **Post-Run Canvas Verification & Missed-Pixel Report** — Optionally re-capture the `Canvas` region after painting finishes to verify that every clicked pixel registered on screen and allow queuing only missed cells for a quick follow-up pass.
- [ ] **Perceptual `CIELAB (ΔE)` Color Matching Mode** — Add optional perceptual `ΔE` distance matching alongside per-channel RGB tolerance to improve template/palette detection under Night Light, HDR, or custom monitor color profiles.
- [ ] **Passive Local Charge-Refill Estimator & Sound Notification** — Estimate passive charge recovery locally from the last synced `available / maximum` counter reading and play an optional gentle notification when charges reach a user-selected threshold (strictly passive notification; manual start only).
- [ ] **Live Per-Color Pixel Counters & Profile Import/Export in `Color Control`** — Display detected unpainted pixel counts next to each swatch in `Color Control` after analysis and support exporting/importing color profiles as standalone `.json` files.

### Priority 3 — Code Health, Static Typing & Platform Diagnostics
- [ ] **Refactor or Retire Legacy `main.py` CLI Prototype** — Modernize legacy `main.py` and `core/automation.py` to delegate to `AnalysisWorker` and `PaintPlan` (removing legacy `print()` calls and unused prototype code).
- [ ] **Full Strict `ruff` & `mypy` Gate Across Legacy GUI Modules** — Bring remaining legacy files (`gui/tabs/colors_tab.py`, `core/logger.py`, `gui/tabs/__init__.py`) to zero `ruff` warnings and strict `mypy` coverage, and enforce both linters in GitHub Actions CI.
- [ ] **Wayland & macOS Accessibility Self-Check Diagnostics** — Detect Wayland sessions on Linux or missing Screen Recording / Accessibility permissions on macOS at startup and display actionable setup guidance in the `Setup` tab.
