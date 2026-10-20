"""导出运行日志（含自检报告），方便用户把问题材料发给我们。

打包成一个 zip，内含：

  运行日志.txt     界面日志全文（带时间戳）
  自检报告.txt     导出时现场跑一遍环境自检
  环境信息.txt     版本、是否打包运行、Python 版本、程序目录、编码等
  截图.png         自检时真实截到的那一帧（用户当时看到的画面）
  截图-原始客户区.png  归一化前的客户区原图，分辨率不匹配时也保留
  截图-区域标注.png  同一帧，叠加 regions.yaml 各检测区域框与大冒险框
  配置/…           用户配置快照（排查需要）

为什么要带那两张图：自检本来就会真的截一张图，但以前只留下文字结论。用户反馈
「识别不对」时，日志里只有「识别失败/骰子不足」这类结论，看不到他当时的画面，
也无法判断是标定偏了、区域框歪了、还是阈值不对——只能再让用户手工截图，来回
好几轮。把已有的那一帧连同区域标注一起放进 zip，一轮就能定位。

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
from typing import Any

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
# 自检时真实截到的那一帧，以及把 regions.yaml 各检测区域框画上去的同一帧。
# 用户反馈「识别不对」时，这两张图才能说明「他当时看到的是什么、框有没有对」。
SHOT_NAME = "截图.png"
REGION_NAME = "截图-区域标注.png"

# 标注图上要画出来的区域（键 -> 图上标签）。顺序即画图顺序。
_OVERLAY_BOXES: tuple[tuple[str, str], ...] = (
    ("cell_id_region", "cell"),
    ("dim_check_region", "dim"),
    ("dice_usage_region", "dice"),
    ("inventory_dice_region_a", "invA"),
    ("inventory_dice_region_b", "invB"),
    ("charge_dice_region", "charge"),
    ("lucky_cards_region", "lucky"),
)
# 这些是「弹窗模板搜索区」，坐标在 config[key]["search"] 下
_OVERLAY_SEARCH_BOXES: tuple[tuple[str, str], ...] = (
    ("confirm_button", "confirm"),
    ("execute_task_button", "task"),
    ("roll_kind", "roll"),
)
_OVERLAY_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 215, 255), (0, 200, 80), (255, 120, 0), (0, 0, 255),
    (255, 0, 255), (0, 255, 255), (200, 200, 0),
)


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


def self_check_report(bound_window: tuple[int, str] | None = None) -> str:
    """现场跑一遍环境自检，返回报告文本。

    自检含「实际截屏测试」，会真的截一张图，所以**只能在脚本停止后**调用
    （界面侧负责把关）。
    """
    from zephie_rolling_on.diagnose import render, run_checks

    text, _ok = render(run_checks(bound_window=bound_window))
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


def _encode_png(img: Any) -> bytes | None:
    """BGR numpy -> PNG 字节；失败返回 None（不让附图上不上而坏掉整次导出）。"""
    try:
        import cv2

        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes() if ok else None
    except Exception:  # noqa: BLE001
        return None


def _draw_box(overlay: Any, box: Any, color: tuple[int, int, int], label: str) -> None:
    """在 overlay 上画一个区域框 + 标签；坐标越界时裁到画面内。"""
    import cv2

    if not isinstance(box, dict):
        return
    left, top = int(box.get("left", 0)), int(box.get("top", 0))
    width, height = int(box.get("width", 0)), int(box.get("height", 0))
    if width <= 0 or height <= 0:
        return
    h, w = overlay.shape[:2]
    x1, y1 = max(0, min(left, w - 1)), max(0, min(top, h - 1))
    x2, y2 = max(0, min(left + width, w - 1)), max(0, min(top + height, h - 1))
    if x2 <= x1 or y2 <= y1:
        return
    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
    # 标签放框上方；贴到顶边时改放框内侧，避免被裁掉
    ty = y1 - 6 if y1 >= 14 else min(y1 + 16, h - 4)
    cv2.putText(overlay, label, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)


def _region_overlay(frame: Any) -> Any | None:
    """把 regions.yaml 的各检测区域 + 大冒险框画到 frame 的副本上。"""
    try:
        import numpy as np

        from zephie_rolling_on.data.adventure_frame import load_anchor
        from zephie_rolling_on.vision.adventure_calib import FRAME_HEIGHT, FRAME_WIDTH
        from zephie_rolling_on.vision.recognize import load_regions

        regions = load_regions()  # 已按当前锚点换算成客户区坐标
        overlay = np.array(frame, copy=True)

        for i, (key, label) in enumerate(_OVERLAY_BOXES):
            _draw_box(overlay, regions.get(key), _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)], label)
        for j, (key, label) in enumerate(_OVERLAY_SEARCH_BOXES, start=len(_OVERLAY_BOXES)):
            entry = regions.get(key)
            search = entry.get("search") if isinstance(entry, dict) else None
            _draw_box(overlay, search, _OVERLAY_COLORS[j % len(_OVERLAY_COLORS)], label)

        # 大冒险界面框：所有区域都相对它，画出来才能一眼看出整体偏没偏
        ax, ay = load_anchor()
        _draw_box(
            overlay,
            {"left": ax, "top": ay, "width": FRAME_WIDTH, "height": FRAME_HEIGHT},
            (0, 255, 0),
            f"anchor {ax},{ay}",
        )
        return overlay
    except Exception:  # noqa: BLE001
        return None


def _capture_images() -> list[tuple[str, bytes]]:
    """自检截到的那一帧 + 区域标注图，作为 zip 附件。

    依赖 ``self_check_report()`` 已经跑过（是它触发的真实截屏）。取不到就返回空，
    不影响其余材料的导出。
    """
    from zephie_rolling_on.diagnose import last_captured_frame, last_raw_captured_frame

    out: list[tuple[str, bytes]] = []
    original = last_raw_captured_frame()
    if original is not None and getattr(original, "size", 0) > 0:
        original_png = _encode_png(original)
        if original_png is not None:
            out.append(("截图-原始客户区.png", original_png))
    frame = last_captured_frame()
    if frame is None or getattr(frame, "size", 0) == 0:
        return out

    raw = _encode_png(frame)
    if raw is not None:
        out.append((SHOT_NAME, raw))
    overlay = _region_overlay(frame)
    if overlay is not None:
        marked = _encode_png(overlay)
        if marked is not None:
            out.append((REGION_NAME, marked))
    return out


def _bundle_name() -> str:
    # 文件名用 ASCII：QQ/微信传文件时不会被转义搞乱，且带版本+时间便于辨认
    stamp = _datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"ZephieLog_v{_app_version()}_{stamp}.zip"


def export_bundle(
    log_text: str,
    *,
    dest_dir: Path | None = None,
    bound_window: tuple[int, str] | None = None,
) -> Path:
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
        check_text = self_check_report(bound_window)
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

    # 截图要放在自检**之后**取：自检那一步才是真正截屏的地方
    payload.extend(_capture_images())

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
        self,
        master,
        log_text: str,
        *,
        bound_window: tuple[int, str] | None = None,
        on_log=None,
        on_closed=None,
        runtime=None,
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
        self._master = master
        self._bound_window = bound_window
        self._runtime = runtime
        self._result: Path | None = None
        self._error = ""
        self._closed = False
        self._stable_size: tuple[int, int] | None = None

        # 先还原最小化的主窗，否则本窗口按 master 坐标居中会落到屏幕外
        # （最小化时 rootx/rooty 为 -32000），而它随后 grab_set 会让界面不可点。
        from zephie_rolling_on.ui.ui_geometry import (
            ensure_master_visible,
            should_set_transient,
        )

        restored = ensure_master_visible(master)

        dialog = tk.Toplevel(master)
        # 测量内容需要 update_idletasks；先隐藏，避免 Tk 在定位前把窗口
        # 映射到默认的左上角，随后再跳到主窗口中央。
        dialog.withdraw()
        self._dialog = dialog
        dialog.title("导出日志")
        dialog.configure(bg=theme["bg"])
        # 自绘标题栏去掉系统拖动区域，弹窗位置由程序控制。
        dialog.overrideredirect(True)
        # 点击窗口空白处也应激活窗口，尤其是上层窗口遮住它后重新点回来的场景。
        dialog.bind("<Button-1>", self._activate, add="+")
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
        titlebar = tk.Frame(shell, bg=theme["bg"])
        titlebar.pack(fill="x", pady=(0, 8))
        tk.Label(
            titlebar,
            text="导出日志",
            anchor="w",
            bg=theme["bg"],
            fg=theme["text"],
            font=cute_font(9, bold=True),
        ).pack(side="left", fill="x", expand=True)
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
            wraplength=self._wraplength(master),
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
            wraplength=self._wraplength(master),
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

        tk.Button(
            titlebar,
            text="×",
            command=_close,
            relief="flat",
            borderwidth=0,
            bg=theme["bg"],
            fg=theme["text"],
            activebackground=theme["border"],
            activeforeground=theme["text"],
            font=cute_font(12),
            width=3,
            takefocus=True,
        ).pack(side="right")

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

        # 先按完成态测量最终布局，再恢复导出中的文案。这样整个过程始终是
        # 同一个、同一尺寸、同一位置的窗口，只切换内容，不会看起来像关闭后重开。
        self._title.config(text="日志已导出！")
        self._detail.config(
            text=f"已保存到程序目录：\n{_bundle_name()}\n\n"
            "把整个 zip 发给我们就行。\n"
            "（其中包含你的部分设置与窗口信息，仅用于排查问题）"
        )
        self._btn_reveal.pack(side="right")
        self._btn_close.pack(side="right", padx=(0, 8))
        self._btn_row.pack(fill="x", pady=(14, 2))
        dialog.update_idletasks()
        self._stable_size = (
            max(dialog.winfo_reqwidth(), 320),
            max(dialog.winfo_reqheight(), 160),
        )
        self._btn_reveal.pack_forget()
        self._btn_close.pack_forget()
        self._btn_row.pack_forget()
        self._title.config(text="Zephie正在整理日志…")
        self._detail.config(text="会同时生成一份环境自检报告，可能需要几秒。")

        dialog.protocol("WM_DELETE_WINDOW", _close)
        # 隐藏状态下先计算好最终尺寸和位置，再显示；导出中与完成态共享这一窗体。
        self._center(master)
        dialog.deiconify()
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        self._activate()
        # 置顶会吞掉先前请求的位置（实测落到 (0,0) 或 Windows 级联位置），
        # 所以这里再定位一次。
        self._center(master)

        threading.Thread(
            target=self._worker,
            args=(log_text, self._bound_window),
            name="log-export",
            daemon=True,
        ).start()
        dialog.after(80, self._poll)

    # -- 内部 --

    def _activate(self, _event=None) -> None:
        """将弹窗提到前面并聚焦；点击标题区或任意空白位置均可触发。"""
        if self._closed:
            return
        try:
            self._dialog.attributes("-topmost", True)
            self._dialog.lift()
            self._dialog.focus_force()
            self._dialog.after(
                250,
                lambda: self._dialog.attributes("-topmost", False)
                if not self._closed and self._dialog.winfo_exists()
                else None,
            )
        except Exception:
            pass

    def _center(self, master) -> None:
        """按主窗口中心定位，并约束在当前屏幕可见范围内。"""
        dialog = self._dialog
        try:
            dialog.update_idletasks()
            sw, sh = int(dialog.winfo_screenwidth()), int(dialog.winfo_screenheight())
            requested_width, requested_height = self._stable_size or (
                dialog.winfo_reqwidth(), dialog.winfo_reqheight()
            )
            width = min(requested_width, max(240, int(sw * 0.9)))
            height = min(requested_height, max(160, int(sh * 0.9)))
            try:
                mx, my = int(master.winfo_rootx()), int(master.winfo_rooty())
                mw, mh = int(master.winfo_width()), int(master.winfo_height())
                if mx < 0 or my < 0 or mw <= 1 or mh <= 1:
                    raise ValueError("主窗口尚无有效位置")
                x, y = mx + (mw - width) // 2, my + (mh - height) // 2
            except Exception:
                x, y = (sw - width) // 2, (sh - height) // 2
            x = max(0, min(x, max(0, sw - width)))
            y = max(0, min(y, max(0, sh - height)))
            dialog.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    @staticmethod
    def _wraplength(master) -> int:
        """根据屏幕宽度换行，适配低分辨率和不同系统缩放。"""
        try:
            usable = int(master.winfo_screenwidth() * 0.9) - 100
            return max(120, min(420, usable))
        except Exception:
            return 360

    def _refit(self, master) -> None:
        """在完成或失败文案更新后重新测量，避免内容被初始窗口尺寸裁切。"""
        try:
            wrap = self._wraplength(master)
            self._title.configure(wraplength=wrap)
            self._detail.configure(wraplength=wrap)
            self._dialog.update_idletasks()
            self._center(master)
        except Exception:
            pass

    def _worker(
        self, log_text: str, bound_window: tuple[int, str] | None = None
    ) -> None:
        try:
            from contextlib import nullcontext
            from zephie_rolling_on.app.runtime_state import use_runtime

            with use_runtime(self._runtime) if self._runtime is not None else nullcontext():
                path = export_bundle(log_text, bound_window=bound_window)
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
        self._refit(self._master)
        self._on_log(f"[导出日志] 已保存：{path}")

    def _show_error(self, reason: str) -> None:
        if self._closed:
            return
        self._error = reason
        self._title.config(text="导出失败了…", fg="#C9786A")
        self._detail.config(text=f"原因：{reason}")
        self._btn_close.pack(side="right")
        self._btn_row.pack(fill="x", pady=(14, 2))
        self._refit(self._master)
        self._on_log(f"[导出日志] 失败：{reason}")

    def _on_reveal(self) -> None:
        if self._result is not None:
            reveal(self._result)

