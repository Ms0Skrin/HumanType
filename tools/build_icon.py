"""Renders assets/app.svg into icon.png and a multi-size icon.ico, plus a preview sheet with the tray glyph."""

import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def render(svg: bytes, size: int) -> Image.Image:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    QSvgRenderer(svg).render(painter, QRectF(0, 0, size, size))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def main() -> None:
    QGuiApplication(sys.argv)
    app_svg = (ASSETS / "app.svg").read_bytes()
    tray_svg = (ASSETS / "tray.svg").read_text(encoding="utf-8")
    frames = [render(app_svg, s) for s in SIZES]
    render(app_svg, 512).save(ASSETS / "icon.png")
    frames[-1].save(ASSETS / "icon.ico", format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])

    # Preview: app icon sizes, then the tray glyph as the dark and the light taskbar show it.
    light_tray = tray_svg.replace('stroke="white"', 'stroke="#0F172A"').replace('stroke="#38BDF8"', 'stroke="#0284C7"')
    width = sum(SIZES) + 20 * len(SIZES) + 260
    sheet = Image.new("RGBA", (width, 2 * 296), (243, 244, 246, 255))
    sheet.paste(Image.new("RGBA", (width, 296), (24, 24, 28, 255)), (0, 296))
    for row, tray in ((0, light_tray), (1, tray_svg)):
        x = 20
        for frame in frames:
            sheet.alpha_composite(frame, (x, row * 296 + 148 - frame.height // 2))
            x += frame.width + 20
        for i, size in enumerate((16, 24, 32, 64)):
            glyph = render(tray.encode("utf-8"), size)
            sheet.alpha_composite(glyph, (x + 10, row * 296 + 30 + i * 62 + (64 - size) // 2))
    sheet.save(ASSETS / "preview.png")
    print("written:", *(p.name for p in ASSETS.iterdir()))


if __name__ == "__main__":
    main()
