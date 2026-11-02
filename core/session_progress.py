"""Persistent session progress storage and validation for safe painting resume."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .exceptions import RegionError
from .screen_capture import validate_region

logger = logging.getLogger(__name__)

DEFAULT_PROGRESS_FILENAME: str = "session_progress.json"
PROGRESS_SCHEMA_VERSION: int = 1
CHECKPOINT_INTERVAL_PIXELS: int = 10


@dataclass(slots=True)
class SessionProgress:
    """Snapshot of analysis state and painted pixel coordinates for safe resume."""

    version: int
    canvas_region: tuple[int, int, int, int]
    palette_region: tuple[int, int, int, int]
    pixel_size: int
    canvas_scale: tuple[float, float]
    template_fingerprint: str
    painted_positions: set[tuple[int, int]]
    pixel_map: dict[tuple[int, int], dict[str, tuple[int, int, int]]]
    color_position_map: dict[tuple[int, int, int], tuple[int, int]]
    status: str = "in_progress"
    updated_at: str = ""


_UTC = timezone(timedelta(0))


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(_UTC).isoformat()


def compute_template_fingerprint(
    pixel_map: dict[tuple[int, int], dict[str, Any]],
) -> str:
    """Compute a deterministic SHA-256 fingerprint of template preview coordinates and colors."""
    digest = hashlib.sha256()
    for (x, y) in sorted(pixel_map.keys()):
        preview = pixel_map[(x, y)].get("preview_color", (0, 0, 0))
        entry_bytes = f"{x},{y}:{int(preview[0])},{int(preview[1])},{int(preview[2])};".encode(
            "ascii"
        )
        digest.update(entry_bytes)
    return digest.hexdigest()


def _serialize_progress(progress: SessionProgress) -> dict[str, Any]:
    """Convert :class:`SessionProgress` into a JSON-serializable dictionary."""
    serialized_pixel_map = [
        [
            int(pos[0]),
            int(pos[1]),
            [
                int(colors["preview_color"][0]),
                int(colors["preview_color"][1]),
                int(colors["preview_color"][2]),
            ],
            [
                int(colors["pixel_color"][0]),
                int(colors["pixel_color"][1]),
                int(colors["pixel_color"][2]),
            ],
        ]
        for pos, colors in sorted(progress.pixel_map.items())
    ]

    serialized_palette_map = [
        [
            [int(rgb[0]), int(rgb[1]), int(rgb[2])],
            [int(pt[0]), int(pt[1])],
        ]
        for rgb, pt in sorted(progress.color_position_map.items())
    ]

    serialized_painted = [
        [int(pos[0]), int(pos[1])] for pos in sorted(progress.painted_positions)
    ]

    return {
        "version": int(progress.version),
        "canvas_region": list(progress.canvas_region),
        "palette_region": list(progress.palette_region),
        "pixel_size": int(progress.pixel_size),
        "canvas_scale": [
            float(progress.canvas_scale[0]),
            float(progress.canvas_scale[1]),
        ],
        "template_fingerprint": progress.template_fingerprint,
        "painted_positions": serialized_painted,
        "pixel_map": serialized_pixel_map,
        "color_position_map": serialized_palette_map,
        "status": str(progress.status),
        "updated_at": progress.updated_at or utc_now_iso(),
    }


def _deserialize_progress(raw: Any) -> SessionProgress:
    """Validate and reconstruct :class:`SessionProgress` from parsed JSON data."""
    if not isinstance(raw, dict):
        raise TypeError("Progress root must be a JSON object.")

    version = raw.get("version")
    if version != PROGRESS_SCHEMA_VERSION:
        raise ValueError(f"Unsupported progress schema version: {version!r}.")

    canvas_region = validate_region(
        raw.get("canvas_region"),
        name="Saved canvas region",
        allow_negative_origin=True,
    )
    palette_region = validate_region(
        raw.get("palette_region"),
        name="Saved palette region",
        allow_negative_origin=True,
    )

    pixel_size = int(raw["pixel_size"])
    if pixel_size <= 0:
        raise ValueError(f"Invalid pixel_size: {pixel_size}.")

    scale_raw = raw.get("canvas_scale", [1.0, 1.0])
    if not isinstance(scale_raw, list) or len(scale_raw) != 2:
        raise ValueError("Invalid canvas_scale in progress file.")
    canvas_scale = (float(scale_raw[0]), float(scale_raw[1]))
    if canvas_scale[0] <= 0 or canvas_scale[1] <= 0:
        raise ValueError("Non-positive canvas_scale in progress file.")

    pixel_map: dict[tuple[int, int], dict[str, tuple[int, int, int]]] = {}
    for row in raw.get("pixel_map", []):
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError("Malformed pixel_map entry in progress file.")
        px, py, prev_c, pix_c = row
        if len(prev_c) != 3 or len(pix_c) != 3:
            raise ValueError("Malformed RGB/BGR triplet in pixel_map.")
        pixel_map[(int(px), int(py))] = {
            "preview_color": (int(prev_c[0]), int(prev_c[1]), int(prev_c[2])),
            "pixel_color": (int(pix_c[0]), int(pix_c[1]), int(pix_c[2])),
        }

    color_position_map: dict[tuple[int, int, int], tuple[int, int]] = {}
    for item in raw.get("color_position_map", []):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("Malformed color_position_map entry in progress file.")
        rgb_list, pt_list = item
        if len(rgb_list) != 3 or len(pt_list) != 2:
            raise ValueError("Malformed palette coordinate entry.")
        color_position_map[
            (int(rgb_list[0]), int(rgb_list[1]), int(rgb_list[2]))
        ] = (int(pt_list[0]), int(pt_list[1]))

    painted_positions: set[tuple[int, int]] = set()
    for pt in raw.get("painted_positions", []):
        if not isinstance(pt, list) or len(pt) != 2:
            raise ValueError("Malformed painted_positions entry.")
        painted_positions.add((int(pt[0]), int(pt[1])))

    fingerprint = str(raw.get("template_fingerprint", ""))
    if not fingerprint:
        fingerprint = compute_template_fingerprint(pixel_map)

    return SessionProgress(
        version=PROGRESS_SCHEMA_VERSION,
        canvas_region=canvas_region,
        palette_region=palette_region,
        pixel_size=pixel_size,
        canvas_scale=canvas_scale,
        template_fingerprint=fingerprint,
        painted_positions=painted_positions,
        pixel_map=pixel_map,
        color_position_map=color_position_map,
        status=str(raw.get("status", "in_progress")),
        updated_at=str(raw.get("updated_at", "")),
    )


def save_session_progress(file_path: str, progress: SessionProgress) -> bool:
    """Atomically write session progress to ``file_path`` via temp file and ``os.replace``."""
    target = Path(file_path)
    parent = target.parent if str(target.parent) else Path(".")
    tmp_path: str | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        payload = _serialize_progress(progress)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(parent),
            prefix=".session_progress_",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            json.dump(payload, tmp_file, indent=2)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        os.replace(tmp_path, str(target))
        return True
    except (OSError, TypeError, ValueError):
        logger.exception("Failed to save session progress to %s", file_path)
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def load_session_progress(file_path: str) -> SessionProgress | None:
    """Load and validate session progress from ``file_path``, or return ``None`` if invalid."""
    if not file_path or not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            raw_data = json.load(handle)
        return _deserialize_progress(raw_data)
    except (OSError, json.JSONDecodeError, RegionError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "Ignoring unreadable or incompatible session progress file %s: %s",
            file_path,
            exc,
        )
        return None


def clear_session_progress(file_path: str) -> None:
    """Delete ``file_path`` if it exists on disk."""
    if not file_path:
        return
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError:
        logger.warning("Could not remove session progress file %s", file_path, exc_info=True)
