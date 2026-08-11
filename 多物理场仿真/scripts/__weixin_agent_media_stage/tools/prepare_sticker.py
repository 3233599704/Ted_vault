"""Normalize user-provided sticker art onto a padded transparent canvas."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path

from PIL import Image, ImageOps


ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def cache_key(source: Path, scale: float, canvas: int, fill: float) -> str:
    digest = hashlib.sha256()
    digest.update(source.read_bytes())
    digest.update(f"|v4-visual-fill|{scale:.4f}|{canvas}|{fill:.4f}".encode("ascii"))
    return digest.hexdigest()[:24]


def remove_edge_connected_white(image: Image.Image) -> tuple[Image.Image, bool]:
    """Make only near-white pixels connected to the outer edge transparent."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def near_white(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        return alpha > 0 and min(red, green, blue) >= 225 and max(red, green, blue) - min(red, green, blue) <= 24

    def add(x: int, y: int) -> None:
        index = y * width + x
        if visited[index] or not near_white(x, y):
            return
        visited[index] = 1
        queue.append((x, y))

    for x in range(width):
        add(x, 0)
        add(x, height - 1)
    for y in range(height):
        add(0, y)
        add(width - 1, y)

    removed = 0
    while queue:
        x, y = queue.popleft()
        red, green, blue, _alpha = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)
        removed += 1
        if x > 0:
            add(x - 1, y)
        if x + 1 < width:
            add(x + 1, y)
        if y > 0:
            add(x, y - 1)
        if y + 1 < height:
            add(x, y + 1)
    return rgba, removed > max(16, width * height * 0.01)


def normalize(source: Path, cache_dir: Path, scale: float, canvas: int, fill: float = 0.82) -> dict:
    output = cache_dir / f"{cache_key(source, scale, canvas, fill)}.png"
    if output.exists():
        with Image.open(output) as cached:
            return {"path": str(output), "width": cached.width, "height": cached.height, "cached": True}

    with Image.open(source) as opened:
        if getattr(opened, "is_animated", False):
            raise ValueError("暂不支持动态 GIF/WebP，请先转换为静态图片")
        image = ImageOps.exif_transpose(opened).convert("RGBA")
    image, white_background_removed = remove_edge_connected_white(image)
    alpha_box = image.getchannel("A").getbbox()
    if alpha_box:
        image = image.crop(alpha_box)
    output_target = max(64, round(canvas * scale))
    target = max(48, round(output_target * fill))
    ratio = min(target / image.width, target / image.height)
    width = max(1, round(image.width * ratio))
    height = max(1, round(image.height * ratio))
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    output_width = max(width, round(width / fill))
    output_height = max(height, round(height / fill))
    result = Image.new("RGBA", (output_width, output_height), (0, 0, 0, 0))
    result.alpha_composite(image, ((output_width - width) // 2, (output_height - height) // 2))
    cache_dir.mkdir(parents=True, exist_ok=True)
    result.save(output, "PNG", optimize=True)
    return {
        "path": str(output),
        "width": output_width,
        "height": output_height,
        "content_width": width,
        "content_height": height,
        "content_scale": scale,
        "visual_fill": fill,
        "layout": "proportional_padding",
        "white_background_removed": white_background_removed,
        "cached": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--scale", type=float, default=0.62)
    parser.add_argument("--fill", type=float, default=0.82)
    parser.add_argument("--canvas", type=int, default=512)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    source = Path(args.source).resolve()
    cache_dir = Path(args.cache).resolve()
    if not inside(root, source) or source.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise SystemExit("拒绝处理自定义表情目录以外的文件")
    if not source.is_file():
        raise SystemExit("自定义表情文件不存在")
    if not 0.25 <= args.scale <= 0.9:
        raise SystemExit("表情主体比例必须在 0.25 到 0.9 之间")
    if not 0.6 <= args.fill <= 0.98:
        raise SystemExit("表情视觉填充比例必须在 0.6 到 0.98 之间")
    if not 256 <= args.canvas <= 1024:
        raise SystemExit("表情画布尺寸必须在 256 到 1024 之间")
    print(json.dumps(normalize(source, cache_dir, args.scale, args.canvas, args.fill), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
