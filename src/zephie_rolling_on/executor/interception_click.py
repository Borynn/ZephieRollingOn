"""可选：Interception 驱动级鼠标/键盘（需单独安装驱动 + interception-python）。"""

from __future__ import annotations

from collections.abc import Callable


def interception_available() -> bool:
    try:
        import interception  # noqa: F401

        return True
    except ImportError:
        return False


def click_screen_interception(
    sx: int,
    sy: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """
    经 Interception 驱动发送鼠标移动与左键。
    返回 (成功, 失败原因)。
    """
    log = on_log or (lambda _m: None)
    try:
        import interception
    except ImportError:
        msg = (
            "未安装 interception-python。"
            "请执行: pip install interception-python"
        )
        return False, msg

    try:
        interception.auto_capture_devices()
        interception.move_to(int(sx), int(sy))
        interception.click(int(sx), int(sy), button="left")
    except Exception as exc:
        hint = (
            "若提示驱动相关错误，请先安装 Interception 驱动："
            "http://www.oblita.com/interception （管理员安装，必要时重启）"
        )
        log(f"[点击] interception 失败: {exc}")
        log(f"[点击] {hint}")
        return False, str(exc)

    return True, ""


def press_key_interception(
    key: str,
    *,
    on_log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """
    经 Interception 驱动发送按键（与鼠标点击同一驱动）。
    返回 (成功, 失败原因)。
    """
    log = on_log or (lambda _m: None)
    try:
        import interception
    except ImportError:
        msg = (
            "未安装 interception-python。"
            "请执行: pip install interception-python"
        )
        return False, msg

    try:
        interception.auto_capture_devices()
        interception.press(str(key).lower())
    except Exception as exc:
        hint = (
            "若提示驱动相关错误，请先安装 Interception 驱动："
            "http://www.oblita.com/interception （管理员安装，必要时重启）"
        )
        log(f"[键盘] interception 失败: {exc}")
        log(f"[键盘] {hint}")
        return False, str(exc)

    return True, ""


def press_f12_interception(
    *,
    on_log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    return press_key_interception("f12", on_log=on_log)
