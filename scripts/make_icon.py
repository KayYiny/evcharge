# -*- coding: utf-8 -*-
"""生成图标资源（assets/icon.png / icon.ico / icon.icns）。

用法：
    python scripts/make_icon.py                # 缺什么生成什么（以 icon.png 为准）
    python scripts/make_icon.py --force        # 全部重新生成
    python scripts/make_icon.py --redraw       # 连 icon.png 也用内置闪电图案重画

说明：
- ``icon.png``   —— 运行时窗口图标（Flet ``page.window.icon``），256×256；
- ``icon.ico``   —— Windows EXE 图标（多尺寸，Pillow 直接写）；
- ``icon.icns``  —— macOS .app 图标（用系统自带 ``iconutil`` 打包 iconset）。

**已有自己的 logo 时**：把图片替换成 ``assets/icon.png``（建议 512×512 或 1024×1024
的方形 PNG），再跑一次本脚本即可，无需手工做 icns/ico。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "icon.png"
ICO_PATH = ASSETS / "icon.ico"
ICNS_PATH = ASSETS / "icon.icns"

DRAW_SIZE = 256
RADIUS = 56
BG = (31, 111, 235, 255)  # Material 蓝
BOLT = (255, 255, 255, 255)

# 闪电多边形（相对 0~100 的坐标系）
BOLT_POINTS = [(61, 4), (23, 55), (46, 55), (39, 96), (77, 43), (53, 43)]

# Windows ico 常用尺寸
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# macOS iconset 要求的文件名 → 像素尺寸
ICONSET_ENTRIES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def draw_default(path: Path = PNG_PATH) -> Path:
    """用内置的「蓝底 + 闪电」图案画一张 icon.png（无自有 logo 时使用）。"""
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (DRAW_SIZE, DRAW_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [(0, 0), (DRAW_SIZE - 1, DRAW_SIZE - 1)], radius=RADIUS, fill=BG
    )
    scale = DRAW_SIZE / 100.0
    inset = DRAW_SIZE * 0.16
    span = DRAW_SIZE - 2 * inset
    points = [
        (inset + x / 100.0 * span, inset + y / 100.0 * span) for x, y in BOLT_POINTS
    ]
    draw.polygon(points, fill=BOLT)
    image.save(path)
    print(f"已生成 {path.relative_to(ROOT)}")
    return path


def _square(image: Image.Image, size: int) -> Image.Image:
    """缩放到正方形（保持比例，居中补透明），保证图标不变形。"""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    copy = image.copy()
    copy.thumbnail((size, size), Image.LANCZOS)
    canvas.paste(
        copy,
        ((size - copy.width) // 2, (size - copy.height) // 2),
        copy if copy.mode == "RGBA" else None,
    )
    return canvas


def build_ico(source: Image.Image) -> Path:
    """多尺寸 .ico（Windows）。"""
    largest = max(size for size, _ in ICO_SIZES)
    _square(source, largest).save(ICO_PATH, format="ICO", sizes=ICO_SIZES)
    print(f"已生成 {ICO_PATH.relative_to(ROOT)}（{len(ICO_SIZES)} 种尺寸）")
    return ICO_PATH


def build_icns(source: Image.Image) -> Path | None:
    """用 macOS 自带 iconutil 打包 .icns；非 macOS 或缺工具时跳过。"""
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("跳过 icon.icns：当前系统没有 iconutil（仅 macOS 需要）")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for name, size in ICONSET_ENTRIES.items():
            _square(source, size).save(iconset / name)
        result = subprocess.run(
            [iconutil, "-c", "icns", str(iconset), "-o", str(ICNS_PATH)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"生成 icon.icns 失败：{result.stderr.strip()[:200]}")
            return None
    print(f"已生成 {ICNS_PATH.relative_to(ROOT)}")
    return ICNS_PATH


def ensure_icons(force: bool = False, redraw: bool = False) -> dict:
    """按需生成三份图标资源，返回 ``{后缀: 路径或 None}``。"""
    ASSETS.mkdir(parents=True, exist_ok=True)

    if redraw or not PNG_PATH.exists():
        draw_default(PNG_PATH)

    source = Image.open(PNG_PATH).convert("RGBA")
    if source.width < 256 or source.height < 256:
        print(f"提示：icon.png 只有 {source.width}×{source.height}，放大后可能偏模糊；"
              "建议换成 512×512 以上的方形图。")

    result = {".png": PNG_PATH}
    if force or not ICO_PATH.exists():
        result[".ico"] = build_ico(source)
    else:
        result[".ico"] = ICO_PATH
    if force or not ICNS_PATH.exists():
        result[".icns"] = build_icns(source)
    else:
        result[".icns"] = ICNS_PATH
    return result


def main() -> None:
    force = "--force" in sys.argv
    redraw = "--redraw" in sys.argv
    icons = ensure_icons(force=force, redraw=redraw)
    print("当前图标资源：")
    for suffix, path in icons.items():
        if path and Path(path).exists():
            print(f"  {suffix:6} {Path(path).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
