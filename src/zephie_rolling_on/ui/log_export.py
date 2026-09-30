"""导出运行日志（含自检报告），方便用户把问题材料发给我们。

打包成一个 zip，内含：

  运行日志.txt     界面日志全文（带时间戳）
  自检报告.txt     导出时现场跑一遍环境自检
  环境信息.txt     版本、是否打包运行、Python 版本、程序目录、编码等
  配置/…           用户配置快照（排查需要）

为什么打成 zip 而不是单个 txt：支持流程里最常见的问题不是导出失败，而是用户
只发了日志、缺了我们需要的环境信息，来回追问要耗掉好几天。zip 让用户只需发
一个文件。

zip 落在**程序根目录**（与 exe 同级），这样用户一定找得到；根目录不可写时
回退到临时目录。
"""

from __future__ import annotations

import datetime as _datetime
import locale
import os
import platform
import sys
import zipfile
from pathlib import Path

from zephie_rolling_on.paths import project_root

# 放进 zip 的用户配置（都是排查时真正会用到的；不含卡池历史等大数据）
CONFIG_SNAPSHOT_FILES: tuple[str, ...] = (
    "auto_click.yaml",
    "regions.yaml",
    "click_targets.yaml",
    "game_window.yaml",
    "hotkeys.yaml",
    "adventure_frame.yaml",
)

LOG_NAME = "运行日志.txt"
CHECK_NAME = "自检报告.txt"
ENV_NAME = "环境信息.txt"
CONFIG_DIR_NAME = "配置"


def _app_version() -> str:
    try:
        from zephie_rolling_on import __version__

        return str(__version__)
    except Exception:
        return "未知"


def _is_elevated() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def environment_report() -> str:
    """程序与运行环境的快照。"""
    lines = [
        "Zephie Rolling On — 环境信息",
        "=" * 46,
        f"程序版本   : {_app_version()}",
        f"运行方式   : {'打包运行' if getattr(sys, 'frozen', False) else '源码运行'}",
        f"Python     : {platform.python_version()}",
        f"操作系统   : {platform.platform()}",
        f"系统内部版本: {platform.version()}",
        f"管理员权限 : {'是' if _is_elevated() else '否'}",
        f"程序目录   : {project_root()}",
        f"可执行文件 : {sys.executable}",
        f"当前工作目录: {os.getcwd()}",
        # 编码相关：非 ASCII 路径的读写问题排查要用到
        f"首选项编码 : {locale.getpreferredencoding(False)}",
        f"文件系统编码: {sys.getfilesystemencoding()}",
        "=" * 46,
    ]
    return "\n".join(lines) + "\n"


def self_check_report() -> str:
    """现场跑一遍环境自检，返回报告文本。

    自检含「实际截屏测试」，会真的截一张图，所以**只能在脚本停止后**调用
    （界面侧负责把关）。
    """
    from zephie_rolling_on.diagnose import render, run_checks

    text, _ok = render(run_checks())
    # 自检为验证截屏会绑定窗口并留下 WGC 会话，导出后应释放，避免占着不放
    try:
        from zephie_rolling_on.vision.capture_wgc import release_wgc_session

        release_wgc_session(None)
    except Exception:
        pass
    return text


