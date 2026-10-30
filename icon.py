"""Generate the Modbus Studio application icon.

The artwork is rendered at high resolution and downsampled by Pillow into a
multi-size Windows ICO, so it stays crisp in Explorer, the taskbar and titlebar.
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError as exc:  # pragma: no cover - friendly CLI failure
    raise SystemExit("缺少 Pillow，请先运行：python -m pip install Pillow") from exc


CANVAS = 1024
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]


def _mix(first: tuple[int, int, int], second: tuple[int, int, int], amount: float) -> tuple[int, int, int, int]:
    return tuple(round(a + (b - a) * amount) for a, b in zip(first, second)) + (255,)


def create_icon() -> Image.Image:
    """Return a 1024px transparent master image."""
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    # A restrained shadow gives the icon depth without muddying 16px variants.
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((92, 104, 932, 944), radius=230, fill=(5, 25, 47, 115))
    shadow = shadow.filter(ImageFilter.GaussianBlur(32))
    image.alpha_composite(shadow)

    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((82, 70, 942, 930), radius=224, fill=255)
    gradient = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    top, bottom = (13, 116, 188), (8, 39, 83)
    for y in range(70, 931):
        t = (y - 70) / 860
        gradient_draw.line((82, y, 942, y), fill=_mix(top, bottom, t), width=1)
    gradient.putalpha(mask)
    image.alpha_composite(gradient)

    draw = ImageDraw.Draw(image)
    # Soft inner border and a quiet engineering grid.
    draw.rounded_rectangle((98, 86, 926, 914), radius=208, outline=(157, 225, 255, 110), width=8)
    for offset in (292, 512, 732):
        draw.line((155, offset, 869, offset), fill=(111, 191, 225, 35), width=4)
        draw.line((offset, 145, offset, 855), fill=(111, 191, 225, 28), width=4)

    # The M is also a connected Modbus signal path. Rounded circuit nodes remain
    # recognizable at very small Windows icon sizes.
    path = [(226, 690), (226, 340), (512, 610), (798, 340), (798, 690)]
    draw.line(path, fill=(239, 249, 255, 255), width=82, joint="curve")
    for x, y in path:
        draw.ellipse((x - 50, y - 50, x + 50, y + 50), fill=(239, 249, 255, 255))
        draw.ellipse((x - 27, y - 27, x + 27, y + 27), fill=(21, 205, 174, 255))

    # Three status indicators subtly reference RX, link and TX.
    for x, color in ((418, (64, 210, 244, 255)), (512, (38, 224, 177, 255)), (606, (64, 210, 244, 255))):
        draw.ellipse((x - 21, 790, x + 21, 832), fill=color)
    return image


def generate(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    master = create_icon()
    png_path = output_dir / "modbus_studio.png"
    ico_path = output_dir / "modbus_studio.ico"
    master.resize((512, 512), Image.Resampling.LANCZOS).save(png_path, "PNG", optimize=True)
    master.save(ico_path, format="ICO", sizes=ICO_SIZES, bitmap_format="bmp")
    return png_path, ico_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Modbus Studio 的 PNG 和 Windows ICO 图标")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "resources",
        help="图标输出目录（默认：resources）",
    )
    args = parser.parse_args()
    png_path, ico_path = generate(args.output_dir.resolve())
    print(f"已生成 PNG：{png_path}")
    print(f"已生成 ICO：{ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
