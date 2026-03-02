"""
Обработчики команд /start и /help.
"""
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, Application

from loguru import logger


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("User {} ({}) started the bot", user.id, user.username)

    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        "🚗 *Добро пожаловать в AiAprint* — эксклюзивные принты с вашим авто на футболке!\n\n"
        "📸 *Как это работает:*\n"
        "1. Отправьте фото вашего автомобиля\n"
        "2. Выберите цвет футболки\n"
        "3. ИИ нарисует принт в стиле авто-аппарель\n"
        "4. Получите превью и оформите заказ\n\n"
        "💡 *Просто отправьте фото авто — и мы начнём!*"
    )

    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "🛠 *Помощь по боту AiAprint*\n\n"
        "📸 *Отправка фото:* Отправьте фото вашего автомобиля, выберите цвет футболки — и ИИ нарисует принт в стиле авто-аппарель.\n\n"
        "⏱ *Время обработки:* Генерация дизайна занимает 1–2 минуты.\n\n"
        "🎨 *Качество превью:* Предпросмотр отправляется с водяным знаком. "
        "Финальный файл для печати — в высоком качестве.\n\n"
        "❓ *Вопросы и поддержка:* обратитесь к администратору.\n\n"
        "📋 *Команды:*\n"
        "/start — начать работу\n"
        "/help — эта справка\n"
        "/status — статус последнего заказа"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from models import async_session, Order
    from sqlalchemy import select, desc

    user = update.effective_user
    async with async_session() as session:
        result = await session.execute(
            select(Order)
            .where(Order.telegram_user_id == user.id)
            .order_by(desc(Order.created_at))
            .limit(1)
        )
        order = result.scalar_one_or_none()

    if not order:
        await update.message.reply_text("У вас ещё нет заказов. Отправьте фото автомобиля!")
        return

    STATUS_LABELS = {
        "pending": "⏳ Ожидает обработки",
        "generating": "🎨 Генерируется дизайн",
        "preview_sent": "👁 Превью отправлено",
        "confirmed": "✅ Заказ подтверждён",
        "in_production": "🖨 В производстве",
        "shipped": "📦 Отправлен",
        "delivered": "🏠 Доставлен",
        "cancelled": "❌ Отменён",
    }

    status_label = STATUS_LABELS.get(order.status, order.status)
    text = (
        f"📋 *Последний заказ #{order.id:05d}*\n\n"
        f"Статус: {status_label}\n"
        f"Создан: {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    )
    if order.moysklad_order_name:
        text += f"Номер заказа: `{order.moysklad_order_name}`\n"

    await update.message.reply_text(text, parse_mode="Markdown")


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))


# Для совместимости с импортом через router
class _Router:
    def register(self, app: Application) -> None:
        register(app)


router = _Router()