def _read_text_tolerant(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _bundle_name() -> str:
    # 文件名用 ASCII：QQ/微信传文件时不会被转义搞乱，且带版本+时间便于辨认
    stamp = _datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"ZephieLog_v{_app_version()}_{stamp}.zip"


def export_bundle(log_text: str, *, dest_dir: Path | None = None) -> Path:
    """把日志与自检材料打成一个 zip，返回 zip 路径。

    根目录不可写时回退到临时目录。自检报告若生成失败（例如缺依赖），仍会导出
    其余内容，并把原因写进报告位。
    """
    candidates: list[Path] = []
    if dest_dir is not None:
        candidates.append(Path(dest_dir))
    else:
        candidates.append(project_root())
        candidates.append(Path(os.environ.get("TEMP", ".")))

    try:
        check_text = self_check_report()
    except Exception as exc:  # noqa: BLE001
        check_text = (
            "自检未能完成："
            f"{type(exc).__name__}: {exc}\n"
            "（其余材料不受影响，仍已导出）\n"
        )

    payload: list[tuple[str, bytes]] = [
        (LOG_NAME, log_text.encode("utf-8")),
        (CHECK_NAME, check_text.encode("utf-8")),
        (ENV_NAME, environment_report().encode("utf-8")),
    ]

    config_dir = project_root() / "config"
    for name in CONFIG_SNAPSHOT_FILES:
        src = config_dir / name
        text = _read_text_tolerant(src)
        if text is not None:
            payload.append((f"{CONFIG_DIR_NAME}/{name}", text.encode("utf-8")))

    last_error: Exception | None = None
    for base in candidates:
        target = Path(base) / _bundle_name()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                for arcname, data in payload:
                    zf.writestr(arcname, data)
            return target
        except OSError as exc:
            last_error = exc
            continue
    raise OSError(f"无法写入导出文件：{last_error}")


def reveal(path: Path) -> None:
    """在资源管理器中定位该文件（失败则退化为打开所在目录）。"""
    try:
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(path)])
        return
    except Exception:
        pass
    try:
        os.startfile(str(Path(path).parent))
    except Exception:
        pass


