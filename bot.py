"""
AiAprint Telegram Bot
Точка входа. Запуск: python bot.py
"""
import sys
from loguru import logger

import config
from models import init_db
from handlers.start import register as register_start
from handlers.photo import register as register_photo
from handlers.admin import register as register_admin


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO",
    )
    logger.add(
        "data/bot.log",
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        encoding="utf-8",
    )


async def post_init(application) -> None:
    """Выполняется после инициализации приложения."""
    await init_db()
    logger.info("Database initialized")

    # Устанавливаем команды бота
    from telegram import BotCommand
    await application.bot.set_my_commands([
        BotCommand("start", "Начать работу"),
        BotCommand("help", "Помощь"),
        BotCommand("status", "Статус последнего заказа"),
    ])
    logger.info("Bot commands set")


def main() -> None:
    setup_logging()
    logger.info("Starting AiAprint bot...")

    # Валидация конфига
    errors = config.validate_config()
    if errors:
        for err in errors:
            logger.error("Config error: {}", err)
        logger.error("Fix the errors above and restart the bot.")
        sys.exit(1)

    from telegram.ext import Application

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Регистрируем обработчики
    register_start(app)
    register_photo(app)
    register_admin(app)

    logger.info("Bot started. Admin IDs: {}", config.ADMIN_IDS)
    logger.info("AI provider: {}", config.AI_PROVIDER)

    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
