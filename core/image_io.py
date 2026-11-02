"""Unicode-safe image writing helpers for Windows and POSIX file paths."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .config import resolve_writable_path


def save_rgb_image(filename: str, img_rgb: np.ndarray) -> str:
    """Save an RGB ``uint8`` NumPy image to ``filename`` using Unicode-safe file I/O.

    Unlike ``cv2.imwrite``, ``PIL.Image.save`` uses Python's native wide-character
    file APIs on Windows, avoiding silent failures when paths contain Cyrillic or
    other non-ASCII characters.
    """
    resolved = resolve_writable_path(filename)
    target = Path(resolved)
    if str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img_rgb).save(target, format="PNG")
    return str(target)


def save_bgr_image(filename: str, img_bgr: np.ndarray) -> str:
    """Convert a BGR ``uint8`` NumPy image to RGB and save it Unicode-safely as PNG."""
    if img_bgr.ndim == 3 and img_bgr.shape[2] == 3:
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    else:
        img_rgb = img_bgr
    return save_rgb_image(filename, img_rgb)
