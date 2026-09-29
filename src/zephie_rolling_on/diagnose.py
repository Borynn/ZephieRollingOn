"""独立自检：检查运行环境、依赖与资源是否满足运行要求。

独立于脚本界面，**不随界面启动自动运行**。按需手动执行：

    源码运行:  python -m zephie_rolling_on.diagnose
    便携包:    双击 自检.bat

目标平台：Windows 10 / Windows 11，仅 64 位。

退出码：0 无致命问题；1 存在 FAIL 项。
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

# Windows 10 起始内部版本号；WGC（Windows.Graphics.Capture）自 1803 起提供。
WIN10_RTM_BUILD = 10240
WGC_MIN_BUILD = 17134  # Windows 10 1803

# Windows 10/11 内部版本号 → 版本名称
_BUILD_NAMES: tuple[tuple[int, str], ...] = (
    (26100, "Windows 11 24H2"),
    (22631, "Windows 11 23H2"),
    (22621, "Windows 11 22H2"),
    (22000, "Windows 11 21H2"),
    (19045, "Windows 10 22H2"),
    (19044, "Windows 10 21H2"),
    (19043, "Windows 10 21H1"),
    (19042, "Windows 10 20H2"),
    (19041, "Windows 10 2004"),
    (18363, "Windows 10 1909"),
    (18362, "Windows 10 1903"),
    (17763, "Windows 10 1809"),
    (17134, "Windows 10 1803"),
    (16299, "Windows 10 1709"),
    (15063, "Windows 10 1703"),
    (14393, "Windows 10 1607"),
    (10586, "Windows 10 1511"),
    (10240, "Windows 10 1500"),
)


@dataclass
class Check:
    level: str
    label: str
    detail: str = ""


def _build_name(build: int) -> str:
    for threshold, name in _BUILD_NAMES:
        if build >= threshold:
            return name
    return "未知版本"


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def check_windows() -> list[Check]:
    out: list[Check] = []
    try:
        rel, ver, _csd, _ptype = platform.win32_ver()
        build = int(platform.version().split(".")[-1])
    except Exception as exc:  # noqa: BLE001
        return [Check(WARN, "Windows 版本", f"无法解析：{exc}")]

    name = _build_name(build)
    if build >= WIN10_RTM_BUILD:
        out.append(Check(OK, "系统版本", f"{name}（内部版本 {build}）"))
    else:
        out.append(
            Check(FAIL, "系统版本", f"内部版本 {build} 低于 Windows 10，不受支持")
        )

    if build < WGC_MIN_BUILD:
        # 无 WGC 不等于不可用：桌面裁切在 Win10 首发版上即可工作，只是要求
        # 游戏窗口不被遮挡（被遮挡时软件会提示并停止，不会截到错误画面）。
        out.append(
            Check(
                OK,
                "屏幕捕获方式",
                "无 Windows.Graphics.Capture（需 1803 及以上），将使用 GDI 桌面裁切；"
                "请保持游戏窗口不被其他窗口遮挡",
            )
        )
    return out


def check_bitness() -> list[Check]:
    if sys.maxsize > 2**32:
        return [Check(OK, "系统位数", "64 位")]
    return [Check(FAIL, "系统位数", "32 位系统不受支持")]


def check_admin() -> list[Check]:
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:  # noqa: BLE001
        return [Check(WARN, "管理员权限", f"无法检测：{exc}")]
    if elevated:
        return [Check(OK, "管理员权限", "已提权，窗口捕获与输入注入可用")]
    return [
        Check(
            WARN,
            "管理员权限",
            "未提权；部分捕获/点击可能被系统拦截。请以管理员身份运行",
        )
    ]


def check_python() -> list[Check]:
    frozen = bool(getattr(sys, "frozen", False))
    how = "打包运行" if frozen else "源码运行"
    return [Check(OK, "Python 运行时", f"{platform.python_version()}（{how}）")]


def check_dpi() -> list[Check]:
    """DPI 感知在导入 win32_window 时设置；这里复用真实代码路径验证它生效。"""
    try:
        # 导入即执行 SetProcessDpiAwareness，正是应用启动时做的事
        from zephie_rolling_on.vision import win32_window  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return [Check(FAIL, "DPI 感知", f"设置失败：{type(exc).__name__}: {exc}")]

    try:
        val = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(val))
        label = {0: "未设置", 1: "System DPI", 2: "Per-Monitor DPI"}.get(
            val.value, str(val.value)
        )
        level = OK if val.value >= 1 else WARN
        return [Check(level, "DPI 感知", label)]
    except Exception:
        try:
            aware = bool(ctypes.windll.user32.IsProcessDPIAware())
            return [
                Check(
                    OK if aware else WARN,
                    "DPI 感知",
                    "System DPI" if aware else "未设置（坐标可能偏移）",
                )
            ]
        except Exception as exc:  # noqa: BLE001
            return [Check(WARN, "DPI 感知", f"无法检测：{exc}")]


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _probe(module: str, label: str) -> Check:
    try:
        mod = __import__(module, fromlist=["__version__"])
        version = getattr(mod, "__version__", "")
        return Check(OK, label, str(version) if version else "已就绪")
    except Exception as exc:  # noqa: BLE001
        return Check(FAIL, label, f"{type(exc).__name__}: {exc}")


def check_dependencies() -> list[Check]:
    # tkinter 用独立探测：它是界面能启动的前提
    try:
        import tkinter  # noqa: F401

        tk = Check(OK, "tkinter（界面）", "已就绪")
    except Exception as exc:  # noqa: BLE001
        tk = Check(FAIL, "tkinter（界面）", f"{type(exc).__name__}: {exc}")

    return [
        tk,
        _probe("cv2", "OpenCV（模板匹配）"),
        _probe("numpy", "NumPy"),
        _probe("yaml", "PyYAML（配置）"),
        _probe("PIL", "Pillow（图像）"),
        _probe("win32gui", "pywin32（窗口）"),
        _probe("windows_capture", "windows-capture（截屏）"),
        _probe("onnxruntime", "ONNX Runtime（OCR）"),
        _probe("rapidocr", "RapidOCR（数字识别）"),
    ]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

_REQUIRED_CONFIG = (
    "regions.yaml",
    "click_targets.yaml",
    "adventure_frame.yaml",
    "auto_click.default.yaml",
)

_REQUIRED_ASSETS = (
    "ui/zehpie_window_icon.png",
    "ui/deck_drawn_mark.png",
    "lucky_cards/manifest.yaml",
)


def _root() -> Path:
    from zephie_rolling_on.paths import project_root

    return project_root()


def check_resources() -> list[Check]:
    root = _root()
    out: list[Check] = []

    missing = [n for n in _REQUIRED_CONFIG if not (root / "config" / n).is_file()]
    out.append(
        Check(
            OK if not missing else FAIL,
            "配置文件",
            f"{len(_REQUIRED_CONFIG)} 项齐全" if not missing else "缺少：" + ", ".join(missing),
        )
    )

    missing_a = [n for n in _REQUIRED_ASSETS if not (root / "assets" / n).is_file()]
    out.append(
        Check(
            OK if not missing_a else FAIL,
            "图像资源",
            f"{len(_REQUIRED_ASSETS)} 项齐全" if not missing_a else "缺少：" + ", ".join(missing_a),
        )
    )

    for name in ("map.xlsx", "map_info.txt"):
        exists = (root / name).is_file()
        out.append(Check(OK if exists else FAIL, name, "已就绪" if exists else "缺失"))

    return out


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _find_test_window() -> int | None:
    """挑一个可见、够大的窗口作为截屏测试对象（排除本工具自身）。"""
    try:
        import win32gui

        from zephie_rolling_on.vision.win32_window import is_our_tool_window
    except Exception:
        return None

    best = [0, 0]  # [area, hwnd]

    def _cb(hwnd: int, _extra) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return True
            if not win32gui.GetWindowText(hwnd):
                return True
            if is_our_tool_window(hwnd):
                return True
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            w, h = right - left, bottom - top
            if w < 640 or h < 480:
                return True
            area = w * h
            if area > best[0]:
                best[0], best[1] = area, int(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None
    return best[1] or None


def check_capture() -> list[Check]:
    out: list[Check] = []
    try:
        from zephie_rolling_on.vision.capture import windows_build, wgc_supported

        build = windows_build()
        if wgc_supported():
            out.append(
                Check(OK, "截屏后端", f"Windows Graphics Capture 可用（内部版本 {build}）")
            )
        else:
            out.append(
                Check(
                    OK,
                    "截屏后端",
                    f"使用 GDI 桌面裁切（内部版本 {build} 无 Windows Graphics Capture）"
                    "；需保持游戏窗口不被遮挡",
                )
            )
    except Exception as exc:  # noqa: BLE001
        out.append(Check(FAIL, "截屏后端", f"{type(exc).__name__}: {exc}"))

    # 实测一次：真正截一张图，才算证据
    hwnd = _find_test_window()
    if not hwnd:
        out.append(
            Check(
                WARN,
                "实际截屏测试",
                "未找到可用于测试的窗口（打开任意窗口后重试）",
            )
        )
        return out

    try:
        import win32gui

        title = win32gui.GetWindowText(hwnd) or "(无标题)"
    except Exception:
        title = "(未知)"

    try:
        from zephie_rolling_on.vision.capture import (
            capture_window_client,
            last_capture_failure_note,
        )
        from zephie_rolling_on.vision.capture_wgc import last_capture_method

        frame = capture_window_client(hwnd)
        if frame is not None and getattr(frame, "size", 0) > 0:
            h, w = frame.shape[:2]
            out.append(
                Check(OK, "实际截屏测试", f"用「{title}」测试，{last_capture_method()} 成功 {w}x{h}")
            )
        else:
            note = last_capture_failure_note() or "未返回图像"
            out.append(
                Check(
                    WARN,
                    "实际截屏测试",
                    f"用「{title}」测试失败：{note}"
                    "（若游戏本身可正常识别，可忽略此项）",
                )
            )
    except Exception as exc:  # noqa: BLE001
        out.append(Check(FAIL, "实际截屏测试", f"{type(exc).__name__}: {exc}"))

    return out


# ---------------------------------------------------------------------------
# Decision models
# ---------------------------------------------------------------------------

def check_models() -> list[Check]:
    out: list[Check] = []
    try:
        from zephie_rolling_on.decision_models import discover_decision_models
        from zephie_rolling_on.decision_models.model_host_client import host_executable
    except Exception as exc:  # noqa: BLE001
        return [Check(FAIL, "决策模型模块", f"{type(exc).__name__}: {exc}")]

    host = host_executable()
    out.append(
        Check(
            OK if host else WARN,
            "model_host.exe",
            str(host) if host else "未找到；.zm 模型不可用（.vpk 不受影响）",
        )
    )

    skipped: list[tuple[str, str]] = []

    def _on_error(name: str, exc: BaseException) -> None:
        skipped.append((name, f"{type(exc).__name__}: {exc}"))

    try:
        models = discover_decision_models(on_error=_on_error)
    except Exception as exc:  # noqa: BLE001
        return out + [Check(FAIL, "模型扫描", f"{type(exc).__name__}: {exc}")]

    if models:
        names = ", ".join(f"{m.display.name}({m.display.model_id})" for m in models)
        out.append(Check(OK, "可用模型", f"{len(models)} 个：{names}"))
    else:
        out.append(
            Check(WARN, "可用模型", "decision_models/ 下未发现模型包，请先放入模型")
        )

    for name, reason in skipped:
        out.append(Check(WARN, f"模型包 {name}", f"加载失败，已跳过：{reason}"))

    # VELA 原生引擎：进程内加载，缺失只影响 .vpk
    try:
        from zephie_rolling_on.decision_models.vela_package import _find_vela_module

        if _find_vela_module() is not None:
            out.append(Check(OK, "VELA 原生引擎", "已可导入，.vpk 可用"))
        else:
            out.append(
                Check(WARN, "VELA 原生引擎", "未找到；.vpk 模型不可用（.zm 不受影响）")
            )
    except Exception as exc:  # noqa: BLE001
        out.append(Check(WARN, "VELA 原生引擎", f"检测失败：{type(exc).__name__}: {exc}"))

    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run_checks() -> list[Check]:
    checks: list[Check] = []
    for fn in (
        check_windows,
        check_bitness,
        check_admin,
        check_python,
        check_dpi,
        check_dependencies,
        check_resources,
        check_capture,
        check_models,
    ):
        try:
            checks.extend(fn())
        except Exception as exc:  # noqa: BLE001
            checks.append(
                Check(FAIL, fn.__name__, f"{type(exc).__name__}: {exc}")
            )
    return checks


def render(checks: list[Check]) -> tuple[str, bool]:
    root = _root()
    header = [
        "Zephie Rolling On — 环境自检",
        "=" * 46,
        "目标平台：Windows 10 / Windows 11，仅 64 位",
        f"程序目录：{root}",
        "=" * 46,
    ]
    body = [f"[{c.level:4}] {c.label}" + (f" — {c.detail}" if c.detail else "") for c in checks]

    counts = {OK: 0, WARN: 0, FAIL: 0}
    for c in checks:
        counts[c.level] = counts.get(c.level, 0) + 1
    footer = [
        "=" * 46,
        f"通过 {counts[OK]} 项，警告 {counts[WARN]} 项，失败 {counts[FAIL]} 项",
    ]
    if counts[FAIL]:
        footer.append("存在失败项：请按上面提示处理后重试。")
    elif counts[WARN]:
        footer.append("无失败项。警告项不影响启动，但可能影响识别效果。")
    else:
        footer.append("环境正常。")

    text = "\n".join(header + body + footer) + "\n"
    return text, counts[FAIL] == 0


def _write_report(text: str) -> Path | None:
    for base in (_root(), Path(os.environ.get("TEMP", "."))):
        try:
            p = base / "check_report.txt"
            p.write_text(text, encoding="utf-8")
            return p
        except OSError:
            continue
    return None


def _show_window(text: str, report: Path | None) -> bool:
    """用 Tk 显示报告（打包版无控制台）。失败返回 False。"""
    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except Exception:
        return False
    try:
        root_win = tk.Tk()
        root_win.title("Zephie Rolling On — 环境自检")
        root_win.geometry("760x560")
        frame = tk.Frame(root_win)
        frame.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        box = scrolledtext.ScrolledText(
            frame, wrap="word", font=("Consolas", 9), height=30
        )
        box.pack(fill="both", expand=True)
        box.insert("1.0", text)
        box.configure(state="disabled")

        bar = tk.Frame(root_win)
        bar.pack(fill="x", padx=10, pady=8)

        def _copy() -> None:
            root_win.clipboard_clear()
            root_win.clipboard_append(text)
            hint.config(text="已复制到剪贴板")

        hint = tk.Label(bar, text=f"报告已保存：{report}" if report else "")
        hint.pack(side="left")
        tk.Button(bar, text="复制报告", command=_copy).pack(side="right")
        tk.Button(bar, text="关闭", command=root_win.destroy).pack(side="right", padx=6)
        root_win.mainloop()
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    text, ok = render(run_checks())
    report = _write_report(text)

    if "--text" in args or not _show_window(text, report):
        try:
            print(text)
        except Exception:
            pass
        if report is not None:
            try:
                print(f"报告已保存：{report}")
            except Exception:
                pass

    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
