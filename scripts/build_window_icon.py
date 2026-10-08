#!/usr/bin/env python
"""从原始立绘生成窗口 / 任务栏图标（多分辨率 .ico + 256px PNG）。

为什么要单独一个脚本
--------------------
之前的 .ico 是临时一次性生成的，源图是**方图且不含 alpha**，于是：

* 圆角外的浅紫背景被一起画进图标，主体没占满画布；
* 运行时 Tk 的 ``wm iconbitmap`` 请求小图标尺寸时，只要该尺寸在 .ico 里只有
  PNG 帧可满足就会加载失败并回退成大图标（实测 32x32 被硬缩进标题栏 16x16
  —— 这才是"图标很糊"的根因）。
  槽位问题修在 ``ui/win_taskbar.py``；本脚本只负责把**素材**做对。

主体尺寸：为什么是"满铺"而不是"完整装进"
----------------------------------------
任务栏 / 桌面图标画布是**正方形**，而白色圆边以内的画面是**竖长条**
（实测 284x316，宽高比 0.90）。两种做法：

* **完整装进**正方形 —— 主体只占 90% 宽度，左右各空 5%，图标看着比邻座小一圈；
* **满铺**正方形 —— 短边定尺寸，长边居中裁掉（上下各 16px），主体铺满画布。

选后者。裁掉的 32px 里，上方是蝴蝶结顶端、下方是衣领下缘；在 16~48px 的真实
图标尺寸下（1px ≈ 母版 8.9px）这点裁切不可见，而"看起来小一圈"是每台机器上
都能看到的。另外原图那圈 **10px 白环也一并丢掉** —— 它是原图的装饰边框，留着
会让主体再缩 6.6%，也正是不像邻座那个"满铺圆角方块"的原因。

圆角沿用**内缘**半径（外缘 60.5 − 白环 10 = 50.5），蒙版与原画内缘相切，不会
多切画面。

产物
----
``assets/ui/icon_master.png``        正方形 RGBA 母版（满铺、圆角外透明）
``assets/ui/zehpie_window_icon.png`` 256x256 RGBA
``assets/ui/zehpie_window_icon.ico`` 16/20/24/28/32/40/48/56/64/96/128/256

为什么是这些尺寸（"多分辨图层"）
--------------------------------
Windows 需要"标题栏 = SM_CXSMICON、任务栏 = SM_CXICON"，两者都随显示缩放变化：
100% 是 16/32，125% 是 20/40，150% 是 24/48，175% 是 28/56，200% 是 32/64。
上面那串尺寸正好覆盖这些档位，Windows 才能取到**原生像素**而不是缩放插值出来的
糊图。

帧编码约定
----------
256 用 PNG 压缩（省体积，Vista+ 支持），其余走 32bpp DIB + 全 0 AND 掩码
（小尺寸最保险的写法：alpha 通道生效，掩码只作占位）。**不要全改成 PNG** ——
Tk 的小图标加载路径读不了 PNG 帧（见上文）。

用法
----
    python scripts/build_window_icon.py --source <原始立绘>   # 重切母版并重出图标
    python scripts/build_window_icon.py                       # 由母版重出图标
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "assets" / "ui"
MASTER_PATH = UI_DIR / "icon_master.png"
PNG_PATH = UI_DIR / "zehpie_window_icon.png"
ICO_PATH = UI_DIR / "zehpie_window_icon.ico"

PNG_SIZE = 256
# 覆盖 100%/125%/150%/175%/200% 缩放下的 SM_CXSMICON / SM_CXICON
ICO_SIZES = (16, 20, 24, 28, 32, 40, 48, 56, 64, 96, 128, 256)
PNG_FRAME_MIN = 256  # >= 此尺寸用 PNG 帧，其余用 DIB 帧

NEAR_WHITE = 228  # 判定白色边框的阈值（三通道同时超过）
MASK_SUPERSAMPLE = 4  # 圆角蒙版的超采样倍数（消除锯齿）


def _detect_card(rgb: np.ndarray) -> tuple[int, int, int, int, float]:
    """定位白色圆角边框环，返回 (x, y, w, h, 圆角半径)。

    边框是源图里最大的近白连通域。半径用两个独立测量取平均：

    * 最上一行里白像素的起点 x —— 正好是圆弧的右端（= 左边 + 半径）；
    * 最左一列里白像素的起点 y —— 正好是圆弧的下端（= 上边 + 半径）。
    """
    import cv2

    near_white = _near_white_mask(rgb)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        near_white.astype(np.uint8), connectivity=8
    )
    if count <= 1:
        raise SystemExit("找不到白色圆角边框：源图里没有任何近白连通域")

    idx = 1 + int(np.argmax(stats[1:, 4]))
    x, y, w, h = (int(v) for v in stats[idx, :4])
    comp = labels == idx
    radius = (
        int(np.flatnonzero(comp[y])[0]) - x + int(np.flatnonzero(comp[:, x])[0]) - y
    ) / 2.0
    return x, y, w, h, radius


def _near_white_mask(rgb: np.ndarray) -> np.ndarray:
    return (
        (rgb[:, :, 0] > NEAR_WHITE)
        & (rgb[:, :, 1] > NEAR_WHITE)
        & (rgb[:, :, 2] > NEAR_WHITE)
    )


def _detect_ring(rgb: np.ndarray, x: int, y: int, w: int, h: int) -> int:
    """白环厚度：中点行/列上、从边框四边往里的连续近白游程。

    取四个游程的**中位数**：画面里白色的部分（浅色头发、白衬衫）紧贴白环内侧，
    会让某一侧的游程一直数下去；中位数对这一个方向的异常既不敏感，又不至于像
    取最小值那样被单像素杂色带偏。
    """
    nw = _near_white_mask(rgb)
    row = nw[y + h // 2]
    col = nw[:, x + w // 2]

    def run(seq: np.ndarray, start: int, step: int) -> int:
        n, i = 0, start
        while 0 <= i < len(seq) and seq[i]:
            n += 1
            i += step
        return n

    runs = (
        run(row, x, 1),
        run(row, x + w - 1, -1),
        run(col, y, 1),
        run(col, y + h - 1, -1),
    )
    return int(np.median(runs))


def _round_corners(card: Image.Image, radius: float) -> Image.Image:
    """裁成圆角：圆角外全部透明。蒙版先按 4x 超采样画，再降采样取抗锯齿边缘。"""
    card = card.convert("RGBA")
    w, h = card.size
    ss = MASK_SUPERSAMPLE
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, fill=255
    )
    card.putalpha(mask.resize((w, h), Image.LANCZOS))
    return card


def build_master(source: Path) -> Image.Image:
    """把原始立绘切成**满铺**的正方形 RGBA 母版（只保留白环以内的画面）。"""
    im = Image.open(source).convert("RGB")
    rgb = np.asarray(im).astype(np.int16)
    x, y, w, h, radius = _detect_card(rgb)
    ring = _detect_ring(rgb, x, y, w, h)
    print(f"  卡片 bbox = ({x}, {y}) {w}x{h}，外缘圆角半径 ≈ {radius:.1f}，白环厚 ≈ {ring}px")

    # 只取白环以内的画面；白环本身丢掉
    inner = im.crop((x + ring, y + ring, x + w - ring, y + h - ring))
    iw, ih = inner.size
    if ring and iw < PNG_FRAME_MIN:
        raise SystemExit(f"去掉白环后只剩 {iw}x{ih}，不足以原生渲染 {PNG_FRAME_MIN}px 帧")

    # 满铺正方形：短边定尺寸，长边居中裁掉（纯裁切，不重采样，母版保持原始像素）
    side = min(iw, ih)
    cx, cy = (iw - side) // 2, (ih - side) // 2
    square = inner.crop((cx, cy, cx + side, cy + side))
    print(
        f"  白环以内 {iw}x{ih} -> 满铺正方形 {side}x{side}"
        f"（横向裁 {cx * 2}px，纵向裁 {cy * 2}px）"
    )

    # 内缘圆角与外缘同心；母版只裁不缩，半径照搬即可
    inner_radius = max(0.0, radius - ring)
    return _round_corners(square, inner_radius)


def _encode_dib(img: Image.Image) -> bytes:
    """32bpp DIB 帧：BITMAPINFOHEADER + 自下而上的 BGRA + 全 0 AND 掩码。"""
    size = img.size[0]
    buf = io.BytesIO()
    img.save(buf, format="DIB")
    data = bytearray(buf.getvalue())

    bi_size = struct.unpack_from("<I", data, 0)[0]
    if bi_size != 40 or len(data) != 40 + size * size * 4:
        raise SystemExit(f"DIB 头布局与预期不符（biSize={bi_size}），需更新 _encode_dib")

    struct.pack_into("<i", data, 8, size * 2)  # 图标约定：biHeight = 2*高度
    data += b"\x00" * (((size + 31) // 32) * 4 * size)  # AND 掩码：全 0，实际看 alpha
    return bytes(data)


def _encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_ico(frames: list[tuple[int, Image.Image]], out: Path) -> None:
    count = len(frames)
    offset = 6 + 16 * count
    entries = bytearray()
    blobs = bytearray()
    for size, img in frames:
        blob = _encode_png(img) if size >= PNG_FRAME_MIN else _encode_dib(img)
        dim = 0 if size >= 256 else size  # 0 表示 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    out.write_bytes(struct.pack("<HHH", 0, 1, count) + bytes(entries) + bytes(blobs))


def _verify_ico(path: Path) -> None:
    """回读校验：目录项数量、尺寸、格式都要和写入时一致。"""
    raw = path.read_bytes()
    _, kind, count = struct.unpack_from("<HHH", raw, 0)
    if kind != 1 or count != len(ICO_SIZES):
        raise SystemExit(f"回读失败：type={kind} entries={count}")
    seen = []
    for i in range(count):
        w, h, _c, _r, _p, bpp, size, off = struct.unpack_from("<BBBBHHII", raw, 6 + i * 16)
        fmt = "PNG" if raw[off : off + 8] == b"\x89PNG\r\n\x1a\n" else "DIB"
        seen.append(f"{w or 256}x{h or 256}:{fmt}/{bpp}bpp")
    print("  回读：" + "  ".join(seen))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成窗口 / 任务栏图标")
    parser.add_argument(
        "--source",
        type=Path,
        help="原始立绘；给了就重切圆角母版（不提交进仓库，母版才是素材）",
    )
    args = parser.parse_args()

    if args.source is not None:
        if not args.source.is_file():
            raise SystemExit(f"找不到源图：{args.source}")
        print(f"[1/3] 重切母版 <- {args.source.name}")
        UI_DIR.mkdir(parents=True, exist_ok=True)
        build_master(args.source).save(MASTER_PATH)

    if not MASTER_PATH.is_file():
        raise SystemExit(f"缺少母版 {MASTER_PATH}；首次生成请带 --source")

    master = Image.open(MASTER_PATH).convert("RGBA")
    if master.size[0] != master.size[1]:
        raise SystemExit(f"母版必须是正方形，当前为 {master.size}")

    print(f"[2/3] 母版 {master.size[0]}x{master.size[0]} -> {len(ICO_SIZES)} 帧")
    frames = [(s, master.resize((s, s), Image.LANCZOS)) for s in ICO_SIZES]
    write_ico(frames, ICO_PATH)
    master.resize((PNG_SIZE, PNG_SIZE), Image.LANCZOS).save(PNG_PATH)

    print(f"[3/3] {ICO_PATH.name} {ICO_PATH.stat().st_size} B")
    _verify_ico(ICO_PATH)
    print(f"      {PNG_PATH.name} {PNG_PATH.stat().st_size} B ({PNG_SIZE}x{PNG_SIZE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
