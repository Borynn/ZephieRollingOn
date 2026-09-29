"""Unicode-safe image read/write.

On Windows, ``cv2.imread`` / ``cv2.imwrite`` pass the path to OpenCV as ANSI
text, so every call fails — silently returning ``None`` or ``False`` — as soon
as the path contains characters outside the system code page. A Chinese folder
name is enough, which is common for a portable build extracted by hand.

These wrappers route the bytes through Python file I/O instead, so the path is
handled by the Unicode API.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread_unicode(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """Read an image; returns ``None`` if the file is missing or undecodable."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), flags)


def imwrite_unicode(path: str | Path, image: np.ndarray) -> bool:
    """Write an image, creating parent directories as needed."""
    p = Path(path)
    ok, buf = cv2.imencode(p.suffix or ".png", image)
    if not ok:
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(buf.tobytes())
    except OSError:
        return False
    return True
