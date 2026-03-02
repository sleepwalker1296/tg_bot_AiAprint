"""
Генерация дизайна принта для футболки через AI API.
Поддерживает: OpenAI DALL-E 3, Stability AI, Replicate, KIE.AI Nano Banana 2.
"""
import base64
import io
import json as _json
from pathlib import Path

import aiohttp
import httpx
from loguru import logger

import config


# ---------------------------------------------------------------------------
# Словарь слоганов по маркам авто (русский, 2 строки)
# ---------------------------------------------------------------------------

# Ключи в нижнем регистре, включая русские варианты написания
_BRAND_SLOGANS: list[tuple[list[str], tuple[str, str]]] = [
    (["bmw", "бмв"],                        ("ПРОСТО BMW",          "ЭТИМ ВСЁ СКАЗАНО")),
    (["mercedes", "мерседес", "мерс"],      ("MERCEDES",            "НЕ ТРЕБУЕТ ПОЯСНЕНИЙ")),
    (["toyota", "тойота"],                  ("УМИРАТЬ УМЕЕТ",       "НО НЕ ХОЧЕТ")),
    (["lada", "лада", "ваз", "vaz"],        ("РОССИЙСКИЙ ОРИГИНАЛ", "БЕЗ АНАЛОГОВ")),
    (["volkswagen", "vw", "фольксваген"],   ("СДЕЛАНО В ГЕРМАНИИ",  "ЛЮБЯТ В ДУШЕ")),
    (["audi", "ауди"],                      ("ЧЕТЫРЕ КОЛЬЦА",       "ОДНА ЛЮБОВЬ")),
    (["ford", "форд"],                      ("FORD НЕ ЕДЕТ",        "FORD ЛЕТИТ")),
    (["nissan", "ниссан"],                  ("ДРИФТ В КРОВИ",       "С ЗАВОДА")),
    (["subaru", "субару"],                  ("BOXER ЗВУЧИТ",        "ИЗ СЕРДЦА")),
    (["honda", "хонда"],                    ("VTEC ВКЛЮЧИЛСЯ",      "ПОЧУВСТВУЙ")),
    (["mazda", "мазда"],                    ("ZOOM-ZOOM",           "НАВСЕГДА")),
    (["kia", "киа"],                        ("РАНЬШЕ",              "НЕ УВАЖАЛИ")),
    (["hyundai", "хёндэ", "хундай"],        ("КОРЕЯ",               "ОТВЕТИЛА")),
    (["porsche", "порше"],                  ("НЕ ДЛЯ ВСЕХ",         "ДЛЯ СВОИХ")),
    (["range rover", "рендж ровер"],        ("СЕРВИС КАЖДЫЙ ДЕНЬ",  "ЭТО НОРМАЛЬНО")),
    (["land rover", "ленд ровер"],          ("БЕЗДОРОЖЬЕ",          "ЭТО ДОМ")),
    (["jeep", "джип"],                      ("ДОРОГИ НЕ НУЖНЫ",     "СЕРЬЁЗНО")),
    (["volvo", "вольво"],                   ("ШВЕДЫ ЗНАЮТ",         "КАК ВЫЖИТЬ")),
    (["renault", "рено"],                   ("ФРАНЦУЗСКИЙ ХАРАКТЕР","НЕ ЛЕЧИТСЯ")),
    (["peugeot", "пежо"],                   ("ФРАНЦУЗСКИЙ СТИЛЬ",   "ФРАНЦУЗСКИЕ НЕРВЫ")),
    (["citroen", "ситроен"],                ("ФРАНЦУЗЫ СНОВА",      "УДИВЛЯЮТ")),
    (["mitsubishi", "мицубиси"],            ("EVO ЖИВ",             "В КАЖДОМ ИЗ НАС")),
    (["lexus", "лексус"],                   ("ЯПОНСКАЯ РОСКОШЬ",    "БЕЗ ИЗВИНЕНИЙ")),
    (["infiniti", "инфинити"],              ("РОСКОШЬ",             "В ДЕТАЛЯХ")),
    (["chevrolet", "шевроле"],              ("ШЕВИК",               "НАРОДНЫЙ ВЫБОР")),
    (["dodge", "додж"],                     ("АМЕРИКАНСКАЯ",        "НЕВМЕНЯЕМОСТЬ")),
    (["tesla", "тесла"],                    ("ТИХО",                "НО БЫСТРО")),
    (["lamborghini", "ламборгини"],         ("КОГДА ТИХО",          "НЕ ВАРИАНТ")),
    (["ferrari", "феррари"],                ("КРАСНЫЙ",             "ЕДИНСТВЕННЫЙ ЦВЕТ")),
    (["maserati", "мазерати"],              ("ЗВУК",                "КОТОРЫЙ ПОМНЯТ")),
    (["alfa romeo", "альфа ромео"],         ("ИТАЛЬЯНСКИЙ ТЕМПЕРАМЕНТ", "ИТАЛЬЯНСКАЯ НАДЁЖНОСТЬ")),
    (["suzuki", "сузуки"],                  ("МАЛЕНЬКИЙ",           "НО ЗЛОЙ")),
    (["skoda", "шкода"],                    ("ПРОСТО РАБОТАЕТ",     "БЕЗ ЛИШНИХ СЛОВ")),
    (["opel", "опель"],                     ("НЕМЕЦ",               "С ДУШОЙ")),
    (["mini", "мини"],                      ("МАЛЕНЬКИЙ СНАРУЖИ",   "БОЛЬШОЙ ВНУТРИ")),
    (["rolls-royce", "rolls royce", "роллс ройс"], ("КОГДА ВСЁ ОСТАЛЬНОЕ", "ЭТО НЕ ROLLS")),
    (["bentley", "бентли"],                 ("СКОРОСТЬ",            "С ДОСТОИНСТВОМ")),
    (["aston martin", "астон мартин"],      ("ДЖЕЙМС БОНд",        "ОДОБРИЛ БЫ")),
    (["jaguar", "ягуар"],                   ("БРИТАНСКАЯ КРОВЬ",    "НИКОГДА НЕ ОСТЫНЕТ")),
    (["uaz", "уаз"],                        ("УАЗ ЕДЕТ",            "КОГДА ВСЕ ЗАСТРЯЛИ")),
    (["haval", "хавал"],                    ("КИТАЙ",               "УДИВЛЯЕТ")),
    (["chery", "чери"],                     ("ЧЕМ НЕ ЯПОНЕЦ",       "ВОПРОС ОТКРЫТ")),
    (["geely", "джили"],                    ("БУДУЩЕЕ",             "УЖЕ ЗДЕСЬ")),
    (["genesis", "дженезис"],               ("КОРЕЯ",               "ВЫРОСЛА")),
    (["cadillac", "кадиллак"],              ("AMERICAN DREAM",      "НА КОЛЁСАХ")),
    (["lincoln", "линкольн"],               ("АМЕРИКАНСКАЯ РОСКОШЬ","ДЛЯ ВСЕХ")),
    (["volga", "волга", "газ"],             ("СОВЕТСКАЯ КЛАССИКА",  "ВЕЧНЫЙ СТИЛЬ")),
    (["москвич", "moskvich"],               ("МОСКВИЧ ВЕРНУЛСЯ",    "МОСКВА В ШОКЕ")),
]

