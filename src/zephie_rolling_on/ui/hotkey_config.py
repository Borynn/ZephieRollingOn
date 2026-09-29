"""全局快捷键配置：解析/格式化、读写 hotkeys.yaml。"""
from __future__ import annotations

from zephie_rolling_on.paths import project_root

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = project_root()
HOTKEYS_PATH = PROJECT_ROOT / "config" / "hotkeys.yaml"

ACTION_BIND_WINDOW = "bind_window"
ACTION_AUTO_START = "auto_click_start"
ACTION_AUTO_PAUSE = "auto_click_pause"  # 已废弃：界面/设置不再提供；保留常量以免旧配置解析报错
ACTION_AUTO_STOP = "auto_click_stop"

ALL_ACTIONS = (
    ACTION_BIND_WINDOW,
    ACTION_AUTO_START,
    ACTION_AUTO_STOP,
)

ACTION_LABELS: dict[str, str] = {
    ACTION_BIND_WINDOW: "绑定游戏窗口",
    ACTION_AUTO_START: "自动点击 — 启动/继续",
    ACTION_AUTO_STOP: "自动点击 — 停止",
}

DEFAULT_HOTKEYS: dict[str, str] = {
    ACTION_BIND_WINDOW: "Ctrl+Shift+G",
    ACTION_AUTO_START: "Ctrl+Alt+F9",
    ACTION_AUTO_STOP: "Ctrl+Alt+F11",
}

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

MOD_NAME_TO_FLAG: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}

FLAG_TO_MOD_NAME: dict[int, str] = {
    MOD_CONTROL: "Ctrl",
    MOD_ALT: "Alt",
    MOD_SHIFT: "Shift",
    MOD_WIN: "Win",
}

MOD_ORDER = (MOD_CONTROL, MOD_ALT, MOD_SHIFT, MOD_WIN)

VK_NUMPAD0 = 0x60
VK_NUMPAD9 = 0x69

# Tk keysym → Windows VK（常用键）
_KEYSYM_TO_VK: dict[str, int] = {}
for _i in range(1, 25):
    _KEYSYM_TO_VK[f"F{_i}"] = 0x6F + _i
for _c in range(ord("A"), ord("Z") + 1):
    _KEYSYM_TO_VK[chr(_c)] = _c
    _KEYSYM_TO_VK[chr(_c).lower()] = _c
for _c in range(ord("0"), ord("9") + 1):
    _KEYSYM_TO_VK[chr(_c)] = _c
for _d in range(10):
    _KEYSYM_TO_VK[f"KP_{_d}"] = VK_NUMPAD0 + _d
    _KEYSYM_TO_VK[f"Num{_d}"] = VK_NUMPAD0 + _d
    _KEYSYM_TO_VK[f"Numpad{_d}"] = VK_NUMPAD0 + _d
_KEYSYM_TO_VK.update(
    {
        "space": 0x20,
        "Return": 0x0D,
        "Tab": 0x09,
        "Escape": 0x1B,
        "BackSpace": 0x08,
        "Delete": 0x2E,
        "Insert": 0x2D,
        "Home": 0x24,
        "End": 0x23,
        "Prior": 0x21,
        "Next": 0x22,
        "Left": 0x25,
        "Right": 0x27,
        "Up": 0x26,
        "Down": 0x28,
    }
)

_VK_TO_KEY_LABEL: dict[int, str] = {}
for _c in range(ord("A"), ord("Z") + 1):
    _VK_TO_KEY_LABEL[_c] = chr(_c)
for _c in range(ord("0"), ord("9") + 1):
    _VK_TO_KEY_LABEL[_c] = chr(_c)
for _i in range(1, 25):
    _VK_TO_KEY_LABEL[0x6F + _i] = f"F{_i}"
for _d in range(10):
    _VK_TO_KEY_LABEL[VK_NUMPAD0 + _d] = f"Num{_d}"


@dataclass(frozen=True)
class HotkeyBinding:
    action: str
    enabled: bool
    modifiers: tuple[str, ...]
    key: str
    vk: int | None = None

    @property
    def label(self) -> str:
        if not self.enabled or not self.key:
            return "（未设置）"
        return format_hotkey(self.modifiers, self.key)

    def mod_vk(self) -> tuple[int, int] | None:
        if not self.enabled or not self.key:
            return None
        mod = modifiers_to_flags(self.modifiers)
        vk = self.vk if self.vk is not None else key_to_vk(self.key)
        if vk is None:
            return None
        return mod, vk


