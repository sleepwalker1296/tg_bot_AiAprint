"""
Основной обработчик фотографий от пользователей.
Пайплайн: получение фото → выбор цвета футболки → генерация дизайна → водяной знак → отправка.
"""
import io
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import MessageHandler, CallbackQueryHandler, ContextTypes, Application, filters
from loguru import logger

import config
from models import async_session, Order, OrderStatus
from services.ai_generator import AIGenerator, AIGenerationError
from services.image_processor import ImageProcessor
from services.moysklad import MoySkladClient, MoySkladError


_image_processor = ImageProcessor()
_ai_generator = AIGenerator()

# Доступные цвета футболок: callback_key → (emoji, название, английское название для промта)
TSHIRT_COLORS: dict[str, tuple[str, str, str]] = {
    "white": ("⚪", "Белая",        "white"),
    "black": ("⚫", "Чёрная",       "black"),
    "gray":  ("🩶", "Серая",        "light gray"),
    "navy":  ("🔵", "Тёмно-синяя", "navy blue"),
}


# ---------------------------------------------------------------------------
# Шаг 1: пользователь прислал фото
# ---------------------------------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимаем фото, сохраняем в user_data и просим выбрать цвет футболки."""
    user = update.effective_user
    message = update.message

    logger.info("Photo received from user {} ({})", user.id, user.username)

    photo = message.photo[-1]

    if photo.file_size and photo.file_size > config.MAX_PHOTO_SIZE:
        await message.reply_text(
            "⚠️ Фото слишком большое. Пожалуйста, отправьте фото меньшего размера."
        )
        return

    # Сохраняем file_id до выбора цвета
    context.user_data["pending_photo_file_id"] = photo.file_id

    # Предлагаем выбрать цвет футболки
    color_items = list(TSHIRT_COLORS.items())
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{emoji} {name}",
                callback_data=f"color_select:{key}",
            )
            for key, (emoji, name, _) in color_items[:2]
        ],
        [
            InlineKeyboardButton(
                f"{emoji} {name}",
                callback_data=f"color_select:{key}",
            )
            for key, (emoji, name, _) in color_items[2:]
        ],
    ])

    await message.reply_text(
        "👕 *Отлично! Фото получено.*\n\n"
        "Выберите цвет футболки для вашего принта:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Шаг 2: пользователь выбрал цвет — запускаем генерацию
# ---------------------------------------------------------------------------

async def handle_color_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь выбрал цвет футболки — стартуем генерацию дизайна."""
    query = update.callback_query
    await query.answer()

    color_key = query.data.split(":")[1]
    if color_key not in TSHIRT_COLORS:
        await query.edit_message_text("⚠️ Неизвестный цвет. Попробуйте ещё раз.")
        return

    emoji, color_ru, color_en = TSHIRT_COLORS[color_key]

    # Достаём file_id из user_data
    photo_file_id = context.user_data.pop("pending_photo_file_id", None)
    if not photo_file_id:
        await query.edit_message_text(
            "⚠️ Фото не найдено. Пожалуйста, отправьте фото автомобиля заново."
        )
        return

    user = update.effective_user

    # Подтверждаем выбор
    await query.edit_message_text(
        f"✅ Выбрана {emoji} *{color_ru}* футболка.\n\n"
        "⏳ *Генерирую дизайн принта...*\n"
        "ИИ анализирует ваш автомобиль — это займёт 1–2 минуты.",
        parse_mode="Markdown",
    )

    # Создаём заказ в БД
    order = Order(
        telegram_user_id=user.id,
        telegram_username=user.username,
        telegram_first_name=user.first_name,
        original_photo_file_id=photo_file_id,
        tshirt_color=color_key,
        status=OrderStatus.GENERATING,
    )
    async with async_session() as session:
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id

    await _run_generation_pipeline(
        context=context,
        status_message=query.message,
        order_id=order_id,
        photo_file_id=photo_file_id,
        color_en=color_en,
        color_ru=f"{emoji} {color_ru}",
        user=user,
    )


# ---------------------------------------------------------------------------
# Пайплайн генерации
# ---------------------------------------------------------------------------

async def _run_generation_pipeline(
    context: ContextTypes.DEFAULT_TYPE,
    status_message,
    order_id: int,
    photo_file_id: str,
    color_en: str,
    color_ru: str,
    user,
) -> None:
    """Скачивает фото, генерирует дизайн, отправляет превью."""
    try:
        # Скачиваем оригинальное фото
        tg_file = await context.bot.get_file(photo_file_id)
        photo_bytes_io = io.BytesIO()
        await tg_file.download_to_memory(photo_bytes_io)
        photo_bytes = photo_bytes_io.getvalue()

        # Публичный Telegram URL (нужен для KIE.AI)
        fp = tg_file.file_path
        if fp.startswith("http"):
            tg_file_url = fp
        else:
            tg_file_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{fp.lstrip('/')}"

        # Сохраняем оригинальное фото
        original_path = config.ORDERS_DIR / f"order_{order_id:05d}_original.jpg"
        _image_processor.save_original(photo_bytes, original_path)

        async with async_session() as session:
            db_order = await session.get(Order, order_id)
            db_order.original_photo_path = str(original_path)
            await session.commit()

        # Генерируем дизайн через AI
        generated_bytes = await _ai_generator.generate(
            original_path,
            source_image_url=tg_file_url,
            tshirt_color=color_en,
        )

        # Сохраняем сгенерированный дизайн
        generated_path = config.ORDERS_DIR / f"order_{order_id:05d}_design.png"
        _image_processor.save_original(generated_bytes, generated_path)

        # Создаём превью с водяным знаком
        preview_bytes = _image_processor.create_preview(generated_path)
        preview_path = config.ORDERS_DIR / f"order_{order_id:05d}_preview.jpg"
        preview_path.write_bytes(preview_bytes)

        # Обновляем запись в БД
        async with async_session() as session:
            db_order = await session.get(Order, order_id)
            db_order.generated_image_path = str(generated_path)
            db_order.preview_image_path = str(preview_path)
            db_order.status = OrderStatus.PREVIEW_SENT
            await session.commit()

        # Удаляем статусное сообщение
        await status_message.delete()

        # Отправляем превью
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Хочу заказать!", callback_data=f"order_confirm:{order_id}"),
                InlineKeyboardButton("❌ Не нравится", callback_data=f"order_cancel:{order_id}"),
            ]
        ])

        await context.bot.send_photo(
            chat_id=user.id,
            photo=io.BytesIO(preview_bytes),
            caption=(
                f"🎨 *Ваш дизайн на {color_ru} футболке готов!*\n\n"
                "👆 Это предварительный просмотр с водяным знаком.\n"
                "После оформления заказа вы получите финальный файл в высоком качестве.\n\n"
                "Нравится дизайн? Оформляем заказ? 👇"
            ),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        # Уведомляем администраторов
        await _notify_admins(context, order_id, user, color_ru, generated_path, original_path)

    except AIGenerationError as exc:
        logger.error("AI generation failed for order {}: {}", order_id, exc)
        await status_message.edit_text(
            "❌ *Ошибка генерации дизайна.*\n\n"
            "К сожалению, не удалось создать дизайн. "
            "Попробуйте отправить другое фото или обратитесь к администратору.",
            parse_mode="Markdown",
        )
        async with async_session() as session:
            db_order = await session.get(Order, order_id)
            db_order.status = OrderStatus.CANCELLED
            db_order.notes = f"AI error: {exc}"
            await session.commit()

    except Exception as exc:
        logger.exception("Unexpected error processing order {}", order_id)
        await status_message.edit_text(
            "❌ *Произошла ошибка.*\n\nПожалуйста, попробуйте ещё раз.",
            parse_mode="Markdown",
        )
        async with async_session() as session:
            db_order = await session.get(Order, order_id)
            db_order.status = OrderStatus.CANCELLED
            db_order.notes = f"Unexpected error: {exc}"
            await session.commit()


# ---------------------------------------------------------------------------
# Подтверждение / отмена заказа
# ---------------------------------------------------------------------------

async def handle_order_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь подтвердил заказ."""
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.split(":")[1])
    user = update.effective_user

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if not order or order.telegram_user_id != user.id:
            await query.edit_message_caption("⚠️ Заказ не найден.")
            return
        order.status = OrderStatus.CONFIRMED
        await session.commit()

    moysklad_order_name = None
    try:
        async with MoySkladClient() as ms:
            ms_order = await ms.create_customer_order(
                telegram_user_id=user.id,
                telegram_username=user.username,
                first_name=user.first_name,
                order_db_id=order_id,
            )
            moysklad_order_name = ms_order.get("name")
            moysklad_order_id = ms_order.get("id")

        async with async_session() as session:
            order = await session.get(Order, order_id)
            order.moysklad_order_id = moysklad_order_id
            order.moysklad_order_name = moysklad_order_name
            await session.commit()

        logger.info("MoySklad order created: {}", moysklad_order_name)

    except MoySkladError as exc:
        logger.error("Failed to create MoySklad order for {}: {}", order_id, exc)

    order_text = f"\nНомер заказа: `{moysklad_order_name}`" if moysklad_order_name else ""
    await query.edit_message_caption(
        f"✅ *Заказ #{order_id:05d} оформлен!*{order_text}\n\n"
        "Наш менеджер свяжется с вами для уточнения деталей:\n"
        "• Размер футболки\n"
        "• Способ и адрес доставки\n"
        "• Оплата\n\n"
        "Спасибо, что выбрали AiAprint! 🚗👕",
        parse_mode="Markdown",
    )

    for admin_id in config.ADMIN_IDS:
        try:
            username = (user.username or "нет").replace("_", "\\_")
            first_name = (user.first_name or "").replace("_", "\\_")
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"✅ *Заказ подтверждён!*\n\n"
                    f"Заказ #{order_id:05d}"
                    f"{' / ' + moysklad_order_name if moysklad_order_name else ''}\n"
                    f"Пользователь: @{username} ({first_name})\n"
                    f"TG ID: `{user.id}`\n\n"
                    f"Необходимо уточнить размер, адрес доставки и организовать оплату."
                ),
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.warning("Failed to notify admin {}: {}", admin_id, exc)


