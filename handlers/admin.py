"""
Панель администратора.
Команды для управления заказами, проверки статусов, ручной отправки файлов.
"""
import io
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    Application,
    filters,
    MessageHandler,
)
from loguru import logger

import config
from models import async_session, Order, OrderStatus
from services.image_processor import ImageProcessor
from services.moysklad import MoySkladClient, MoySkladError

_image_processor = ImageProcessor()


# ------------------------------------------------------------------
# Декоратор: только для администраторов
# ------------------------------------------------------------------

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user.id not in config.ADMIN_IDS:
            await update.message.reply_text("⛔ Доступ запрещён.")
            logger.warning("Unauthorized access attempt by user {}", user.id)
            return
        return await func(update, context)
    return wrapper


# ------------------------------------------------------------------
# Команды
# ------------------------------------------------------------------

@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Главное меню администратора."""
    text = (
        "🔧 *Панель администратора AiAprint*\n\n"
        "📋 Команды:\n"
        "/orders — список последних заказов\n"
        "/order <id> — информация о заказе\n"
        "/hq <id> — отправить HQ файл дизайна\n"
        "/setstatus <id> <статус> — изменить статус заказа\n"
        "/mscheck — проверить подключение к МойСклад\n"
        "/stats — статистика\n\n"
        "📌 Статусы: `pending` `generating` `preview_sent` "
        "`confirmed` `in_production` `shipped` `delivered` `cancelled`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@admin_only
async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список последних 10 заказов."""
    from sqlalchemy import select, desc

    async with async_session() as session:
        result = await session.execute(
            select(Order).order_by(desc(Order.created_at)).limit(10)
        )
        orders = result.scalars().all()

    if not orders:
        await update.message.reply_text("Заказов пока нет.")
        return

    STATUS_EMOJI = {
        "pending": "⏳", "generating": "🎨", "preview_sent": "👁",
        "confirmed": "✅", "in_production": "🖨", "shipped": "📦",
        "delivered": "🏠", "cancelled": "❌",
    }

    lines = ["📋 *Последние заказы:*\n"]
    for o in orders:
        emoji = STATUS_EMOJI.get(o.status, "❓")
        user_tag = f"@{o.telegram_username}" if o.telegram_username else o.telegram_first_name or str(o.telegram_user_id)
        lines.append(
            f"{emoji} `#{o.id:05d}` | {user_tag} | {o.created_at.strftime('%d.%m %H:%M')}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def cmd_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Детали конкретного заказа: /order <id>"""
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /order <id>")
        return

    order_id = int(args[0])
    async with async_session() as session:
        order = await session.get(Order, order_id)

    if not order:
        await update.message.reply_text(f"Заказ #{order_id} не найден.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Отправить HQ", callback_data=f"admin_hq:{order_id}"),
            InlineKeyboardButton("✅ В производство", callback_data=f"admin_status:{order_id}:in_production"),
        ],
        [
            InlineKeyboardButton("📦 Отправлен", callback_data=f"admin_status:{order_id}:shipped"),
            InlineKeyboardButton("❌ Отменить", callback_data=f"admin_status:{order_id}:cancelled"),
        ],
    ])

    text = (
        f"📋 *Заказ #{order_id:05d}*\n\n"
        f"👤 Пользователь: @{order.telegram_username or 'нет'} ({order.telegram_first_name or ''})\n"
        f"🆔 TG ID: `{order.telegram_user_id}`\n"
        f"📊 Статус: `{order.status}`\n"
        f"📅 Создан: {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    )
    if order.moysklad_order_name:
        text += f"📑 МойСклад: `{order.moysklad_order_name}`\n"
    if order.notes:
        text += f"📝 Заметки: {order.notes}\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


@admin_only
async def cmd_send_hq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправить HQ дизайн пользователю: /hq <id>"""
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /hq <id>")
        return

    order_id = int(args[0])
    await _send_hq_to_user(update, context, order_id)


@admin_only
async def cmd_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Изменить статус заказа: /setstatus <id> <статус>"""
    args = context.args
    if len(args) < 2 or not args[0].isdigit():
        await update.message.reply_text("Использование: /setstatus <id> <статус>")
        return

    order_id = int(args[0])
    new_status_str = args[1].lower()

    try:
        new_status = OrderStatus(new_status_str)
    except ValueError:
        await update.message.reply_text(f"Неизвестный статус: `{new_status_str}`", parse_mode="Markdown")
        return

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order:
            await update.message.reply_text(f"Заказ #{order_id} не найден.")
            return
        order.status = new_status
        await session.commit()

    await update.message.reply_text(
        f"✅ Статус заказа #{order_id:05d} изменён на `{new_status_str}`",
        parse_mode="Markdown",
    )
    logger.info("Admin {} changed order {} status to {}", update.effective_user.id, order_id, new_status_str)

    # Уведомляем пользователя
    await _notify_user_status_change(context, order_id, new_status)


@admin_only
async def cmd_moysklad_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверить подключение к МойСклад."""
    await update.message.reply_text("⏳ Проверяю подключение к МойСклад...")
    async with MoySkladClient() as ms:
        ok = await ms.check_connection()
    if ok:
        await update.message.reply_text("✅ МойСклад подключён успешно!")
    else:
        await update.message.reply_text(
            "❌ Не удалось подключиться к МойСклад.\n"
            "Проверьте MOYSKLAD_TOKEN или LOGIN/PASSWORD в .env"
        )


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Статистика заказов."""
    from sqlalchemy import select, func

    async with async_session() as session:
        total_result = await session.execute(select(func.count(Order.id)))
        total = total_result.scalar()

        confirmed_result = await session.execute(
            select(func.count(Order.id)).where(Order.status == OrderStatus.CONFIRMED)
        )
        confirmed = confirmed_result.scalar()

        today_result = await session.execute(
            select(func.count(Order.id)).where(
                func.date(Order.created_at) == func.date("now")
            )
        )
        today = today_result.scalar()

    text = (
        "📊 *Статистика AiAprint*\n\n"
        f"Всего заказов: *{total}*\n"
        f"Подтверждено: *{confirmed}*\n"
        f"Сегодня: *{today}*\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ------------------------------------------------------------------
# Callback-кнопки
# ------------------------------------------------------------------

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user

    if user.id not in config.ADMIN_IDS:
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data.startswith("admin_hq:"):
        order_id = int(data.split(":")[1])
        await _send_hq_to_user(update, context, order_id, from_callback=True)

    elif data.startswith("admin_status:"):
        _, order_id_str, new_status_str = data.split(":")
        order_id = int(order_id_str)
        try:
            new_status = OrderStatus(new_status_str)
        except ValueError:
            return

        async with async_session() as session:
            order = await session.get(Order, order_id)
            if order:
                order.status = new_status
                await session.commit()

        await query.edit_message_text(
            f"✅ Статус заказа #{order_id:05d} изменён на `{new_status_str}`",
            parse_mode="Markdown",
        )
        await _notify_user_status_change(context, order_id, new_status)


# ------------------------------------------------------------------
# Вспомогательные функции
# ------------------------------------------------------------------

async def _send_hq_to_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    from_callback: bool = False,
) -> None:
    """Отправляет HQ файл дизайна пользователю."""
    async with async_session() as session:
        order = await session.get(Order, order_id)

    if not order:
        msg = f"Заказ #{order_id} не найден."
        if from_callback:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    if not order.generated_image_path:
        msg = f"Файл дизайна для заказа #{order_id} не найден."
        if from_callback:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    from pathlib import Path
    generated_path = Path(order.generated_image_path)
    if not generated_path.exists():
        msg = f"Файл дизайна для заказа #{order_id} недоступен на диске."
        if from_callback:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    hq_bytes = _image_processor.get_original_bytes(generated_path)

    # Отправляем как документ (без сжатия Telegram)
    await context.bot.send_document(
        chat_id=order.telegram_user_id,
        document=io.BytesIO(hq_bytes),
        filename=f"AiAprint_order_{order_id:05d}_HQ.png",
        caption=(
            f"🎨 *Ваш дизайн принта — высокое качество*\n\n"
            f"Заказ #{order_id:05d}\n"
            f"Файл готов к передаче в печать. "
            f"Благодарим за заказ!"
        ),
        parse_mode="Markdown",
    )

    confirm_msg = f"✅ HQ файл дизайна заказа #{order_id:05d} отправлен пользователю."
    if from_callback:
        await update.callback_query.edit_message_text(confirm_msg)
    else:
        await update.message.reply_text(confirm_msg)

    logger.info("HQ file sent to user {} for order {}", order.telegram_user_id, order_id)


STATUS_MESSAGES = {
    OrderStatus.IN_PRODUCTION: "🖨 *Ваш заказ #{order_id:05d} передан в производство!*\n\nМы уже печатаем ваш принт. Скоро будет готово!",
    OrderStatus.SHIPPED: "📦 *Ваш заказ #{order_id:05d} отправлен!*\n\nТрек-номер и детали доставки будут предоставлены отдельно.",
    OrderStatus.DELIVERED: "🏠 *Ваш заказ #{order_id:05d} доставлен!*\n\nСпасибо что выбрали AiAprint! Будем рады новым заказам 🚗👕",
    OrderStatus.CANCELLED: "❌ *Заказ #{order_id:05d} отменён.*\n\nЕсли у вас есть вопросы — обратитесь к администратору.",
}


async def _notify_user_status_change(
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    new_status: OrderStatus,
) -> None:
    """Отправляет уведомление пользователю при смене статуса."""
    if new_status not in STATUS_MESSAGES:
        return

    async with async_session() as session:
        order = await session.get(Order, order_id)

    if not order:
        return

    text = STATUS_MESSAGES[new_status].format(order_id=order_id)
    try:
        await context.bot.send_message(
            chat_id=order.telegram_user_id,
            text=text,
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Failed to notify user {} about status change: {}", order.telegram_user_id, exc)


# ------------------------------------------------------------------
# Регистрация
# ------------------------------------------------------------------

def register(app: Application) -> None:
    app.add_handler(CommandHandler("admin", cmd_admin, filters=filters.User(config.ADMIN_IDS) if config.ADMIN_IDS else filters.ALL))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("order", cmd_order_detail))
    app.add_handler(CommandHandler("hq", cmd_send_hq))
    app.add_handler(CommandHandler("setstatus", cmd_set_status))
    app.add_handler(CommandHandler("mscheck", cmd_moysklad_check))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=r"^admin_"))


class _Router:
    def register(self, app: Application) -> None:
        register(app)


router = _Router()