_DEFAULT_SLOGAN: tuple[str, str] = ("ТВОЙ АВТОМОБИЛЬ", "ТВОЙ ПРИНТ")


def get_slogan_for_car(car_brand: str) -> tuple[str, str]:
    """Возвращает пару строк слогана по введённой марке авто."""
    if not car_brand:
        return _DEFAULT_SLOGAN
    text = car_brand.lower()
    for keys, slogan in _BRAND_SLOGANS:
        for key in keys:
            if key in text:
                return slogan
    return _DEFAULT_SLOGAN


# ---------------------------------------------------------------------------
# Ошибка генерации
# ---------------------------------------------------------------------------

class AIGenerationError(Exception):
    """Ошибка генерации изображения."""


# ---------------------------------------------------------------------------
# Основной класс генератора
# ---------------------------------------------------------------------------

class AIGenerator:

    async def generate(
        self,
        source_image_path: Path,
        source_image_url: str = "",
        tshirt_color: str = "white",
        license_plate: str | None = None,
        car_brand: str = "",
    ) -> bytes:
        """
        source_image_url — публичный URL для KIE.AI.
        tshirt_color     — 'white' или 'black'.
        license_plate    — гос. номер (опционально).
        car_brand        — марка/модель авто для подбора слогана.
        """
        provider = config.AI_PROVIDER.lower()
        logger.info(
            "Generating via provider={}, color={}, plate={}, brand={}",
            provider, tshirt_color, license_plate, car_brand,
        )

        if provider == "openai":
            return await self._generate_openai(source_image_path)
        elif provider == "stability":
            return await self._generate_stability(source_image_path)
        elif provider == "replicate":
            return await self._generate_replicate(source_image_path)
        elif provider == "kieai":
            return await self._generate_kieai(
                source_image_url, tshirt_color, license_plate, car_brand
            )
        else:
            raise AIGenerationError(f"Неизвестный AI_PROVIDER: {provider}")

    # ------------------------------------------------------------------
    # KIE.AI Nano Banana 2
    # ------------------------------------------------------------------

    async def _generate_kieai(
        self,
        source_image_url: str,
        tshirt_color: str = "white",
        license_plate: str | None = None,
        car_brand: str = "",
    ) -> bytes:
        import asyncio

        if not source_image_url:
            raise AIGenerationError("KIE.AI: не передан source_image_url.")

        # Слоган
        slogan_line1, slogan_line2 = get_slogan_for_car(car_brand)

        # Цвет футболки на русском
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
                f"Сохрани государственный номер «{license_plate}» на номерном знаке автомобиля "
                f"вместе с флагом страны на номере — точно как в оригинале."
            )
        else:
            plate_instruction = (
                "Если на фото виден государственный номер — сохрани его вместе с флагом на знаке."
            )

        prompt = (
            # Общая задача
            "Создай фотореалистичный мокап футболки с авто-принтом.\n"

            # Футболка — полностью в кадре
            f"Покажи полностью {shirt_ru} футболку: воротник сверху, "
            f"оба рукава по бокам, подол снизу — без обрезки. "
            f"Чистый студийный фон за футболкой.\n"

            # Принт на груди
            "На передней части футболки по центру — авто-принт:\n"

            # Автомобиль
            "Автомобиль нарисован как смелая художественная иллюстрация — "
            "жирные контуры, высокий контраст, кинематографическое освещение с глубокими тенями. "
            "Сохрани точно: марку, модель, цвет кузова, диски, фары и все характерные детали "
            "без изменений и без тюнинга. "
            "Авто занимает большую часть принта, полностью виден, по центру.\n"

            # Фон принта — из оригинального фото
            "Фон на принте — взят с оригинального фото автомобиля.\n"

            # Гос. номер
            f"{plate_instruction}\n"

            # Слоган
            f"На принте над и/или под машиной — жирный стилизованный текст: "
            f"«{slogan_line1}» первая строка, «{slogan_line2}» вторая строка. "
            f"Шрифт широкий, заглавный, в стиле авто-брендов.\n"

            # Контраст под цвет
            f"{shirt_contrast}\n"

            # Качество
            "Качество готово к DTF-печати: чёткие края, детализированный рисунок."
        )

        logger.debug("KIE.AI prompt ({} chars)", len(prompt))
        logger.debug("Slogan: {} / {}", slogan_line1, slogan_line2)

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
                    logger.warning("KIE.AI poll error {}: {}", poll_resp.status_code, poll_resp.text)
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

    # ------------------------------------------------------------------
    # OpenAI DALL-E 3
    # ------------------------------------------------------------------

    async def _generate_openai(self, source_image_path: Path) -> bytes:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

        with open(source_image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        vision_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_data}", "detail": "high"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Опиши автомобиль для принта на футболку: "
                            "марка, модель, цвет, характерные черты. "
                            "Кратко и точно."
                        ),
                    },
                ],
            }],
            max_tokens=300,
        )
        car_description = vision_response.choices[0].message.content
        logger.debug("Car description: {}", car_description)

        prompt = (
            f"Мокап белой футболки, полностью в кадре: воротник, оба рукава, подол. "
            f"Принт на груди: {car_description}. "
            f"Стиль: авто-аппарель, жирные контуры, высокий контраст. "
            f"Без людей, без лишних логотипов."
        )

        dalle_response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="hd",
            n=1,
            response_format="b64_json",
        )

        image_bytes = base64.b64decode(dalle_response.data[0].b64_json)
        logger.info("OpenAI generation completed, {} bytes", len(image_bytes))
        return image_bytes

    # ------------------------------------------------------------------
    # Stability AI
    # ------------------------------------------------------------------

    async def _generate_stability(self, source_image_path: Path) -> bytes:
        url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/image-to-image"

        with open(source_image_path, "rb") as f:
            image_data = f.read()

        async with aiohttp.ClientSession() as session:
            form_data = aiohttp.FormData()
            form_data.add_field("init_image", image_data, content_type="image/jpeg")
            form_data.add_field("init_image_mode", "IMAGE_STRENGTH")
            form_data.add_field("image_strength", "0.35")
            form_data.add_field("text_prompts[0][text]", config.AI_PROMPT_TEMPLATE)
            form_data.add_field("text_prompts[0][weight]", "1")
            form_data.add_field("text_prompts[1][text]", "blurry, low quality, watermark")
            form_data.add_field("text_prompts[1][weight]", "-1")
            form_data.add_field("cfg_scale", "7")
            form_data.add_field("steps", "30")
            form_data.add_field("samples", "1")

            async with session.post(
                url,
                data=form_data,
                headers={"Authorization": f"Bearer {config.STABILITY_API_KEY}"},
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise AIGenerationError(f"Stability AI error {resp.status}: {text}")
                result = await resp.json()

        image_bytes = base64.b64decode(result["artifacts"][0]["base64"])
        logger.info("Stability AI generation completed, {} bytes", len(image_bytes))
        return image_bytes

    # ------------------------------------------------------------------
    # Replicate
    # ------------------------------------------------------------------

    async def _generate_replicate(self, source_image_path: Path) -> bytes:
        import replicate  # type: ignore

        with open(source_image_path, "rb") as f:
            image_data = f.read()

        image_b64 = base64.b64encode(image_data).decode("utf-8")
        client = replicate.Client(api_token=config.REPLICATE_API_TOKEN)
        output = await client.async_run(
            "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",
            input={
                "image": f"data:image/jpeg;base64,{image_b64}",
                "prompt": config.AI_PROMPT_TEMPLATE,
                "negative_prompt": "blurry, low quality, watermark",
                "prompt_strength": 0.8,
                "num_inference_steps": 30,
            },
        )

        image_url = output[0] if isinstance(output, list) else output
        async with httpx.AsyncClient() as http:
            response = await http.get(str(image_url))
            response.raise_for_status()

        logger.info("Replicate generation completed")
        return response.content
