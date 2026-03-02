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


class AIGenerationError(Exception):
    """Ошибка генерации изображения."""


class AIGenerator:
    """Генерирует дизайн принта на основе фото автомобиля."""

    async def generate(
        self,
        source_image_path: Path,
        source_image_url: str = "",
        tshirt_color: str = "white",
        license_plate: str | None = None,
    ) -> bytes:
        """
        Принимает путь к оригинальному фото.
        source_image_url — публичный URL для KIE.AI.
        tshirt_color     — 'white' или 'black'.
        license_plate    — гос. номер авто (опционально).
        Возвращает байты PNG-изображения.
        """
        provider = config.AI_PROVIDER.lower()
        logger.info(
            "Generating design via provider={}, color={}, plate={}",
            provider, tshirt_color, license_plate,
        )

        if provider == "openai":
            return await self._generate_openai(source_image_path)
        elif provider == "stability":
            return await self._generate_stability(source_image_path)
        elif provider == "replicate":
            return await self._generate_replicate(source_image_path)
        elif provider == "kieai":
            return await self._generate_kieai(
                source_image_url, tshirt_color, license_plate, source_image_path
            )
        else:
            raise AIGenerationError(f"Неизвестный AI_PROVIDER: {provider}")

    # ------------------------------------------------------------------
    # Вспомогательный шаг: анализ авто + генерация слогана через GPT-4o
    # ------------------------------------------------------------------

    async def _analyze_car_and_get_tagline(self, image_path: Path) -> dict:
        """
        Использует GPT-4o Vision для определения марки/модели авто
        и генерации смешного русского слогана специфичного для этой машины.
        Возвращает dict: {car, tagline_line1, tagline_line2}.
        Если OPENAI_API_KEY не задан — возвращает пустой dict.
        """
        if not config.OPENAI_API_KEY:
            logger.debug("OPENAI_API_KEY not set, skipping car analysis")
            return {}

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            logger.debug("Analyzing car with GPT-4o Vision...")
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                                "detail": "low",
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "You are a creative copywriter for automotive apparel.\n"
                                "Look at this car photo and:\n"
                                "1. Identify: make, model, approximate year, body type.\n"
                                "2. Write a SHORT funny/ironic Russian t-shirt slogan (2 lines) "
                                "that references THIS specific car's real reputation, culture, or memes. "
                                "Keep it brief — max 4-5 words per line. "
                                "Uppercase. Humor that car enthusiasts would appreciate.\n\n"
                                "Examples of the STYLE (not to copy verbatim):\n"
                                "- BMW 3 Touring: 'НЕ УНИВЕРСАЛ\\nЭТО WAGON'\n"
                                "- Toyota Land Cruiser 200: 'ДОРОГИ?\\nНЕ СЛЫШАЛ'\n"
                                "- Lada Vesta: 'РОССИЙСКИЙ ОТВЕТ\\nВСЕМУ МИРУ'\n"
                                "- Range Rover: 'СЕРВИС КАЖДЫЙ ДЕНЬ\\nЭТО НОРМАЛЬНО'\n"
                                "- Porsche Cayenne: 'НЕ ДЛЯ ТРЕКОВ\\nДЛЯ ПОНТОВ'\n"
                                "- Mercedes S-Class: 'ЕХАЛ ИЗ САЛОНА\\nНЕ СБАВЛЯЯ'\n\n"
                                "Respond ONLY with valid JSON, no extra text:\n"
                                '{"car": "Make Model Year", "tagline_line1": "LINE1", "tagline_line2": "LINE2"}'
                            ),
                        },
                    ],
                }],
                max_tokens=150,
                temperature=0.9,
            )

            raw = response.choices[0].message.content.strip()
            # Вырезаем JSON если обёрнут в ```
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = _json.loads(raw)
            logger.info("Car identified: {}, tagline: {} / {}",
                        result.get("car"), result.get("tagline_line1"), result.get("tagline_line2"))
            return result

        except Exception as exc:
            logger.warning("Car analysis failed (non-critical): {}", exc)
            return {}

    # ------------------------------------------------------------------
    # KIE.AI Nano Banana 2
    # ------------------------------------------------------------------

    async def _generate_kieai(
        self,
        source_image_url: str,
        tshirt_color: str = "white",
        license_plate: str | None = None,
        source_image_path: Path | None = None,
    ) -> bytes:
        """
        Генерация через KIE.AI Nano Banana 2.
        Перед вызовом KIE.AI — опционально анализирует авто через GPT-4o.
        """
        import asyncio

        if not source_image_url:
            raise AIGenerationError(
                "KIE.AI: не передан source_image_url."
            )

        # --- Анализ авто и генерация слогана ---
        tagline_info: dict = {}
        if source_image_path:
            tagline_info = await self._analyze_car_and_get_tagline(source_image_path)

        car_name = tagline_info.get("car", "")
        line1 = tagline_info.get("tagline_line1", "")
        line2 = tagline_info.get("tagline_line2", "")

        # --- Сборка промта ---

        # Описание авто для KIE.AI
        car_subject = (
            f"the {car_name}" if car_name
            else "the car from the reference photo"
        )

        # Инструкции по гос. номеру
        plate_instruction = (
            f'Include the license plate reading "{license_plate}" '
            f"on the car\'s rear/front license plate holder. "
            if license_plate else ""
        )

        # Инструкции по тексту-слогану
        if line1 and line2:
            text_instruction = (
                f'The t-shirt print includes bold stylized text: '
                f'"{line1}" on the first line and "{line2}" on the second line. '
                f'Place the text above and/or below the car illustration. '
                f'Use a bold condensed automotive font style. '
            )
        else:
            text_instruction = ""

        # Адаптация под цвет футболки
        if tshirt_color == "black":
            shirt_desc = "jet-black"
            contrast_note = (
                "Print uses vibrant colors, bright highlights, and glowing edges "
                "that stand out dramatically against the black shirt fabric. "
                "Text is white or bright colored. "
            )
        else:
            shirt_desc = "pure white"
            contrast_note = (
                "Print uses bold dark outlines and deep shadows "
                "with vivid saturated colors that contrast sharply against the white shirt. "
                "Text is black or dark colored. "
            )

        prompt = (
            # 1. Тип изображения — полный мокап
            f"Create a professional flat-lay product photo of a COMPLETE {shirt_desc} t-shirt "
            f"on a clean studio background. "
            f"The ENTIRE t-shirt must be fully visible in the frame: "
            f"collar at the top, both sleeves fully outstretched left and right, "
            f"and the full hem at the bottom. No cropping of any part of the shirt. "

            # 2. Принт на футболке
            f"On the front chest area of the t-shirt there is a bold automotive graphic print featuring {car_subject}. "
            f"Illustration style: premium motorsport apparel brand aesthetic (like Exhaust, Stance, HKS), "
            f"dynamic 3/4 front-angle view of the car, bold sharp outlines, "
            f"dramatic cinematic lighting with deep shadows and highlights, "
            f"high contrast, street art / JDM tuning culture graphic style. "
            f"Preserve exact make, model, body color, rims, and headlights of the car faithfully. "
            f"Full car visible, centered in the print area. "

            # 3. Гос. номер
            f"{plate_instruction}"

            # 4. Слоган
            f"{text_instruction}"

            # 5. Цветовой контраст
            f"{contrast_note}"

            # 6. Ограничения
            f"No people, no background scenery, no road, no watermarks, no extra logos outside the print. "
            f"The t-shirt is the only subject. Photorealistic product mockup quality."
        )

        logger.debug("KIE.AI prompt ({} chars): {}", len(prompt), prompt[:200])

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
                raise AIGenerationError(f"KIE.AI createTask error {resp.status_code}: {resp.text}")
            data = resp.json()

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            raise AIGenerationError(f"KIE.AI: не получен taskId. Ответ: {data}")

        logger.info("KIE.AI task created: {}", task_id)

        # Polling: каждые 5 сек, максимум 5 минут
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
                    result_json_str = task.get("resultJson", "{}")
                    result = _json.loads(result_json_str)
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

        logger.debug("Step 1: Analyzing car with GPT-4o Vision...")
        vision_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_data}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Describe this car for a t-shirt print: make, model, color, features. "
                            "Be specific and concise. Answer in English."
                        ),
                    },
                ],
            }],
            max_tokens=300,
        )
        car_description = vision_response.choices[0].message.content
        logger.debug("Car description: {}", car_description)

        prompt = (
            f"Flat-lay product photo of a complete white t-shirt on white background. "
            f"Full t-shirt visible: collar, both sleeves, hem. "
            f"Bold automotive print on front: {car_description}. "
            f"Style: motorsport apparel, JDM graphic art, high contrast, bold outlines. "
            f"No people, no extra text, no watermarks."
        )

        logger.debug("Step 2: Generating with DALL-E 3...")
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
            form_data.add_field("text_prompts[1][text]", "blurry, low quality, text, watermark")
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
                "negative_prompt": "blurry, low quality, text, watermark",
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