async def handle_order_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь отказался от заказа."""
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.split(":")[1])
    user = update.effective_user

    async with async_session() as session:
        order = await session.get(Order, order_id)
        if order and order.telegram_user_id == user.id:
            order.status = OrderStatus.CANCELLED
            await session.commit()

    await query.edit_message_caption(
        "😔 *Дизайн не понравился?*\n\n"
        "Попробуйте отправить другое фото автомобиля — "
        "мы создадим новый вариант!\n\n"
        "💡 Для лучшего результата отправьте чёткое фото авто\n"
        "с хорошим ракурсом (спереди-сбоку).",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Уведомление администраторов
# ---------------------------------------------------------------------------

async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    user,
    color_ru: str,
    generated_path: Path,
    original_path: Path,
) -> None:
    """Отправляет администраторам оригинальный дизайн без водяного знака."""
    if not config.ADMIN_IDS:
        return

    generated_bytes = _image_processor.get_original_bytes(generated_path)
    original_bytes = _image_processor.get_original_bytes(original_path)

    username = (user.username or "нет").replace("_", "\\_")
    first_name = (user.first_name or "").replace("_", "\\_")
    caption = (
        f"🆕 *Новый заказ #{order_id:05d}*\n\n"
        f"👤 Пользователь: @{username} ({first_name})\n"
        f"🆔 TG ID: `{user.id}`\n"
        f"👕 Цвет футболки: {color_ru}\n\n"
        f"📎 Ниже — оригинальный дизайн (без водяного знака, высокое качество)"
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=io.BytesIO(original_bytes),
                caption=f"📷 *Исходное фото авто* (заказ #{order_id:05d})",
                parse_mode="Markdown",
            )
            await context.bot.send_document(
                chat_id=admin_id,
                document=io.BytesIO(generated_bytes),
                filename=f"order_{order_id:05d}_design_HQ.png",
                caption=caption,
                parse_mode="Markdown",
            )
            logger.debug("Admin {} notified about order {}", admin_id, order_id)
        except Exception as exc:
            logger.error("Failed to notify admin {} about order {}: {}", admin_id, order_id, exc)


# ---------------------------------------------------------------------------
# Регистрация хэндлеров
# ---------------------------------------------------------------------------

def register(app: Application) -> None:
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_color_selection, pattern=r"^color_select:"))
    app.add_handler(CallbackQueryHandler(handle_order_confirm, pattern=r"^order_confirm:"))
    app.add_handler(CallbackQueryHandler(handle_order_cancel, pattern=r"^order_cancel:"))


class _Router:
    def register(self, app: Application) -> None:
        register(app)


router = _Router()
