"""Generate the Modbus Studio application icons from the master artwork.

The master image is kept separate so normal builds can regenerate the PNG and
multi-size Windows ICO without replacing the artwork with a procedural icon.
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - friendly CLI failure
    raise SystemExit("缺少 Pillow，请先运行：python -m pip install Pillow") from exc


ROOT = Path(__file__).resolve().parent
SOURCE_PATH = ROOT / "resources" / "modbus_studio_source.png"
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]


def create_icon() -> Image.Image:
    """Load and normalize the square master artwork."""
    if not SOURCE_PATH.is_file():
        raise SystemExit(f"缺少图标源文件：{SOURCE_PATH}")

    with Image.open(SOURCE_PATH) as source:
        image = source.convert("RGBA")
    if image.width != image.height:
        raise SystemExit(f"图标源文件必须是正方形：{SOURCE_PATH}")
    return image


def generate(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    master = create_icon()
    png_path = output_dir / "modbus_studio.png"
    ico_path = output_dir / "modbus_studio.ico"
    master.resize((512, 512), Image.Resampling.LANCZOS).save(png_path, "PNG", optimize=True)
    master.save(ico_path, format="ICO", sizes=ICO_SIZES)
    return png_path, ico_path


def main() -> int:
    parser = argparse.ArgumentParser(description="从主图生成 Modbus Studio 的 PNG 和 Windows ICO 图标")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "resources",
        help="图标输出目录（默认：resources）",
    )
    args = parser.parse_args()
    png_path, ico_path = generate(args.output_dir.resolve())
    print(f"已生成 PNG：{png_path}")
    print(f"已生成 ICO：{ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
