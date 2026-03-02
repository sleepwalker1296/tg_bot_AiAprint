"""
Генерация дизайна принта для футболки через AI API.
Поддерживает: OpenAI DALL-E 3, Stability AI, Replicate, KIE.AI Nano Banana 2.
"""
import base64
import io
from pathlib import Path

import aiohttp
import httpx
from loguru import logger

import config


class AIGenerationError(Exception):
    """Ошибка генерации изображения."""


class AIGenerator:
    """Генерирует дизайн принта на основе фото автомобиля."""

    async def generate(self, source_image_path: Path, source_image_url: str = "") -> bytes:
        """
        Принимает путь к оригинальному фото.
        source_image_url — публичный URL для провайдеров, которым нужен URL (kieai).
        Возвращает байты PNG-изображения дизайна.
        """
        provider = config.AI_PROVIDER.lower()
        logger.info("Generating design via provider={}", provider)

        if provider == "openai":
            return await self._generate_openai(source_image_path)
        elif provider == "stability":
            return await self._generate_stability(source_image_path)
        elif provider == "replicate":
            return await self._generate_replicate(source_image_path)
        elif provider == "kieai":
            return await self._generate_kieai(source_image_url)
        else:
            raise AIGenerationError(f"Неизвестный AI_PROVIDER: {provider}")

    # ------------------------------------------------------------------
    # OpenAI DALL-E 3 (image edit / generation)
    # ------------------------------------------------------------------

    async def _generate_openai(self, source_image_path: Path) -> bytes:
        """
        Использует GPT-4o Vision для анализа авто и DALL-E 3 для генерации дизайна.
        Шаг 1: Описываем авто через GPT-4 Vision.
        Шаг 2: Генерируем принт через DALL-E 3.
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

        # Шаг 1: Получаем описание авто
        with open(source_image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        logger.debug("Step 1: Analyzing car with GPT-4o Vision...")
        vision_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
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
                                "Describe this car in detail for a t-shirt print design: "
                                "make, model, color, distinctive features, angle/perspective. "
                                "Be specific and concise. Answer in English."
                            ),
                        },
                    ],
                }
            ],
            max_tokens=300,
        )
        car_description = vision_response.choices[0].message.content
        logger.debug("Car description: {}", car_description)

        # Шаг 2: Генерируем дизайн принта
        prompt = (
            f"T-shirt print design, DTF printing style. "
            f"Central element: {car_description}. "
            f"Style: bold graphic art, high contrast, vivid colors, "
            f"artistic illustration, street art influence. "
            f"White background, no text, no watermarks, "
            f"suitable for direct-to-film printing. "
            f"Professional quality, sharp details."
        )

        logger.debug("Step 2: Generating print design with DALL-E 3...")
        dalle_response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="hd",
            n=1,
            response_format="b64_json",
        )

        image_b64 = dalle_response.data[0].b64_json
        image_bytes = base64.b64decode(image_b64)
        logger.info("OpenAI generation completed, {} bytes", len(image_bytes))
        return image_bytes

    # ------------------------------------------------------------------
    # Stability AI (img2img)
    # ------------------------------------------------------------------

    async def _generate_stability(self, source_image_path: Path) -> bytes:
        """Генерация через Stability AI Stable Diffusion img2img."""
        url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/image-to-image"

        with open(source_image_path, "rb") as f:
            image_data = f.read()

        prompt = config.AI_PROMPT_TEMPLATE

        async with aiohttp.ClientSession() as session:
            form_data = aiohttp.FormData()
            form_data.add_field("init_image", image_data, content_type="image/jpeg")
            form_data.add_field("init_image_mode", "IMAGE_STRENGTH")
            form_data.add_field("image_strength", "0.35")
            form_data.add_field(
                "text_prompts[0][text]",
                prompt,
            )
            form_data.add_field("text_prompts[0][weight]", "1")
            form_data.add_field(
                "text_prompts[1][text]",
                "blurry, low quality, text, watermark, signature",
            )
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

        image_b64 = result["artifacts"][0]["base64"]
        image_bytes = base64.b64decode(image_b64)
        logger.info("Stability AI generation completed, {} bytes", len(image_bytes))
        return image_bytes

    # ------------------------------------------------------------------
    # Replicate
    # ------------------------------------------------------------------

    async def _generate_replicate(self, source_image_path: Path) -> bytes:
        """Генерация через Replicate API (SDXL)."""
        import replicate  # type: ignore

        with open(source_image_path, "rb") as f:
            image_data = f.read()

        image_b64 = base64.b64encode(image_data).decode("utf-8")
        data_uri = f"data:image/jpeg;base64,{image_b64}"

        client = replicate.Client(api_token=config.REPLICATE_API_TOKEN)
        output = await client.async_run(
            "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",
            input={
                "image": data_uri,
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

    # ------------------------------------------------------------------
    # KIE.AI Nano Banana 2 (image-to-image + polling)
    # ------------------------------------------------------------------

    async def _generate_kieai(self, source_image_url: str) -> bytes:
        """
        Генерация через KIE.AI Nano Banana 2.
        Создаёт задачу и опрашивает статус каждые 5 сек (до 5 минут).
        source_image_url — публичный URL исходного изображения.
        """
        import asyncio
        import json as _json

        if not source_image_url:
            raise AIGenerationError(
                "KIE.AI: не передан source_image_url. "
                "Убедитесь, что AI_PROVIDER=kieai и фото загружается через Telegram."
            )

        prompt = (
            "Convert this car photo into a DTF t-shirt print design. "
            "Preserve the exact car: same make, model, body color, rims, headlights, and all distinctive details — draw it faithfully. "
            "Style: bold graphic art illustration, vibrant saturated colors, high contrast, clean sharp outlines, dynamic 3/4 front angle. "
            "Pure white background, no ground, no shadows, no reflections. "
            "The car is the ONLY element — no people, no road, no scenery, no text, no watermarks, no logos. "
            "Full car visible, centered composition. "
            "Print-ready quality, crisp edges suitable for direct-to-film (DTF) transfer."
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.KIE_AI_API_KEY}",
        }
        payload = {
            "model": "nano-banana-2",
            "input": {
                "prompt": prompt,
                "image_input": [source_image_url],
                "aspect_ratio": "auto",
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
