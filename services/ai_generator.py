"""
Генерация дизайна принта для футболки через KIE.AI Nano Banana 2.
"""
import json as _json
from pathlib import Path

import httpx
from loguru import logger

import config


class AIGenerationError(Exception):
    """Ошибка генерации изображения."""


class AIGenerator:

    async def generate(
        self,
        source_image_path: Path,
        source_image_url: str = "",
        tshirt_color: str = "white",
        license_plate: str | None = None,
        custom_text: str | None = None,
    ) -> bytes:
        """
        source_image_url — публичный URL для KIE.AI.
        tshirt_color     — 'white' или 'black'.
        license_plate    — гос. номер (введён пользователем, опционально).
        custom_text      — произвольный текст на принт (опционально).
        """
        if config.AI_PROVIDER.lower() != "kieai":
            raise AIGenerationError(
                f"Поддерживается только AI_PROVIDER=kieai, получен: {config.AI_PROVIDER}"
            )
        logger.info(
            "Generating via KIE.AI, color={}, plate={}, text={}",
            tshirt_color, license_plate, custom_text,
        )
        return await self._generate_kieai(source_image_url, tshirt_color, license_plate, custom_text)

    # ------------------------------------------------------------------
    # KIE.AI Nano Banana 2
    # ------------------------------------------------------------------

    async def _generate_kieai(
        self,
        source_image_url: str,
        tshirt_color: str = "white",
        license_plate: str | None = None,
        custom_text: str | None = None,
    ) -> bytes:
        import asyncio

        if not source_image_url:
            raise AIGenerationError("KIE.AI: не передан source_image_url.")

        shirt_ru = "чёрной" if tshirt_color == "black" else "белой"
        shirt_contrast = (
            "Принт рассчитан на тёмную ткань: яркие цвета, светлые блики, "
            "текст белый или светлый."
            if tshirt_color == "black" else
            "Принт рассчитан на светлую ткань: жирные тёмные контуры, "
            "глубокие тени, текст чёрный или тёмный."
        )

        # Гос. номер
        if license_plate:
            plate_instruction = (
                f"Государственный номер «{license_plate}» должен быть чётко виден "
                f"на номерном знаке автомобиля вместе с флагом страны на знаке — "
                f"точно как в оригинале, без изменений."
            )
        else:
            plate_instruction = (
                "Если на исходном фото виден государственный номер — "
                "сохрани его точно как есть, вместе с флагом на знаке."
            )

        # Текст на принте — только если задан, иначе ничего
        if custom_text:
            text_instruction = (
                f"В нижней части принта, под машиной — ОДИН раз жирный стилизованный текст: "
                f"«{custom_text}». "
                f"Текст размещается строго один раз, не повторяется нигде. "
                f"Шрифт широкий, заглавный."
            )
        else:
            text_instruction = "Текст и надписи на принте отсутствуют."

        prompt = (
            # Задача: мокап футболки
            f"Создай фотореалистичный мокап {shirt_ru} футболки с авто-принтом.\n"

            # Футболка — полностью в кадре
            f"Покажи полностью {shirt_ru} футболку: воротник сверху, "
            f"оба рукава по бокам, подол снизу — без обрезки по краям. "
            f"Чистый студийный фон позади футболки.\n"

            # Принт на груди
            "На передней части футболки по центру — авто-принт:\n"

            # Автомобиль — точно как на фото, без тюнинга, без смены ракурса
            "Автомобиль нарисован как художественная иллюстрация: "
            "жирные контуры, высокий контраст, кинематографическое освещение. "
            "Сохрани точно марку, модель, цвет кузова, диски, фары, ракурс съёмки "
            "и все детали точно как на исходном фото — без изменений и без тюнинга. "
            "Авто занимает большую часть принта, виден полностью, по центру.\n"

            # Фон принта — окружение из исходного фото в стиле иллюстрации
            "Фон принта: воспроизведи окружение с исходного фото "
            "(дорога, парковка, улица, природа и т.д.) "
            "в том же художественном стиле, что и автомобиль — "
            "жирные контуры, высокий контраст, без фотореализма.\n"

            # Гос. номер
            f"{plate_instruction}\n"

            # Текст
            f"{text_instruction}\n"

            # Контраст под цвет футболки
            f"{shirt_contrast}\n"

            # Качество
            "Качество готово к DTF-печати: чёткие края, детализированный рисунок."
        )

        logger.debug("KIE.AI prompt ({} chars)", len(prompt))

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.KIE_AI_API_KEY}",
        }
        payload = {
            "model": "nano-banana-2",
            "input": {
                "prompt": prompt,
                "image_input": [source_image_url],
                "aspect_ratio": "1:1",
                "google_search": False,
                "resolution": "1K",
                "output_format": "png",
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.kie.ai/api/v1/jobs/createTask",
                headers=headers,
                json=payload,
            )
            if resp.status_code != 200:
                raise AIGenerationError(
                    f"KIE.AI createTask error {resp.status_code}: {resp.text}"
                )
            data = resp.json()

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            raise AIGenerationError(f"KIE.AI: не получен taskId. Ответ: {data}")

        logger.info("KIE.AI task created: {}", task_id)

        poll_headers = {"Authorization": f"Bearer {config.KIE_AI_API_KEY}"}
        max_wait = 300
        interval = 5
        elapsed = 0

        async with httpx.AsyncClient(timeout=30) as client:
            while elapsed < max_wait:
                await asyncio.sleep(interval)
                elapsed += interval

                poll_resp = await client.get(
                    "https://api.kie.ai/api/v1/playground/recordInfo",
                    params={"taskId": task_id},
                    headers=poll_headers,
                )
                if poll_resp.status_code != 200:
                    logger.warning("KIE.AI poll {}: {}", poll_resp.status_code, poll_resp.text)
                    continue

                task = poll_resp.json().get("data", {})
                state = task.get("state", "")
                logger.debug("KIE.AI task {} state={}, elapsed={}s", task_id, state, elapsed)

                if state == "success":
                    result = _json.loads(task.get("resultJson", "{}"))
                    urls = result.get("resultUrls", [])
                    if not urls:
                        raise AIGenerationError("KIE.AI: задача завершена, но resultUrls пуст.")
                    img_resp = await client.get(urls[0])
                    img_resp.raise_for_status()
                    logger.info("KIE.AI generation completed, {} bytes", len(img_resp.content))
                    return img_resp.content

                elif state in ("failed", "cancelled", "error"):
                    fail_msg = task.get("failMsg") or "неизвестная ошибка"
                    raise AIGenerationError(f"KIE.AI: задача провалилась ({state}): {fail_msg}")

        raise AIGenerationError("KIE.AI: таймаут ожидания результата (5 минут).")