def format_hotkey(modifiers: tuple[str, ...] | list[str], key: str) -> str:
    names: list[str] = []
    mod_set = {m.lower() for m in modifiers}
    for flag in MOD_ORDER:
        name = FLAG_TO_MOD_NAME[flag]
        token = name.lower()
        if token in mod_set or (token == "ctrl" and "control" in mod_set):
            names.append(name)
    key_label = key
    if key_label.startswith("Num") and len(key_label) == 4 and key_label[3:].isdigit():
        key_label = key_label  # Num6
    elif len(key) == 1 and key.isalpha():
        key_label = key.upper()
    elif key.startswith("f") and key[1:].isdigit():
        key_label = key.upper()
    names.append(key_label)
    return "+".join(names)


def parse_hotkey_string(text: str) -> tuple[tuple[str, ...], str] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if not parts:
        return None
    key = parts[-1]
    mods = tuple(p.lower() for p in parts[:-1])
    for m in mods:
        if m not in MOD_NAME_TO_FLAG:
            return None
    if key_to_vk(key) is None:
        return None
    return mods, key


def modifiers_to_flags(modifiers: tuple[str, ...] | list[str]) -> int:
    flags = MOD_NOREPEAT
    for m in modifiers:
        flags |= MOD_NAME_TO_FLAG.get(m.lower(), 0)
    return flags


def key_to_vk(key: str) -> int | None:
    if not key:
        return None
    k = key.strip()
    if k in _KEYSYM_TO_VK:
        return _KEYSYM_TO_VK[k]
    if len(k) == 1 and k.isalpha():
        return ord(k.upper())
    if len(k) == 1 and k.isdigit():
        return ord(k)
    if k.startswith("Num") and len(k) == 4 and k[3:].isdigit():
        return VK_NUMPAD0 + int(k[3:])
    ku = k.upper()
    if ku.startswith("F") and ku[1:].isdigit():
        n = int(ku[1:])
        if 1 <= n <= 24:
            return 0x6F + n
    return None


def vk_to_key_label(vk: int) -> str:
    return _VK_TO_KEY_LABEL.get(vk, f"VK{vk:02X}")


def modifiers_physically_pressed() -> tuple[str, ...]:
    """用 GetAsyncKeyState 读取真实修饰键，避免 Tk 小键盘误报 Alt。"""
    if sys.platform != "win32":
        return ()
    user32 = __import__("ctypes").windll.user32
    mods: list[str] = []
    if user32.GetAsyncKeyState(0x11) & 0x8000:
        mods.append("ctrl")
    if user32.GetAsyncKeyState(0x10) & 0x8000:
        mods.append("shift")
    if user32.GetAsyncKeyState(0x12) & 0x8000:
        mods.append("alt")
    if user32.GetAsyncKeyState(0x5B) & 0x8000 or user32.GetAsyncKeyState(0x5C) & 0x8000:
        mods.append("win")
    return tuple(mods)


def binding_from_string(action: str, text: str) -> HotkeyBinding:
    parsed = parse_hotkey_string(text)
    if parsed is None:
        return HotkeyBinding(action=action, enabled=False, modifiers=(), key="")
    mods, key = parsed
    vk = key_to_vk(key)
    return HotkeyBinding(action=action, enabled=True, modifiers=mods, key=key, vk=vk)


