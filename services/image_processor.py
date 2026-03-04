"""
Обработка изображений: создание превью с водяным знаком, мокап футболки через PIL.
"""
import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageEnhance
from loguru import logger

import config

# ---------------------------------------------------------------------------
# Параметры зоны принта А3 на футболке (в долях размера шаблона)
# ---------------------------------------------------------------------------
# Принт занимает 44 % ширины шаблона, центр по горизонтали — посередине,
# центр по вертикали — 40 % от верха (грудь).
_PRINT_CENTER_X = 0.50   # горизонтальный центр зоны принта
_PRINT_CENTER_Y = 0.55   # вертикальный центр зоны принта (грудь)
_PRINT_WIDTH_RATIO = 0.44  # ширина принта = 44 % ширины шаблона
# Соотношение сторон А3 portrait: 297 мм × 420 мм → высота = ширина × 420/297
_A3_HEIGHT_RATIO = 420 / 297


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

    def save_dtf(self, dtf_bytes: bytes, dest: Path) -> Path:
        """Сохраняет DTF PNG с прозрачным фоном (RGBA сохраняется)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(dtf_bytes)) as img:
            img.convert("RGBA").save(dest, format="PNG", optimize=False)
        logger.debug("DTF saved to {}", dest)
        return dest

    def get_original_bytes(self, image_path: Path) -> bytes:
        """Читает оригинальное изображение и возвращает PNG-байты без потерь."""
        with Image.open(image_path) as img:
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            buffer.seek(0)
            return buffer.getvalue()

    def get_dtf_bytes(self, dtf_path: Path) -> bytes:
        """Читает DTF PNG с прозрачным фоном (RGBA)."""
        with Image.open(dtf_path) as img:
            buffer = io.BytesIO()
            img.convert("RGBA").save(buffer, format="PNG")
            buffer.seek(0)
            return buffer.getvalue()

    def create_mockup(self, dtf_bytes: bytes, shirt_color: str = "white") -> bytes:
        """
        Накладывает DTF принт на шаблон футболки формата А3.

        Принт (3:4 / A3) масштабируется до 44 % ширины шаблона,
        центрируется горизонтально и размещается на уровне груди (40 % сверху).
        Возвращает JPEG-байты мокапа (макс. 1 500 px по длинной стороне).
        """
        template_path = config.ASSETS_DIR / f"{shirt_color}_shirt.png"
        if not template_path.exists():
            logger.warning("Shirt template not found: {}", template_path)
            return self._dtf_on_plain_bg(dtf_bytes, shirt_color)

        with Image.open(template_path) as tpl:
            # Если шаблон с прозрачностью — сводим на соответствующий фон
            if tpl.mode == "RGBA":
                bg = (20, 20, 20) if shirt_color == "black" else (245, 245, 245)
                shirt = Image.new("RGB", tpl.size, bg)
                shirt.paste(tpl.convert("RGB"), mask=tpl.split()[3])
            else:
                shirt = tpl.convert("RGB")

        sw, sh = shirt.size

        # Размер зоны принта А3
        zone_w = int(sw * _PRINT_WIDTH_RATIO)
        zone_h = int(zone_w * _A3_HEIGHT_RATIO)

        # Верхний левый угол зоны принта
        px = int(sw * _PRINT_CENTER_X) - zone_w // 2
        py = int(sh * _PRINT_CENTER_Y) - zone_h // 2
        px = max(0, px)
        py = max(0, py)

        with Image.open(io.BytesIO(dtf_bytes)) as dtf_img:
            dtf = dtf_img.convert("RGB")
            dtf_scaled = dtf.resize((zone_w, zone_h), Image.LANCZOS)

        # Вырезаем регион футболки под принтом
        shirt_region = shirt.crop((px, py, px + zone_w, py + zone_h))

        if shirt_color == "black":
            # Screen blend: чёрный (#000) фон принта полностью исчезает,
            # яркие цвета иллюстрации проявляются поверх тёмной ткани.
            blended = ImageChops.screen(shirt_region, dtf_scaled)
        else:
            # Multiply blend: белый (#fff) фон принта полностью исчезает,
            # тёмные цвета иллюстрации умножаются на светлую ткань.
            blended = ImageChops.multiply(shirt_region, dtf_scaled)

        # Маска с закруглёнными углами + мягкий край — убираем прямоугольный обрез
        corner_radius = zone_w // 10
        blend_mask = self._rounded_mask(zone_w, zone_h, corner_radius)
        merged = Image.composite(blended, shirt_region, blend_mask)
        shirt.paste(merged, (px, py))
        result = shirt

        # Уменьшаем до разумного размера для отправки
        max_dim = 1500
        if max(sw, sh) > max_dim:
            scale = max_dim / max(sw, sh)
            result = result.resize(
                (int(sw * scale), int(sh * scale)), Image.LANCZOS
            )

        buf = io.BytesIO()
        result.save(buf, format="JPEG", quality=88, optimize=True)
        buf.seek(0)
        logger.debug("Mockup created ({}×{}), {} bytes", result.width, result.height, buf.tell())
        return buf.getvalue()

    @staticmethod
    def _rounded_mask(width: int, height: int, radius: int) -> Image.Image:
        """Маска с закруглёнными углами и размытым (мягким) краем."""
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=255)
        # Размываем край для плавного перехода в ткань
        feather = max(4, radius // 3)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
        return mask

    def _dtf_on_plain_bg(self, dtf_bytes: bytes, shirt_color: str) -> bytes:
        """Запасной вариант: принт на сплошном цветном фоне (если шаблон не найден)."""
        bg_color = (30, 30, 30) if shirt_color == "black" else (245, 245, 245)
        with Image.open(io.BytesIO(dtf_bytes)) as dtf_img:
            dtf = dtf_img.convert("RGBA")
            dw, dh = dtf.size
            # Добавляем поля 20 %
            canvas_w = int(dw * 1.4)
            canvas_h = int(dh * 1.4)
            canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)
            ox = (canvas_w - dw) // 2
            oy = (canvas_h - dh) // 2
            canvas.paste(dtf, (ox, oy), dtf)

        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=88, optimize=True)
        buf.seek(0)
        return buf.getvalue()

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