class LogExportDialog:
    """导出日志的进度/结果对话框。

    自检要真的截一张图，可能耗时数秒，所以工作放在后台线程，界面用 ``after``
    轮询结果，避免主界面卡死看着像崩溃。
    """

    def __init__(
        self, master, log_text: str, *, on_log=None, on_closed=None
    ) -> None:
        import queue
        import threading
        import tkinter as tk

        from zephie_rolling_on.ui.zehpie_theme import (
            COLORS,
            RoundedButton,
            apply_zehpie_window_icon,
            cute_font,
        )

        theme = COLORS
        self._queue: "queue.Queue" = queue.Queue()
        self._on_log = on_log or (lambda _m: None)
        self._on_closed = on_closed or (lambda: None)
        self._result: Path | None = None
        self._error = ""
        self._closed = False

        # 先还原最小化的主窗，否则本窗口按 master 坐标居中会落到屏幕外
        # （最小化时 rootx/rooty 为 -32000），而它随后 grab_set 会让界面不可点。
        from zephie_rolling_on.ui.ui_geometry import (
            ensure_master_visible,
            should_set_transient,
        )

        restored = ensure_master_visible(master)

        dialog = tk.Toplevel(master)
        self._dialog = dialog
        dialog.title("导出日志")
        dialog.configure(bg=theme["bg"])
        dialog.resizable(False, False)
        # 主窗刚由最小化还原时不能设 transient：窗口管理器会把本窗口 withdraw
        # （坐标正确但完全不可见），而它随后 grab_set 会让界面既无弹窗也无响应。
        if should_set_transient(master, restored=restored):
            dialog.transient(master)

        # PhotoImage 需保引用，否则被 GC 回收后窗口图标会变回羽毛笔
        self._photos: list = []
        icon = apply_zehpie_window_icon(dialog)
        if icon is not None:
            self._photos.append(icon)

        shell = tk.Frame(dialog, bg=theme["bg"], padx=16, pady=14)
        shell.pack(fill="both", expand=True)
        card = tk.Frame(
            shell,
            bg=theme["card"],
            highlightbackground=theme["border"],
            highlightcolor=theme["border"],
            highlightthickness=2,
            padx=16,
            pady=14,
        )
        card.pack(fill="both", expand=True)

        self._title = tk.Label(
            card,
            text="Zephie正在整理日志…",
            anchor="w",
            justify="left",
            wraplength=420,
            bg=theme["card"],
            fg=theme.get("accent_deep", theme["accent_dark"]),
            font=cute_font(13),
        )
        self._title.pack(anchor="w")

        self._detail = tk.Label(
            card,
            text="会同时生成一份环境自检报告，可能需要几秒。",
            anchor="w",
            justify="left",
            wraplength=420,
            bg=theme["card"],
            fg=theme["text"],
            font=cute_font(9, bold=False),
        )
        self._detail.pack(anchor="w", pady=(8, 0))

        self._btn_row = tk.Frame(card, bg=theme["card"])

        def _close() -> None:
            if self._closed:
                return
            self._closed = True
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            # 通知调用方恢复「导出日志」按钮状态
            self._on_closed()

        self._btn_close = RoundedButton(
            self._btn_row,
            text="关闭",
            command=_close,
            bg_color=theme["primary_light"],
            fg_color=theme["primary"],
            hover_bg=theme["primary_hover"],
            border_color=theme["border"],
            font=cute_font(10),
            height=34,
            width=96,
        )
        self._btn_reveal = RoundedButton(
            self._btn_row,
            text="打开文件夹",
            command=self._on_reveal,
            bg_color=theme["accent"],
            fg_color="#FFFFFF",
            hover_bg=theme["accent_deep"],
            border_color=theme["accent"],
            font=cute_font(10),
            height=34,
            width=140,
        )

        dialog.protocol("WM_DELETE_WINDOW", _close)
        self._center(master)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        try:
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()
            dialog.after(200, lambda: dialog.attributes("-topmost", False))
        except tk.TclError:
            pass
        # 置顶会吞掉先前请求的位置（实测落到 (0,0) 或 Windows 级联位置），
        # 所以这里再定位一次。
        self._center(master)

        threading.Thread(
            target=self._worker,
            args=(log_text,),
            name="log-export",
            daemon=True,
        ).start()
        dialog.after(80, self._poll)

    # -- 内部 --

    def _center(self, master) -> None:
        """屏幕居中偏上。见 ``ui_geometry.center_on_screen``。

        用屏幕坐标而非 ``master`` 坐标：主窗最小化时 ``rootx/rooty`` 为 -32000，
        按 master 居中的弹窗会落到屏幕外，而它已 ``grab_set()``，用户既看不到
        弹窗又点不动界面。``master`` 参数保留仅为签名兼容。
        """
        from zephie_rolling_on.ui.ui_geometry import center_on_screen

        center_on_screen(self._dialog)

    def _worker(self, log_text: str) -> None:
        try:
            path = export_bundle(log_text)
            self._queue.put(("ok", path))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("error", f"{type(exc).__name__}: {exc}"))

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            kind, payload = self._queue.get_nowait()
        except Exception:
            self._dialog.after(80, self._poll)
            return
        if kind == "ok":
            self._show_done(Path(payload))
        else:
            self._show_error(str(payload))

    def _show_done(self, path: Path) -> None:
        if self._closed:
            return
        self._result = path
        self._title.config(text="日志已导出！")
        self._detail.config(
            text=f"已保存到程序目录：\n{path.name}\n\n"
            "把整个 zip 发给我们就行。\n"
            "（其中包含你的部分设置与窗口信息，仅用于排查问题）"
        )
        self._btn_reveal.pack(side="right")
        self._btn_close.pack(side="right", padx=(0, 8))
        self._btn_row.pack(fill="x", pady=(14, 2))
        self._on_log(f"[导出日志] 已保存：{path}")

    def _show_error(self, reason: str) -> None:
        if self._closed:
            return
        self._error = reason
        self._title.config(text="导出失败了…", fg="#C9786A")
        self._detail.config(text=f"原因：{reason}")
        self._btn_close.pack(side="right")
        self._btn_row.pack(fill="x", pady=(14, 2))
        self._on_log(f"[导出日志] 失败：{reason}")

    def _on_reveal(self) -> None:
        if self._result is not None:
            reveal(self._result)

