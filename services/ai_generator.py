"""
Генерация дизайна принта для футболки через KIE.AI Nano Banana 2.
Генерирует только DTF принт-файл А3 (без футболки, прозрачный фон).
Мокап с футболкой создаётся локально через PIL.
"""
import asyncio
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
        Генерирует DTF принт-файл А3 (только принт, без футболки, прозрачный фон).

        Возвращает dtf_bytes — PNG с прозрачным фоном.
        source_image_url — публичный URL для KIE.AI.
        tshirt_color     — 'white' или 'black'.
        license_plate    — гос. номер (введён пользователем, опционально).
        custom_text      — произвольный текст на принт (опционально).
        """
        if config.AI_PROVIDER.lower() != "kieai":
            raise AIGenerationError(
                f"Поддерживается только AI_PROVIDER=kieai, получен: {config.AI_PROVIDER}"
            )
        if not source_image_url:
            raise AIGenerationError("KIE.AI: не передан source_image_url.")

        logger.info(
            "Generating DTF via KIE.AI, color={}, plate={}, text={}",
            tshirt_color, license_plate, custom_text,
        )

        return await self._generate_kieai_dtf(source_image_url, tshirt_color, license_plate, custom_text)

    # ------------------------------------------------------------------
    # Общий строительный блок промпта
    # ------------------------------------------------------------------

    def _build_car_prompt(
        self,
        tshirt_color: str,
        license_plate: str | None,
        custom_text: str | None,
    ) -> tuple[str, str, str]:
        """Возвращает (shirt_contrast, plate_instruction, text_instruction)."""
        shirt_contrast = (
            "Принт рассчитан на тёмную ткань: яркие цвета, светлые блики, "
            "текст белый или светлый."
            if tshirt_color == "black" else
            "Принт рассчитан на светлую ткань: жирные тёмные контуры, "
            "глубокие тени, текст чёрный или тёмный."
        )

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

        if custom_text:
            text_instruction = (
                f"В нижней части принта, под машиной — ОДИН раз жирный стилизованный текст: "
                f"«{custom_text}». "
                f"Текст размещается строго один раз, не повторяется нигде. "
                f"Шрифт широкий, заглавный."
            )
        else:
            text_instruction = "Текст и надписи на принте отсутствуют."

        return shirt_contrast, plate_instruction, text_instruction

    # ------------------------------------------------------------------
    # DTF принт-файл А3
    # ------------------------------------------------------------------

    async def _generate_kieai_dtf(
        self,
        source_image_url: str,
        tshirt_color: str = "white",
        license_plate: str | None = None,
        custom_text: str | None = None,
    ) -> bytes:
        """Генерирует DTF принт-файл А3 (3:4, 2K, прозрачный фон, свободная форма)."""
        shirt_contrast, plate_instruction, text_instruction = self._build_car_prompt(
            tshirt_color, license_plate, custom_text
        )

        prompt = (
            "Создай DTF принт-файл формата А3 (портрет, соотношение сторон 3:4) "
            "для нанесения на футболку методом DTF-печати.\n"

            "КРИТИЧЕСКИ ВАЖНО ПРО ФОН: "
            "Фон принта должен быть ПОЛНОСТЬЮ ПРОЗРАЧНЫМ (alpha=0). "
            "НЕ заливай фон белым, чёрным или каким-либо другим цветом. "
            "Никаких прямоугольных рамок, никаких бордюров, никаких декоративных углов. "
            "Иллюстрация должна иметь свободную форму — только сам рисунок без прямоугольного контура. "
            "PNG с альфа-каналом, всё что не является частью рисунка — прозрачно.\n"

            "Центральный элемент — автомобиль, нарисованный как художественная иллюстрация: "
            "жирные контуры, высокий контраст, кинематографическое освещение. "
            "Сохрани точно марку, модель, цвет кузова, диски, фары, ракурс съёмки "
            "и все детали точно как на исходном фото — без изменений и без тюнинга. "
            "Авто занимает большую часть принта, виден полностью, по центру.\n"

            "Фон за автомобилем: воспроизведи окружение с исходного фото "
            "(дорога, парковка, улица, природа и т.д.) "
            "в том же художественном стиле иллюстрации — "
            "жирные контуры, высокий контраст, без фотореализма.\n"

            f"{plate_instruction}\n"
            f"{text_instruction}\n"
            f"{shirt_contrast}\n"

            "Цвета принта — яркие, насыщенные, без обесцвечивания. "
            "Качество готово к DTF-печати: чёткие края, детализированный рисунок, "
            "НЕТ прямоугольной рамки, НЕТ фона, НЕТ углов."
        )

        logger.debug("KIE.AI DTF prompt ({} chars)", len(prompt))
        return await self._kieai_request(
            source_image_url=source_image_url,
            prompt=prompt,
            aspect_ratio="3:4",
            resolution="2K",
            task_label="DTF",
        )

    # ------------------------------------------------------------------
    # Низкоуровневый хелпер: создание задачи + polling
    # ------------------------------------------------------------------

    async def _kieai_request(
        self,
        source_image_url: str,
        prompt: str,
        aspect_ratio: str = "3:4",
        resolution: str = "2K",
        task_label: str = "task",
    ) -> bytes:
        """Создаёт задачу KIE.AI и опрашивает до получения результата."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.KIE_AI_API_KEY}",
        }
        payload = {
            "model": "nano-banana-2",
            "input": {
                "prompt": prompt,
                "image_input": [source_image_url],
                "aspect_ratio": aspect_ratio,
                "google_search": False,
                "resolution": resolution,
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
                    f"KIE.AI createTask [{task_label}] error {resp.status_code}: {resp.text}"
                )
            data = resp.json()

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            raise AIGenerationError(f"KIE.AI [{task_label}]: не получен taskId. Ответ: {data}")

        logger.info("KIE.AI {} task created: {}", task_label, task_id)

        poll_headers = {"Authorization": f"Bearer {config.KIE_AI_API_KEY}"}
        max_wait = 300
        interval = 5
        elapsed  = 0

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
                    logger.warning(
                        "KIE.AI {} poll {}: {}", task_label, poll_resp.status_code, poll_resp.text
                    )
                    continue

                task = poll_resp.json().get("data", {})
                state = task.get("state", "")
                logger.debug(
                    "KIE.AI {} task {} state={}, elapsed={}s", task_label, task_id, state, elapsed
                )

                if state == "success":
                    result = _json.loads(task.get("resultJson", "{}"))
                    urls = result.get("resultUrls", [])
                    if not urls:
                        raise AIGenerationError(
                            f"KIE.AI [{task_label}]: задача завершена, но resultUrls пуст."
                        )
                    img_resp = await client.get(urls[0])
                    img_resp.raise_for_status()
                    logger.info("KIE.AI {} completed, {} bytes", task_label, len(img_resp.content))
                    return img_resp.content

                elif state in ("failed", "cancelled", "error"):
                    fail_msg = task.get("failMsg") or "неизвестная ошибка"
                    raise AIGenerationError(
                        f"KIE.AI [{task_label}]: задача провалилась ({state}): {fail_msg}"
                    )

        raise AIGenerationError(f"KIE.AI [{task_label}]: таймаут ожидания результата (5 минут).")
