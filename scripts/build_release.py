"""Build standalone app.exe via PyInstaller and assemble the wplace_bot.zip release archive."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_ROOT_DIR: str = "wplace_bot"
DEFAULT_SPEC_FILE: str = "app.spec"
DEFAULT_ZIP_NAME: str = "wplace_bot.zip"

ROOT_RELEASE_FILES: tuple[str, ...] = (
    "colors.json",
    "config.json",
    "README.md",
    "TASKS.md",
    "LICENSE",
    "requirements.txt",
    "app.spec",
    "app.py",
    "main.py",
)

SOURCE_PACKAGE_DIRS: tuple[str, ...] = (
    "core",
    "gui",
)


def collect_release_files(project_root: Path) -> list[tuple[Path, str]]:
    """Collect core project files and configs as ``(source_path, archive_rel_path)`` pairs.

    Args:
        project_root: Repository root directory.

    Returns:
        Sorted list of ``(absolute_source_path, zip_member_path)`` entries.
    """
    entries: list[tuple[Path, str]] = []

    for rel_name in ROOT_RELEASE_FILES:
        src = project_root / rel_name
        if not src.is_file():
            raise FileNotFoundError(f"Required release file missing: {src}")
        entries.append((src, f"{ARCHIVE_ROOT_DIR}/{rel_name}"))

    for pkg_dir in SOURCE_PACKAGE_DIRS:
        pkg_root = project_root / pkg_dir
        if not pkg_root.is_dir():
            raise FileNotFoundError(f"Required package directory missing: {pkg_root}")
        for py_file in sorted(pkg_root.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            rel_posix = py_file.relative_to(project_root).as_posix()
            entries.append((py_file, f"{ARCHIVE_ROOT_DIR}/{rel_posix}"))

    return entries


def build_executable(
    project_root: Path,
    dist_dir: Path,
    work_dir: Path,
    spec_file: str = DEFAULT_SPEC_FILE,
) -> Path:
    """Compile the standalone GUI executable using PyInstaller and ``app.spec``.

    Args:
        project_root: Repository root containing ``app.spec``.
        dist_dir: Output directory for built artifacts.
        work_dir: Temporary PyInstaller build directory.
        spec_file: Spec filename relative to ``project_root``.

    Returns:
        Path to the compiled executable (``app.exe`` on Windows, ``app`` on POSIX).

    Raises:
        RuntimeError: If PyInstaller fails or does not produce the expected binary.
    """
    spec_path = project_root / spec_file
    if not spec_path.is_file():
        raise FileNotFoundError(f"PyInstaller spec file not found: {spec_path}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        str(spec_path),
    ]
    result = subprocess.run(cmd, cwd=str(project_root), check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"PyInstaller failed with exit code {result.returncode}."
        )

    exe_name = "app.exe" if sys.platform == "win32" else "app"
    built_exe = dist_dir / exe_name
    if not built_exe.is_file():
        raise RuntimeError(f"Expected built executable not found: {built_exe}")

    return built_exe


def create_release_zip(
    project_root: Path,
    output_zip: Path,
    exe_path: Path | None = None,
) -> Path:
    """Create ``wplace_bot.zip`` containing ``app.exe``, configs, docs, and core Python sources.

    Args:
        project_root: Repository root directory.
        output_zip: Destination path for the ``.zip`` archive.
        exe_path: Optional compiled ``app.exe`` binary to place at ``wplace_bot/app.exe``.

    Returns:
        Path to the created ``.zip`` archive.
    """
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    files_to_pack = collect_release_files(project_root)

    with zipfile.ZipFile(
        output_zip, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        if exe_path is not None:
            if not exe_path.is_file():
                raise FileNotFoundError(f"Executable file not found: {exe_path}")
            archive.write(exe_path, arcname=f"{ARCHIVE_ROOT_DIR}/app.exe")

        for src_path, arc_name in files_to_pack:
            archive.write(src_path, arcname=arc_name)

    return output_zip


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for building ``app.exe`` and ``wplace_bot.zip``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Build Place Bot executable and release ZIP archive."
    )
    parser.add_argument(
        "--dist-dir",
        default="dist",
        help="Output directory for executable and ZIP archive (default: dist).",
    )
    parser.add_argument(
        "--work-dir",
        default="build",
        help="Temporary PyInstaller work directory (default: build).",
    )
    parser.add_argument(
        "--zip-name",
        default=DEFAULT_ZIP_NAME,
        help=f"Output ZIP archive filename (default: {DEFAULT_ZIP_NAME}).",
    )
    parser.add_argument(
        "--skip-exe",
        action="store_true",
        help="Package configs and core source files without invoking PyInstaller.",
    )
    parser.add_argument(
        "--require-windows-exe",
        action="store_true",
        help="Fail unless running on Windows and bundling native app.exe.",
    )
    args = parser.parse_args(argv)

    if args.require_windows_exe and sys.platform != "win32":
        logger.error("--require-windows-exe requires a native Windows Python host.")
        return 2

    project_root = Path(__file__).resolve().parent.parent
    dist_dir = (project_root / args.dist_dir).resolve()
    work_dir = (project_root / args.work_dir).resolve()

    exe_for_zip: Path | None = None
    if not args.skip_exe:
        built_binary = build_executable(
            project_root=project_root,
            dist_dir=dist_dir,
            work_dir=work_dir,
        )
        logger.info("Built executable: %s", built_binary)
        if sys.platform == "win32":
            exe_for_zip = built_binary
        else:
            logger.warning(
                "Host platform is %s (not Windows); skipping non-Windows binary in app.exe slot.",
                sys.platform,
            )

    zip_path = dist_dir / args.zip_name
    create_release_zip(
        project_root=project_root,
        output_zip=zip_path,
        exe_path=exe_for_zip,
    )
    logger.info("Created release archive: %s", zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
