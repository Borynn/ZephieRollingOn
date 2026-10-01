"""独立自检：检查运行环境、依赖与资源是否满足运行要求。

**面向开发者**：普通用户不需要单独跑它——界面的「导出日志」会现场运行同一套
自检，把报告打进 zip，用户只需发一个文件。按需手动执行：

    源码运行:  python -m zephie_rolling_on.diagnose
    打包版:    ZephieRollingOn!.exe --check

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
        # 与运行期的 windows_build() 同一来源，避免报告与门禁各算各的而产生分歧
        from zephie_rolling_on.vision.capture import (
            windows_build,
            windows_build_source,
        )

        build = windows_build()
        source = windows_build_source()
    except Exception as exc:  # noqa: BLE001
        return [Check(WARN, "Windows 版本", f"无法解析：{exc}")]

    if build <= 0:
        return [Check(WARN, "Windows 版本", "无法确定内部版本号")]

    name = _build_name(build)
    origin = f"，来源 {source}" if source else ""
    if build >= WIN10_RTM_BUILD:
        out.append(Check(OK, "系统版本", f"{name}（内部版本 {build}{origin}）"))
    else:
        out.append(
            Check(
                FAIL,
                "系统版本",
                f"内部版本 {build}{origin} 低于 Windows 10，不受支持",
            )
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

def _bound_game_window() -> tuple[int, str] | None:
    """读取界面绑定的游戏窗口（``config/game_window.yaml``），返回 ``(hwnd, 标题)``。

    自检应该优先验证**用户真正在用的那个窗口**：只测任意大窗口时，可能挑中
    WGC 根本无法捕获的类型（如资源管理器）而误报失败，也可能测了一个与游戏
    毫无关系的窗口，让最关键的取证行失去意义。

    读法与 ``RuntimeState.bootstrap_from_disk`` 保持一致：优先 ``capture_hwnd``
    （多开时指向真正的捕获目标），再 ``hwnd``、``root_hwnd``。句柄必须仍然有效。
    """
    try:
        import yaml

        from zephie_rolling_on.paths import project_root

        path = project_root() / "config" / "game_window.yaml"
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            return None
    except Exception:
        return None

    try:
        import win32gui
    except Exception:
        return None

    for key in ("capture_hwnd", "hwnd", "root_hwnd"):
        value = raw.get(key)
        if not value:
            continue
        try:
            hwnd = int(value)
        except (TypeError, ValueError):
            continue
        try:
            if hwnd and win32gui.IsWindow(hwnd):
                return hwnd, str(raw.get("title") or "")
        except Exception:
            continue
    return None


def check_capture() -> list[Check]:
    out: list[Check] = []
    try:
        from zephie_rolling_on.vision.capture import (
            windows_build,
            wgc_supported,
            wgc_unsupported_reason,
        )

        build = windows_build()
        if wgc_supported():
            # 这一行只代表「扩展能导入」，不代表会话真能建起来（Win10 那次
            # IsBorderRequired 事故就是导入成功、会话全失败）。所以措辞留余地，
            # 真正的可用性由下面的「WGC 会话实测」给出。
            out.append(
                Check(
                    OK,
                    "截屏后端",
                    f"Windows Graphics Capture 支持（内部版本 {build}；扩展导入成功）",
                )
            )
        else:
            # 把真实原因写进报告：以前只说「无 WGC」，无法区分版本不足与导入失败
            reason = wgc_unsupported_reason() or "原因未知"
            out.append(
                Check(
                    OK,
                    "截屏后端",
                    f"无法使用 Windows Graphics Capture：{reason}；"
                    "改用 GDI 桌面裁切，需保持游戏窗口不被遮挡",
                )
            )
    except Exception as exc:  # noqa: BLE001
        out.append(Check(FAIL, "截屏后端", f"{type(exc).__name__}: {exc}"))

    # 只测**绑定的游戏窗口**：那才是用户真正关心的结论，也是实际会被捕获的目标。
    # 以前还会顺带试最多 6 个其它大窗口，但 WGC 对资源管理器这类窗口本就不支持，
    # 逐窗重试既把结论弄成误报、又把同一条失败路径反复触发——而「会话创建失败」
    # 是机器级条件、与窗口无关，重试没有任何意义。
    bound = _bound_game_window()
    if bound is None:
        out.append(
            Check(
                WARN,
                "实际截屏测试",
                "未绑定游戏窗口，无法实测（先在界面绑定游戏窗口后重跑自检）",
            )
        )
        return out

    hwnd, title = bound
    label = f"绑定的游戏窗口「{(title or '').strip()[:24] or '(无标题)'}」"

    # 单独实测一次「会话能否创建」。截屏失败的原因是多层的（会话创建 / 取帧超时 /
    # 画面判空），而 wgc_supported() 只证明扩展能导入，所以把这一层单独查出来，
    # 避免再出现「报告说 WGC 可用、实际每次截屏都失败」这种误导。
    try:
        from zephie_rolling_on.vision.capture_wgc import (
            probe_wgc_session,
            session_create_blocked,
        )

        ok, reason = probe_wgc_session(hwnd)
        if ok:
            out.append(Check(OK, "WGC 会话实测", f"{label}可创建 WGC 会话"))
        else:
            out.append(Check(FAIL, "WGC 会话实测", f"{label}创建失败：{reason}"))
        # 熔断状态单独报告：运行时降级到桌面裁切是**静默**的，用户不会看到任何
        # 提示，只有这里能查出「WGC 其实已被停用」。
        if session_create_blocked(hwnd):
            out.append(
                Check(
                    WARN,
                    "WGC 重试状态",
                    f"{label}本次绑定内已停止重试 WGC（连续失败达上限），"
                    "运行时会改用桌面裁切、要求窗口不被遮挡；"
                    "重新绑定游戏窗口即可重置（上面这行是绕过该限制实测的结果）",
                )
            )
    except Exception as exc:  # noqa: BLE001
        out.append(Check(FAIL, "WGC 会话实测", f"{type(exc).__name__}: {exc}"))

    # 再真正截一张图，才算「能工作」的证据。
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
                Check(
                    OK,
                    "实际截屏测试",
                    f"{label}测试，{last_capture_method()} 成功 {w}x{h}",
                )
            )
        else:
            note = last_capture_failure_note() or "未返回图像"
            out.append(Check(WARN, "实际截屏测试", f"{label}：{note}"))
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

    # VELA 原生引擎：进程内加载，缺失只影响 .vpk。
    # 两个引擎（v4 / v4.1）模块名不同、模型格式互斥，各自独立可用。
    # 用户看到的是 engine_label()，不是模块标识符。
    try:
        from zephie_rolling_on.decision_models.vela_package import (
            DEFAULT_ENGINE_MODULE,
            ENGINE_LABELS,
            _find_vela_module,
            engine_label,
        )

        available = [
            engine_label(name)
            for name in (DEFAULT_ENGINE_MODULE, *ENGINE_LABELS)
            if _find_vela_module(name) is not None
        ]
        # 去重：同一 label 只报一次
        available = list(dict.fromkeys(available))
        if available:
            out.append(
                Check(
                    OK,
                    "VELA 原生引擎",
                    "已可导入（" + "、".join(available) + "），对应 .vpk 可用",
                )
            )
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
