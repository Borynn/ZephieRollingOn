"""把非 100% 缩放的画面归一化回 100% 标定空间。

为什么需要
----------
``config/regions.yaml``、``config/click_targets.yaml`` 以及 ``assets/`` 下所有模板
都是按 **100% 缩放下的物理像素** 标定的。系统缩放一旦大于 100%，游戏客户区在
物理像素上被整体放大，模板尺寸与区域偏移就全部对不上，识别必然失败。

原做法是启动时直接拦掉非 100% 的用户。现在改为：截到画面后先在内存里压回
100% 尺寸，**后续流程一律不必改**——模板库不动，区域配置不动，OCR 不动，只在
入口做一次等比缩放。这就是「以不变应万变」。

用法::

    frame = normalize_frame_scale(frame, scale_percent)   # scale_percent >= 100

坐标系边界（重要，调用方必须知道）
------------------------------------
归一化之后，**凡是从画面里量出来的坐标都落在 100% 空间**：模板命中的中心
（``ConfirmHit.center_x/center_y``、``RollKindHit.center_*``）、标定锚点、以及由
锚点偏移出来的区域；``regions.yaml`` / ``click_targets.yaml`` 里的配置坐标同样是
100% 空间。

而 ``executor.win32_click`` 的点击接口要的是**真实客户区坐标**。还原集中在该模块
的**三个公开入口**（点击 / 拖拽 / 移光标）里做，而不是散落各调用点；本模块只负责
图像，不做这个还原。

插值选择
--------
缩小用 ``cv2.INTER_AREA``：它按像素区域重采样，缩小时顺带抗锯齿，能把 Windows
强行放大产生的虚边重新收锐，最贴合原有模板。

放大（缩放 < 100%）用 ``cv2.INTER_LINEAR``：``INTER_AREA`` 在放大时会退化成
最近邻、产生块状伪影。放大不是本项目支持的路径（启动门禁会拦掉 < 100%），
这里只为让函数对任意输入都是「正确的」，而不是静默给出错的结果。

备注：曾试过统一用 ``INTER_NEAREST``，希望「不做加权平均」能保住细笔画的峰值
亮度（供下游的「画面是否变灰」判定使用）。实测**反而更糊**，已回退——
降采样时最近邻只是丢弃像素，视觉上比区域重采样差得多。变灰判定的阈值问题另议，
不再以牺牲画质为代价。
"""

from __future__ import annotations

import cv2
import numpy as np

# 标定基准。所有模板与区域配置都按这个缩放下的物理像素制作。
BASELINE_SCALE_PERCENT = 100

# 本次运行实际使用的画面缩放，由启动门禁在检测后写入一次。
#
# 为什么用模块级缓存、而不是每次截图都去问 ``ui.system_scale``：
#   1. 依赖方向：``system_scale`` 在 ``ui/`` 下，而调用方在 ``vision/``（截图）与
#      ``executor/``（点击）。反向 import ``ui`` 会把层次倒过来；改由启动流程把值
#      注入，方向就保持正向。
#   2. 开销：``system_scale_percent()`` 每次要问 4 个 Win32 API，而截图在轮询里
#      被反复调用。
#   3. 语义：缩放不会在一次运行中途变化（启动门禁已处理）。
_frame_scale_percent: int = BASELINE_SCALE_PERCENT


def set_frame_scale_percent(scale_percent: int | float | None) -> None:
    """记录本次运行的画面缩放（由启动门禁调用一次）。

    值不可用时保持基准 100，即「不做任何缩放」——猜一个错误的倍率比不缩放更糟，
    会把整幅画面和所有点击坐标一起算偏。
    """
    global _frame_scale_percent
    try:
        value = int(round(float(scale_percent)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = BASELINE_SCALE_PERCENT
    _frame_scale_percent = value if value > 0 else BASELINE_SCALE_PERCENT


def frame_scale_percent() -> int:
    """本次运行使用的画面缩放百分比；未设置时为 100（即不做缩放）。"""
    return _frame_scale_percent


def baseline_point(x: int, y: int) -> tuple[int, int]:
    """把**物理**客户区坐标点换算到 100% 标定空间。

    与 :func:`baseline_client_size` 同一比例；区别是点不做 ``max(1, …)``——原点
    坐标 0 是合法的。
    """
    scale = int(_frame_scale_percent)
    if scale <= BASELINE_SCALE_PERCENT:
        return int(x), int(y)
    factor = BASELINE_SCALE_PERCENT / float(scale)
    return int(round(int(x) * factor)), int(round(int(y) * factor))


def baseline_client_size(width: int, height: int) -> tuple[int, int]:
    """把**物理**客户区尺寸换算成 100% 标定空间的尺寸。

    为什么需要：游戏的分辨率（例如「1366×768 时跳过动画」这类判断）是按 **100%
    空间的逻辑分辨率** 描述的，而 ``win32_window._client_size_screen_pixels`` 返回的
    是**物理像素**。缩放大于 100% 时两者不相等——125% 下一块 1366×768 的逻辑画面
    会报成 1708×960——所以凡是「按分辨率决定行为」的判断都必须先换算，否则在缩放
    下**永远不匹配**。

    注意与截图归一化的区别：归一化只影响**画面**，凡是读 ``frame.shape`` 的地方
    （模板、区域、OCR）都已经自动落在 100% 空间；而读 Win32 客户区尺寸的地方不会，
    必须显式过这个函数。
    """
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
