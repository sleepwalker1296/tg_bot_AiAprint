"""
Скрипт для создания простого логотипа-водяного знака.
Запустить один раз: python create_watermark.py
Потом можно заменить assets/watermark_logo.png на свой логотип.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def create_default_watermark():
    width, height = 300, 80
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Фон
    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=12, fill=(20, 20, 20, 180))

    # Текст
    try:
        font_large = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32
        )
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14
        )
    except OSError:
        font_large = ImageFont.load_default()
        font_small = font_large

    draw.text((15, 8), "AiAprint", font=font_large, fill=(255, 200, 0, 255))
    draw.text((15, 52), "t-shirt print studio", font=font_small, fill=(200, 200, 200, 200))

    output_path = Path("assets/watermark_logo.png")
    output_path.parent.mkdir(exist_ok=True)
    img.save(output_path, "PNG")
    print(f"Watermark logo saved to {output_path}")


if __name__ == "__main__":
    create_default_watermark()
