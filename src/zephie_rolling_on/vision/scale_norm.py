"""根据用户选择的游戏分辨率决定是否归一化，并缓存本轮点击倍率。

客户区截图与选择尺寸匹配时保留原图；只有与选择尺寸乘显示器缩放匹配时，
才缩小一次。宽高各容差 5px。测试或启动入口读取一次显示器缩放，运行中
复用首次确定的处理方式；识别坐标到物理客户区的换算在点击入口统一完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

# 标定基准。所有模板与区域配置都按这个缩放下的物理像素制作。
BASELINE_SCALE_PERCENT = 100

# 显示器倍率由 UI 入口注入；截图流程不读取 DPI。实际图像倍率由尺寸判定。
GAME_RESOLUTIONS = ((1920, 1080), (1600, 900), (1366, 768))
RESOLUTION_TOLERANCE = 5
RESOLUTION_MISMATCH = "请检查游戏分辨率设置是否与脚本界面选择的选项一致"


@dataclass
class CaptureScaleState:
    """每个界面独立持有；显示器倍率与实际图像倍率分别记录。"""

    resolution: tuple[int, int] | None = None
    selection_required: bool = False
    monitor_percent: int = 100
    frame_percent: int = 100
    ready: bool = False
    click_factors: tuple[float, float] | None = None
    input_size: tuple[int, int] | None = None
    output_size: tuple[int, int] | None = None
    raw_frame: object = field(default=None, repr=False, compare=False)
    report: str = ""


_default_state = CaptureScaleState()


def capture_scale_state() -> CaptureScaleState:
    from zephie_rolling_on.app.runtime_state import get_runtime

    runtime = get_runtime()
    return runtime.capture_scale if runtime is not None else _default_state


def configure_game_resolution(resolution: tuple[int, int] | None) -> None:
    """选项变化或开始新一轮测试时重置判定；不读取显示器 DPI。"""
    if resolution is not None and resolution not in GAME_RESOLUTIONS:
        raise ValueError("不支持的游戏分辨率")
    state = capture_scale_state()
    state.resolution = resolution
    state.selection_required = True
    state.frame_percent = 100
    state.ready = False
    state.click_factors = None
    state.input_size = state.output_size = None
    state.raw_frame = None
    state.report = ""


def set_frame_scale_percent(scale_percent: int | float | None) -> None:
    """缓存入口读取的显示器倍率；配置分辨率后由首次截图决定图像倍率。"""
    try:
        value = int(round(float(scale_percent)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = BASELINE_SCALE_PERCENT
    state = capture_scale_state()
    state.monitor_percent = value if value > 0 else BASELINE_SCALE_PERCENT
    state.frame_percent = state.monitor_percent
    state.ready = False
    state.click_factors = None


def frame_scale_percent() -> int:
    """本次运行实际使用的图像倍率，可能与显示器倍率不同。"""
    return capture_scale_state().frame_percent


def click_scale_factors() -> tuple[float, float]:
    state = capture_scale_state()
    factor = state.frame_percent / 100.0
    return state.click_factors or (factor, factor)


def normalize_window_frame(
    frame: np.ndarray | None,
    client_size: tuple[int, int] | None = None,
) -> np.ndarray | None:
    """先匹配用户选择的尺寸，再决定本轮是否归一化；宽高分别容差 5px。"""
    state = capture_scale_state()
    state.raw_frame = frame
    if frame is None or frame.size == 0:
        return frame
    h, w = frame.shape[:2]
    state.input_size = (w, h)
    if state.selection_required:
        if state.resolution is None:
            state.report = "请选择游戏分辨率"
            raise ValueError(state.report)
        rw, rh = state.resolution
        scaled = (
            int(round(rw * state.monitor_percent / 100.0)),
            int(round(rh * state.monitor_percent / 100.0)),
        )

        def matches(size: tuple[int, int]) -> bool:
            return (
                abs(w - size[0]) <= RESOLUTION_TOLERANCE
                and abs(h - size[1]) <= RESOLUTION_TOLERANCE
            )

        # 本轮首次判断优先保留原图。运行期间不重新选倍率，只验证尺寸仍有效。
        if state.ready:
            valid = matches(state.resolution if state.frame_percent == 100 else scaled)
        elif matches(state.resolution):
            state.frame_percent = 100
            valid = True
        elif matches(scaled):
            state.frame_percent = state.monitor_percent
            valid = True
        else:
            valid = False
        if not valid:
            state.report = (
                f"{RESOLUTION_MISMATCH}（已选 {rw}×{rh}，截图 {w}×{h}，"
                f"显示器缩放 {state.monitor_percent}%，缩放后应为 {scaled[0]}×{scaled[1]}，容差 ±5px）"
            )
            raise ValueError(state.report)

    result = normalize_frame_scale(frame, state.frame_percent)
    oh, ow = result.shape[:2]
    state.output_size = (ow, oh)
    if client_size is not None:
        state.click_factors = (client_size[0] / ow, client_size[1] / oh)
    state.ready = True
    choice = (
        "未指定" if state.resolution is None
        else f"{state.resolution[0]}×{state.resolution[1]}"
    )
    action = "原图直接识别" if state.frame_percent == 100 else f"按 {state.frame_percent}% 归一化"
    state.report = (
        f"已选 {choice}，客户区截图 {w}×{h}，显示器缩放 {state.monitor_percent}%；"
        f"{action}，识别图 {ow}×{oh}"
    )
    return result


def baseline_point(x: int, y: int) -> tuple[int, int]:
    """把**物理**客户区坐标点换算到 100% 标定空间。

    与 :func:`baseline_client_size` 同一比例；区别是点不做 ``max(1, …)``——原点
    坐标 0 是合法的。
    """
    fx, fy = click_scale_factors()
    return int(round(int(x) / fx)), int(round(int(y) / fy))


def baseline_client_size(width: int, height: int) -> tuple[int, int]:
    """按本轮实际坐标映射，把物理客户区尺寸换算成识别图尺寸。"""
    w, h = baseline_point(width, height)
    return max(1, w), max(1, h)


def normalize_frame_scale(
    frame: np.ndarray | None,
    scale_percent: int | float | None,
) -> np.ndarray | None:
    """把 ``frame`` 从 ``scale_percent`` 缩放归一化回 100% 标定空间。

    参数
    ----
    frame
        原始截图（BGR 或灰度 numpy 数组）。``None`` 或空数组原样返回。
    scale_percent
        当前系统缩放百分比。契约上 **>= 100**（启动门禁保证）。

        缩放在此范围内**且不是恰好 100** 时才做缩放；恰好 100、或值不可用
        （``None`` / ``<= 0``）都原样返回——前者本来就不需要处理，后者无法判断
        应当怎么缩放，**宁可不动作也不要猜**。

    返回
    ----
    归一化后的新数组；无需处理时返回**传入的同一个对象**（不复制）。
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return frame

    try:
        scale = float(scale_percent) if scale_percent is not None else 0.0
    except (TypeError, ValueError):
        scale = 0.0
    if scale <= 0 or scale == float(BASELINE_SCALE_PERCENT):
        return frame

    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        return frame

    factor = float(BASELINE_SCALE_PERCENT) / scale
    target_w = max(1, int(round(width * factor)))
    target_h = max(1, int(round(height * factor)))
    if target_w == width and target_h == height:
        # 四舍五入后尺寸没变（例如 101% 的窄图），缩放没有意义
        return frame

    interpolation = (
        cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR
    )
    return cv2.resize(frame, (target_w, target_h), interpolation=interpolation)
