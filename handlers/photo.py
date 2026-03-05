"""
Основной обработчик фотографий от пользователей.
Пайплайн: фото → цвет → текст на принт (опц.) → гос. номер (опц.) → генерация → превью.
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

TSHIRT_COLORS: dict[str, tuple[str, str]] = {
    "white": ("⚪", "Белая"),
    "black": ("⚫", "Чёрная"),
}


# ---------------------------------------------------------------------------
# Шаг 1: фото → выбор цвета
# ---------------------------------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    photo = update.message.photo[-1]

    logger.info("Photo received from user {} ({})", user.id, user.username)

    if photo.file_size and photo.file_size > config.MAX_PHOTO_SIZE:
        await update.message.reply_text(
            "⚠️ Фото слишком большое. Пожалуйста, отправьте фото меньшего размера."
        )
        return

    # Сбрасываем все предыдущие состояния
    for key in ("awaiting_custom_text", "awaiting_plate",
                "pending_color_key", "pending_custom_text", "pending_photo_file_id"):
        context.user_data.pop(key, None)

    context.user_data["pending_photo_file_id"] = photo.file_id

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚪ Белая",  callback_data="color_select:white"),
        InlineKeyboardButton("⚫ Чёрная", callback_data="color_select:black"),
    ]])

    await update.message.reply_text(
        "👕 *Фото получено!*\n\nВыберите цвет футболки:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Шаг 2: цвет выбран → спрашиваем текст для принта
# ---------------------------------------------------------------------------

async def handle_color_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    color_key = query.data.split(":")[1]
    if color_key not in TSHIRT_COLORS:
        await query.edit_message_text("⚠️ Неизвестный цвет. Попробуйте ещё раз.")
        return

    if not context.user_data.get("pending_photo_file_id"):
        await query.edit_message_text(
            "⚠️ Фото не найдено. Пожалуйста, отправьте фото автомобиля заново."
        )
        return

    emoji, color_ru = TSHIRT_COLORS[color_key]
    context.user_data["pending_color_key"] = color_key
    context.user_data["awaiting_custom_text"] = True

    await query.edit_message_text(
        f"{emoji} *{color_ru}* футболка выбрана.\n\n"
        "✍️ Введите *текст для принта на футболке*\n"
        "_(например: МОЩЬ В КАЖДОЙ ДЕТАЛИ, ЖИВУ НА СКОРОСТИ)_\n\n"
        "Текст будет размещён под машиной на принте.\n"
        "Если текст не нужен — нажмите «Пропустить»:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Пропустить", callback_data="custom_text_skip"),
        ]]),
    )


# ---------------------------------------------------------------------------
# Шаг 2b: текст пропущен → переходим к номеру
# ---------------------------------------------------------------------------

async def handle_skip_custom_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    context.user_data.pop("awaiting_custom_text", None)
    context.user_data["pending_custom_text"] = None
    context.user_data["awaiting_plate"] = True

    await query.edit_message_text(
        "⏭ Текст пропущен — принт будет без надписи.\n\n"
        "🔢 Введите *гос. номер* автомобиля _(например: А123ВС77)_\n\n"
        "Номер будет на машине в принте вместе с флагом.\n"
        "Если номер хорошо виден на фото — можно пропустить:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Пропустить", callback_data="plate_skip"),
        ]]),
    )


# ---------------------------------------------------------------------------
# Шаг 3: гос. номер пропущен → запуск генерации
# ---------------------------------------------------------------------------

async def handle_skip_plate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    context.user_data.pop("awaiting_plate", None)

    await query.edit_message_text(
        "⏭ Номер пропущен — использую номер с фото (если виден).\n\n"
        "⏳ *Генерирую дизайн...* Это займёт 1–2 минуты.",
        parse_mode="Markdown",
    )

    await _launch_generation(update=update, context=context, plate=None,
                             status_message=query.message)


# ---------------------------------------------------------------------------
# Универсальный обработчик текстового ввода
# ---------------------------------------------------------------------------

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_custom_text"):
        context.user_data.pop("awaiting_custom_text", None)
        text = update.message.text.strip()
        context.user_data["pending_custom_text"] = text or None
        context.user_data["awaiting_plate"] = True

        await update.message.reply_text(
            f"✅ Текст *«{text}»* будет на принте.\n\n"
            "🔢 Введите *гос. номер* автомобиля _(например: А123ВС77)_\n\n"
            "Если номер хорошо виден на фото — можно пропустить:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустить", callback_data="plate_skip"),
            ]]),
        )

    elif context.user_data.get("awaiting_plate"):
        context.user_data.pop("awaiting_plate", None)
        raw = update.message.text.strip().upper()
        plate = raw if 4 <= len(raw) <= 10 else None

        await update.message.reply_text(
            f"✅ Номер *{plate}* принят." if plate
            else "ℹ️ Не похоже на гос. номер — продолжаю без явного номера.",
            parse_mode="Markdown",
        )
        await _launch_generation(update=update, context=context, plate=plate)


# ---------------------------------------------------------------------------
# Запуск генерации
# ---------------------------------------------------------------------------

async def _launch_generation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    plate: str | None,
    status_message=None,
) -> None:
    user = update.effective_user
    photo_file_id  = context.user_data.pop("pending_photo_file_id", None)
    color_key      = context.user_data.pop("pending_color_key", "white")
    custom_text    = context.user_data.pop("pending_custom_text", None)

    if not photo_file_id:
        await update.effective_message.reply_text(
            "⚠️ Фото потеряно. Пожалуйста, отправьте фото заново."
        )
        return

    emoji, color_ru = TSHIRT_COLORS.get(color_key, TSHIRT_COLORS["white"])

    if status_message is None:
        status_message = await update.message.reply_text(
            "⏳ *Генерирую дизайн...* Это займёт 1–2 минуты.",
            parse_mode="Markdown",
        )

    order = Order(
        telegram_user_id=user.id,
        telegram_username=user.username,
        telegram_first_name=user.first_name,
        original_photo_file_id=photo_file_id,
        tshirt_color=color_key,
        license_plate=plate,
        custom_text=custom_text,
        status=OrderStatus.GENERATING,
    )
    async with async_session() as session:
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id

    await _run_generation_pipeline(
        context=context,
        status_message=status_message,
        order_id=order_id,
        photo_file_id=photo_file_id,
        color_key=color_key,
        color_ru=f"{emoji} {color_ru}",
        custom_text=custom_text,
        plate=plate,
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
    color_key: str,
    color_ru: str,
    custom_text: str | None,
    plate: str | None,
    user,
) -> None:
    try:
        tg_file = await context.bot.get_file(photo_file_id)
        photo_bytes_io = io.BytesIO()
        await tg_file.download_to_memory(photo_bytes_io)
        photo_bytes = photo_bytes_io.getvalue()

        fp = tg_file.file_path
        tg_file_url = fp if fp.startswith("http") else (
            f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{fp.lstrip('/')}"
        )

        original_path = config.ORDERS_DIR / f"order_{order_id:05d}_original.jpg"
        _image_processor.save_original(photo_bytes, original_path)

        async with async_session() as session:
            db_order = await session.get(Order, order_id)
            db_order.original_photo_path = str(original_path)
            await session.commit()

        # 1 вызов KIE.AI → DTF принт (только принт, прозрачный фон, без футболки)
        dtf_bytes = await _ai_generator.generate(
            original_path,
            source_image_url=tg_file_url,
            tshirt_color=color_key,
            license_plate=plate,
            custom_text=custom_text,
        )

        # ── Диагностика прозрачности DTF ───────────────────────────────────
        import io as _io
        from PIL import Image as _Image
        _diag = _Image.open(_io.BytesIO(dtf_bytes))
        logger.info("[DTF DIAG] mode={} size={}", _diag.mode, _diag.size)
        logger.info("[DTF DIAG] extrema={}", _diag.getextrema())
        if _diag.mode == "RGBA":
            _alpha = _diag.split()[3]
            _amin, _amax = _alpha.getextrema()
            logger.info("[DTF DIAG] alpha min={} max={}", _amin, _amax)
            if _amin > 0:
                logger.warning("[DTF DIAG] ⚠️ фон НЕ полностью прозрачный (alpha min={})", _amin)
            else:
                logger.info("[DTF DIAG] ✅ фон прозрачный (alpha min=0)")
        else:
            logger.warning("[DTF DIAG] ⚠️ изображение без альфа-канала (mode={})", _diag.mode)
        _diag.close()
        # ───────────────────────────────────────────────────────────────────

        dtf_path = config.ORDERS_DIR / f"order_{order_id:05d}_design.png"
        _image_processor.save_dtf(dtf_bytes, dtf_path)

        # PIL накладывает принт на шаблон футболки → мокап для клиента
        mockup_bytes = _image_processor.create_mockup(dtf_bytes, color_key)
        mockup_path  = config.ORDERS_DIR / f"order_{order_id:05d}_mockup.jpg"
        mockup_path.write_bytes(mockup_bytes)

        # Превью для клиента = мокап + водяной знак + размытие
        preview_bytes = _image_processor.create_preview(mockup_path)
        preview_path  = config.ORDERS_DIR / f"order_{order_id:05d}_preview.jpg"
        preview_path.write_bytes(preview_bytes)

        async with async_session() as session:
            db_order = await session.get(Order, order_id)
            db_order.generated_image_path = str(dtf_path)
            db_order.preview_image_path   = str(preview_path)
            db_order.status = OrderStatus.PREVIEW_SENT
            await session.commit()

        await status_message.delete()

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Хочу заказать!", callback_data=f"order_confirm:{order_id}"),
            InlineKeyboardButton("❌ Не нравится",   callback_data=f"order_cancel:{order_id}"),
        ]])

        plate_line = f"\n🔢 Номер: *{plate}*" if plate else ""
        text_line  = f"\n✍️ Текст: _{custom_text}_" if custom_text else ""

        await context.bot.send_photo(
            chat_id=user.id,
            photo=io.BytesIO(preview_bytes),
            caption=(
                f"🎨 *Мокап на {color_ru} футболке готов!*"
                f"{plate_line}{text_line}\n\n"
                "👆 Предварительный просмотр с водяным знаком.\n"
                "После оформления заказа — финальный файл в высоком качестве.\n\n"
                "Нравится? 👇"
            ),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        await _notify_admins(context, order_id, user, color_ru, custom_text, plate,
                             dtf_path, mockup_path, original_path)

    except AIGenerationError as exc:
        logger.error("AI generation failed for order {}: {}", order_id, exc)
        await status_message.edit_text(
            "❌ *Ошибка генерации.*\n\nПопробуйте отправить другое фото.",
            parse_mode="Markdown",
        )
        async with async_session() as session:
            db_order = await session.get(Order, order_id)
            db_order.status = OrderStatus.CANCELLED
            db_order.notes  = f"AI error: {exc}"
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
            db_order.notes  = f"Unexpected error: {exc}"
            await session.commit()


# ---------------------------------------------------------------------------
# Подтверждение / отмена
# ---------------------------------------------------------------------------

async def handle_order_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            moysklad_order_id   = ms_order.get("id")

        async with async_session() as session:
            order = await session.get(Order, order_id)
            order.moysklad_order_id   = moysklad_order_id
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
            username   = (user.username   or "нет").replace("_", "\\_")
            first_name = (user.first_name or "").replace("_", "\\_")
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"✅ *Заказ подтверждён!*\n\n"
                    f"Заказ #{order_id:05d}"
                    f"{' / ' + moysklad_order_name if moysklad_order_name else ''}\n"
                    f"Пользователь: @{username} ({first_name})\n"
                    f"TG ID: `{user.id}`\n\n"
                    f"Уточните размер, адрес доставки и организуйте оплату."
                ),
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.warning("Failed to notify admin {}: {}", admin_id, exc)


async def handle_order_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        "Отправьте другое фото — создадим новый вариант!\n\n"
        "💡 Лучший результат — чёткое фото авто с хорошим ракурсом.",
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
    custom_text: str | None,
    plate: str | None,
    dtf_path: Path,
    mockup_path: Path,
    original_path: Path,
) -> None:
    if not config.ADMIN_IDS:
        return

    dtf_bytes      = _image_processor.get_dtf_bytes(dtf_path)       # RGBA, прозрачный фон
    mockup_bytes   = Path(mockup_path).read_bytes()                  # JPEG мокап
    original_bytes = _image_processor.get_original_bytes(original_path)

    username   = (user.username   or "нет").replace("_", "\\_")
    first_name = (user.first_name or "").replace("_", "\\_")
    text_line  = f"\n✍️ Текст: {custom_text}" if custom_text else ""
    plate_line = f"\n🔢 Гос. номер: `{plate}`" if plate else ""

    info_caption = (
        f"🆕 *Новый заказ #{order_id:05d}*\n\n"
        f"👤 @{username} ({first_name})\n"
        f"🆔 TG ID: `{user.id}`\n"
        f"👕 Цвет: {color_ru}"
        f"{text_line}{plate_line}"
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=io.BytesIO(original_bytes),
                caption=f"📷 *Исходное фото* (заказ #{order_id:05d})",
                parse_mode="Markdown",
            )
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=io.BytesIO(mockup_bytes),
                caption=f"👕 *Мокап футболки* (заказ #{order_id:05d})",
                parse_mode="Markdown",
            )
            await context.bot.send_document(
                chat_id=admin_id,
                document=io.BytesIO(dtf_bytes),
                filename=f"order_{order_id:05d}_DTF_A3.png",
                caption=info_caption + "\n\n📎 DTF принт-файл А3 (без водяного знака)",
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
    app.add_handler(CallbackQueryHandler(handle_color_selection,   pattern=r"^color_select:"))
    app.add_handler(CallbackQueryHandler(handle_skip_custom_text,  pattern=r"^custom_text_skip$"))
    app.add_handler(CallbackQueryHandler(handle_skip_plate,        pattern=r"^plate_skip$"))
    app.add_handler(CallbackQueryHandler(handle_order_confirm,     pattern=r"^order_confirm:"))
    app.add_handler(CallbackQueryHandler(handle_order_cancel,      pattern=r"^order_cancel:"))


class _Router:
    def register(self, app: Application) -> None:
        register(app)


router = _Router()
