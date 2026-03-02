"""
Обработка изображений: создание превью с водяным знаком и размытием.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance
from loguru import logger

import config


class ImageProcessor:
    """Создаёт превью (водяной знак + размытие) и хранит оригинал."""

    def __init__(self) -> None:
        self._watermark_logo: Image.Image | None = self._load_watermark_logo()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def create_preview(self, image_path: Path) -> bytes:
        """
        Принимает путь к оригинальному изображению.
        Возвращает байты JPEG-превью с водяным знаком и размытием.
        """
        with Image.open(image_path) as img:
            img = img.convert("RGBA")
            img = self._apply_blur(img)
            img = self._reduce_quality_visually(img)
            img = self._apply_watermark_text(img)
            if self._watermark_logo:
                img = self._apply_watermark_logo(img)
            preview = img.convert("RGB")

        buffer = io.BytesIO()
        preview.save(buffer, format="JPEG", quality=config.PREVIEW_QUALITY, optimize=True)
        buffer.seek(0)
        logger.debug("Preview created, size={} bytes", len(buffer.getvalue()))
        return buffer.getvalue()

    def save_original(self, image_bytes: bytes, dest: Path) -> Path:
        """Сохраняет оригинальное изображение без изменений."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Конвертируем в RGB (без альфа-канала) для PNG совместимости
            save_img = img.convert("RGB")
            save_img.save(dest, format="PNG", optimize=False)
        logger.debug("Original saved to {}", dest)
        return dest

    def get_original_bytes(self, image_path: Path) -> bytes:
        """Читает оригинальное изображение и возвращает PNG-байты без потерь."""
        with Image.open(image_path) as img:
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            buffer.seek(0)
            return buffer.getvalue()

    # ------------------------------------------------------------------
    # Приватные методы
    # ------------------------------------------------------------------

    def _apply_blur(self, img: Image.Image) -> Image.Image:
        """Применяет Гауссово размытие."""
        if config.BLUR_RADIUS > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius=config.BLUR_RADIUS))
        return img

    def _reduce_quality_visually(self, img: Image.Image) -> Image.Image:
        """Уменьшает насыщенность для ощущения «плохого» качества."""
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(0.75)
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(0.90)
        return img

    def _apply_watermark_text(self, img: Image.Image) -> Image.Image:
        """Накладывает текстовый водяной знак в несколько рядов."""
        draw = ImageDraw.Draw(img, "RGBA")
        width, height = img.size

        font_size = max(24, width // 20)
        font = self._get_font(font_size)

        text = config.WATERMARK_TEXT
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # Повторяем водяной знак по всему изображению по сетке
        padding_x = text_w + 40
        padding_y = text_h + 40

        for y in range(-padding_y, height + padding_y, padding_y * 2):
            for x in range(-padding_x, width + padding_x, padding_x):
                # Чередуем строки со смещением
                offset = padding_x // 2 if (y // (padding_y * 2)) % 2 else 0
                draw.text(
                    (x + offset, y),
                    text,
                    font=font,
                    fill=(255, 255, 255, 100),
                )
                draw.text(
                    (x + offset + 1, y + 1),
                    text,
                    font=font,
                    fill=(0, 0, 0, 60),
                )

        return img

    def _apply_watermark_logo(self, img: Image.Image) -> Image.Image:
        """Накладывает логотип как водяной знак в центр."""
        logo = self._watermark_logo.copy()

        # Масштабируем логотип до 30% ширины изображения
        max_logo_width = img.width // 3
        ratio = max_logo_width / logo.width
        new_size = (int(logo.width * ratio), int(logo.height * ratio))
        logo = logo.resize(new_size, Image.LANCZOS)

        # Делаем полупрозрачным
        if logo.mode != "RGBA":
            logo = logo.convert("RGBA")
        r, g, b, a = logo.split()
        a = a.point(lambda p: int(p * 0.5))
        logo.putalpha(a)

        # Позиция — центр
        x = (img.width - logo.width) // 2
        y = (img.height - logo.height) // 2
        img.paste(logo, (x, y), logo)
        return img

    @staticmethod
    def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Загружает шрифт; при отсутствии системного использует дефолтный."""
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
        for path in font_candidates:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def _load_watermark_logo(self) -> Image.Image | None:
        """Загружает логотип для водяного знака, если он есть."""
        logo_path = config.WATERMARK_LOGO_PATH
        if logo_path.exists():
            try:
                return Image.open(logo_path).convert("RGBA")
            except Exception as exc:
                logger.warning("Cannot load watermark logo {}: {}", logo_path, exc)
        return None
