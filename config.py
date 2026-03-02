import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# ===== TELEGRAM =====
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# ===== AI GENERATION =====
AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
STABILITY_API_KEY: str = os.getenv("STABILITY_API_KEY", "")
REPLICATE_API_TOKEN: str = os.getenv("REPLICATE_API_TOKEN", "")

# Промпт для генерации дизайна футболки
AI_PROMPT_TEMPLATE = (
    "Create a high-quality t-shirt print design featuring the car from the photo. "
    "Style: bold, vibrant, artistic illustration suitable for DTF/DTG printing. "
    "Background: white or transparent. "
    "The car should be the central element, rendered in a stylized, eye-catching way. "
    "No text, no watermarks, clean design ready for print."
)

# ===== МОЙСКЛАД =====
MOYSKLAD_LOGIN: str = os.getenv("MOYSKLAD_LOGIN", "")
MOYSKLAD_PASSWORD: str = os.getenv("MOYSKLAD_PASSWORD", "")
MOYSKLAD_TOKEN: str = os.getenv("MOYSKLAD_TOKEN", "")
MOYSKLAD_BASE_URL: str = "https://api.moysklad.ru/api/remap/1.2"
MOYSKLAD_ORGANIZATION_ID: str = os.getenv("MOYSKLAD_ORGANIZATION_ID", "")
MOYSKLAD_STORE_ID: str = os.getenv("MOYSKLAD_STORE_ID", "")

# ===== ИЗОБРАЖЕНИЯ =====
WATERMARK_LOGO_PATH: Path = BASE_DIR / os.getenv("WATERMARK_LOGO_PATH", "assets/watermark_logo.png")
WATERMARK_TEXT: str = os.getenv("WATERMARK_TEXT", "AiAprint © Предпросмотр")
BLUR_RADIUS: float = float(os.getenv("BLUR_RADIUS", "5"))
PREVIEW_QUALITY: int = int(os.getenv("PREVIEW_QUALITY", "40"))

# ===== ДИРЕКТОРИИ =====
TEMP_DIR: Path = BASE_DIR / os.getenv("TEMP_DIR", "temp")
ORDERS_DIR: Path = BASE_DIR / os.getenv("ORDERS_DIR", "data/orders")
ASSETS_DIR: Path = BASE_DIR / "assets"

for _dir in (TEMP_DIR, ORDERS_DIR, ASSETS_DIR, BASE_DIR / "data"):
    _dir.mkdir(parents=True, exist_ok=True)

# ===== БАЗА ДАННЫХ =====
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR}/data/bot.db")

# ===== ЛИМИТЫ =====
MAX_PHOTO_SIZE: int = int(os.getenv("MAX_PHOTO_SIZE", str(20 * 1024 * 1024)))


def validate_config() -> list[str]:
    """Проверяет наличие обязательных переменных окружения."""
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN не задан")
    if not ADMIN_IDS:
        errors.append("ADMIN_IDS не задан или пуст")
    if AI_PROVIDER == "openai" and not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY не задан (AI_PROVIDER=openai)")
    if AI_PROVIDER == "stability" and not STABILITY_API_KEY:
        errors.append("STABILITY_API_KEY не задан (AI_PROVIDER=stability)")
    return errors
