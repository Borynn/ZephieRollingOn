"""窗口几何记忆：主界面 / 快捷键 / 详细卡池 位置与尺寸。"""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

import re
from pathlib import Path

import yaml

_PROJECT_ROOT = project_root()
GEOMETRY_PATH = _PROJECT_ROOT / "config" / "ui_geometry.yaml"

_GEO_RE = re.compile(
    r"^(?P<w>\d+)x(?P<h>\d+)(?:\+(?P<x>-?\d+)\+(?P<y>-?\d+))?$"
)


def _load_all() -> dict:
    if not GEOMETRY_PATH.is_file():
        return {}
    try:
        with GEOMETRY_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _save_all(data: dict) -> None:
    try:
        GEOMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with GEOMETRY_PATH.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)
    except OSError:
        pass


def load_geometry(key: str) -> str | None:
    raw = _load_all().get(key)
    if not isinstance(raw, str) or not _GEO_RE.match(raw.strip()):
        return None
    return raw.strip()


def save_geometry(key: str, geometry: str) -> None:
    geo = (geometry or "").strip()
    if not _GEO_RE.match(geo):
        return
    data = _load_all()
    if data.get(key) == geo:
        return
    data[key] = geo
    _save_all(data)


def apply_saved_geometry(
    win,
    key: str,
    *,
    default: str,
) -> None:
    """应用已保存几何；无效则回退 default。尽量保证窗口落在可见屏内。"""
    geo = load_geometry(key) or default
    try:
        win.geometry(geo)
        win.update_idletasks()
    except Exception:
        try:
            win.geometry(default)
        except Exception:
            return
        return

    try:
        import tkinter as tk

        x = int(win.winfo_x())
        y = int(win.winfo_y())
        w = int(win.winfo_width())
        h = int(win.winfo_height())
        sw = int(win.winfo_screenwidth())
        sh = int(win.winfo_screenheight())
        # 至少露出标题栏一截
        margin = 40
        if x + w < margin:
            x = margin - min(w, sw // 2)
        if y + h < margin:
            y = 0
        if x > sw - margin:
            x = max(0, sw - margin)
        if y > sh - margin:
            y = max(0, sh - margin)
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


def remember_window_geometry(win, key: str) -> None:
    """读取当前 geometry 并写入配置。"""
    try:
        win.update_idletasks()
        geo = win.winfo_geometry()
    except Exception:
        return
    # winfo_geometry → "WxH+X+Y"
    save_geometry(key, geo)