def binding_from_tk_event(action: str, event: Any) -> HotkeyBinding | None:
    """从 Tk KeyPress 构造绑定。Windows 下用 keycode + GetAsyncKeyState，避免小键盘误带 Alt。"""
    keysym = str(getattr(event, "keysym", "") or "")
    if keysym in (
        "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
        "Win_L", "Win_R", "Super_L", "Super_R", "Meta_L", "Meta_R",
    ):
        return None

    vk = int(getattr(event, "keycode", 0) or 0)
    if sys.platform == "win32" and vk > 0:
        key = vk_to_key_label(vk)
        if VK_NUMPAD0 <= vk <= VK_NUMPAD9:
            key = f"Num{vk - VK_NUMPAD0}"
        elif key.startswith("VK"):
            if keysym.startswith("KP_") and keysym[3:].isdigit():
                d = int(keysym[3:])
                vk = VK_NUMPAD0 + d
                key = f"Num{d}"
            elif key_to_vk(keysym) is not None:
                vk = key_to_vk(keysym) or vk
                key = keysym.upper() if len(keysym) == 1 and keysym.isalpha() else keysym
            else:
                return None
        mods = modifiers_physically_pressed()
        return HotkeyBinding(action=action, enabled=True, modifiers=mods, key=key, vk=vk)

    mods_list: list[str] = []
    state = int(getattr(event, "state", 0))
    if state & 0x4:
        mods_list.append("ctrl")
    if state & 0x1:
        mods_list.append("shift")
    if keysym.startswith("KP_"):
        pass
    elif state & 0x20000 or state & 0x8:
        mods_list.append("alt")
    if state & 0x200000:
        mods_list.append("win")

    key = keysym
    if keysym.startswith("KP_") and keysym[3:].isdigit():
        d = int(keysym[3:])
        key = f"Num{d}"
    elif len(key) == 1 and key.isalpha():
        key = key.upper()
    vk = key_to_vk(key)
    if vk is None:
        return None
    return HotkeyBinding(action=action, enabled=True, modifiers=tuple(mods_list), key=key, vk=vk)


def load_hotkey_bindings(path: Path | None = None) -> dict[str, HotkeyBinding]:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None and rt.hotkeys:
            out: dict[str, HotkeyBinding] = {}
            for action in ALL_ACTIONS:
                text = str(rt.hotkeys.get(action, "") or "")
                out[action] = binding_from_string(action, text)
            return out

    p = path or HOTKEYS_PATH
    raw: dict[str, Any] = {}
    if p.is_file():
        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    out = {}
    for action in ALL_ACTIONS:
        val = raw.get(action, DEFAULT_HOTKEYS.get(action, ""))
        if isinstance(val, dict):
            enabled = bool(val.get("enabled", True))
            mods = tuple(str(m).lower() for m in (val.get("modifiers") or ()))
            key = str(val.get("key", "") or "")
            if not enabled or not key:
                out[action] = HotkeyBinding(action=action, enabled=False, modifiers=(), key="")
            else:
                vk = key_to_vk(key)
                out[action] = HotkeyBinding(action=action, enabled=True, modifiers=mods, key=key, vk=vk)
        else:
            out[action] = binding_from_string(action, str(val or ""))
    return out


def save_hotkey_bindings(bindings: dict[str, HotkeyBinding], path: Path | None = None) -> Path:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            labels: dict[str, str] = {}
            for action in ALL_ACTIONS:
                b = bindings.get(action) or HotkeyBinding(
                    action=action, enabled=False, modifiers=(), key=""
                )
                value = b.label if b.enabled and b.key else ""
                if value == "（未设置）":
                    value = ""
                labels[action] = value
            rt.hotkeys = labels
            return HOTKEYS_PATH

    p = path or HOTKEYS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 全局快捷键（控制面板运行中生效；修改后请在「快捷键设置」里保存并应用）",
        "# 格式：修饰键+主键；修饰键 Ctrl/Alt/Shift/Win 可省略；小键盘数字保存为 Num6 等",
        "# 留空表示禁用该项",
        "",
    ]
    for action in ALL_ACTIONS:
        b = bindings.get(action) or HotkeyBinding(action=action, enabled=False, modifiers=(), key="")
        value = b.label if b.enabled and b.key else ""
        if value == "（未设置）":
            value = ""
        lines.append(f"{action}: {value or ''}")
    lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def validate_bindings(bindings: dict[str, HotkeyBinding]) -> list[str]:
    """检查重复快捷键等问题，返回错误文案列表。"""
    errors: list[str] = []
    seen: dict[str, str] = {}
    for action in ALL_ACTIONS:
        b = bindings.get(action)
        if b is None or not b.enabled or not b.key:
            continue
        if b.mod_vk() is None:
            errors.append(f"{ACTION_LABELS[action]}：无效快捷键 {b.label}")
            continue
        label = b.label
        if label in seen:
            errors.append(f"{ACTION_LABELS[action]} 与 {ACTION_LABELS[seen[label]]} 快捷键重复：{label}")
        else:
            seen[label] = action
    return errors
