"""
Интеграция с МойСклад API v1.2.
Документация: https://dev.moysklad.ru/doc/api/remap/1.2/
"""
import base64
from datetime import datetime
from typing import Any

import aiohttp
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

import config


class MoySkladError(Exception):
    """Ошибка при работе с МойСклад API."""


class MoySkladClient:
    """Асинхронный клиент для МойСклад API."""

    def __init__(self) -> None:
        self._base_url = config.MOYSKLAD_BASE_URL
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "MoySkladClient":
        self._session = aiohttp.ClientSession(headers=self._auth_headers())
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Заказы покупателей
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def create_customer_order(
        self,
        telegram_user_id: int,
        telegram_username: str | None,
        first_name: str | None,
        order_db_id: int,
    ) -> dict[str, Any]:
        """
        Создаёт заказ покупателя в МойСклад.
        Возвращает данные созданного заказа.
        """
        name = telegram_username or first_name or str(telegram_user_id)
        order_name = f"TG-{order_db_id:05d}"

        payload: dict[str, Any] = {
            "name": order_name,
            "description": (
                f"Заказ из Telegram бота AiAprint\n"
                f"Пользователь: @{telegram_username or 'нет'} ({first_name or ''})\n"
                f"TG ID: {telegram_user_id}\n"
                f"DB ID: {order_db_id}"
            ),
            "moment": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Привязываем организацию если задана
        if config.MOYSKLAD_ORGANIZATION_ID:
            payload["organization"] = {
                "meta": {
                    "href": (
                        f"{self._base_url}/entity/organization/"
                        f"{config.MOYSKLAD_ORGANIZATION_ID}"
                    ),
                    "type": "organization",
                    "mediaType": "application/json",
                }
            }

        # Привязываем склад если задан
        if config.MOYSKLAD_STORE_ID:
            payload["store"] = {
                "meta": {
                    "href": f"{self._base_url}/entity/store/{config.MOYSKLAD_STORE_ID}",
                    "type": "store",
                    "mediaType": "application/json",
                }
            }

        # Позиции заказа — 1 футболка с принтом
        payload["positions"] = [
            {
                "quantity": 1,
                "price": 0,  # Цена будет выставлена администратором
                "discount": 0,
                "vat": 0,
                "assortment": {
                    "meta": {
                        "href": f"{self._base_url}/entity/product/new",
                        "type": "product",
                        "mediaType": "application/json",
                    }
                },
            }
        ]

        url = f"{self._base_url}/entity/customerorder"
        result = await self._post(url, payload)
        logger.info("MoySklad order created: id={}, name={}", result.get("id"), order_name)
        return result

    async def get_customer_order(self, order_id: str) -> dict[str, Any]:
        """Получает заказ по ID."""
        url = f"{self._base_url}/entity/customerorder/{order_id}"
        return await self._get(url)

    async def update_order_status(self, order_id: str, state_name: str) -> dict[str, Any]:
        """Обновляет статус заказа."""
        # Сначала находим нужное состояние
        states = await self._get_order_states()
        state = next((s for s in states if s.get("name") == state_name), None)
        if not state:
            logger.warning("State '{}' not found in MoySklad", state_name)
            return {}

        url = f"{self._base_url}/entity/customerorder/{order_id}"
        payload = {"state": {"meta": state["meta"]}}
        return await self._put(url, payload)

    # ------------------------------------------------------------------
    # Контрагенты (покупатели)
    # ------------------------------------------------------------------

    async def find_or_create_counterparty(
        self,
        telegram_user_id: int,
        telegram_username: str | None,
        first_name: str | None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Ищет контрагента по Telegram ID в описании или создаёт нового."""
        # Поиск по тегу telegram_id
        search_url = (
            f"{self._base_url}/entity/counterparty"
            f"?search=tg:{telegram_user_id}&limit=1"
        )
        result = await self._get(search_url)
        rows = result.get("rows", [])
        if rows:
            logger.debug("Counterparty found: {}", rows[0].get("id"))
            return rows[0]

        # Создаём нового
        name = telegram_username or first_name or f"TG_{telegram_user_id}"
        payload: dict[str, Any] = {
            "name": name,
            "description": f"tg:{telegram_user_id}",
            "companyType": "individual",
        }
        if phone:
            payload["phone"] = phone

        url = f"{self._base_url}/entity/counterparty"
        new_cp = await self._post(url, payload)
        logger.info("Counterparty created: id={}", new_cp.get("id"))
        return new_cp

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    async def _get_order_states(self) -> list[dict[str, Any]]:
        url = f"{self._base_url}/entity/customerorder/metadata"
        meta = await self._get(url)
        return meta.get("states", [])

    async def check_connection(self) -> bool:
        """Проверяет подключение к МойСклад."""
        try:
            await self._get(f"{self._base_url}/entity/organization?limit=1")
            return True
        except Exception as exc:
            logger.error("MoySklad connection check failed: {}", exc)
            return False

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if config.MOYSKLAD_TOKEN:
            headers["Authorization"] = f"Bearer {config.MOYSKLAD_TOKEN}"
        elif config.MOYSKLAD_LOGIN and config.MOYSKLAD_PASSWORD:
            credentials = base64.b64encode(
                f"{config.MOYSKLAD_LOGIN}:{config.MOYSKLAD_PASSWORD}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {credentials}"
        else:
            logger.warning("MoySklad credentials not configured!")
        return headers

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._auth_headers())
        return self._session

    async def _get(self, url: str) -> dict[str, Any]:
        async with self._get_session().get(url) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise MoySkladError(f"GET {url} → {resp.status}: {data}")
            return data

    async def _post(self, url: str, payload: dict) -> dict[str, Any]:
        async with self._get_session().post(url, json=payload) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise MoySkladError(f"POST {url} → {resp.status}: {data}")
            return data

    async def _put(self, url: str, payload: dict) -> dict[str, Any]:
        async with self._get_session().put(url, json=payload) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise MoySkladError(f"PUT {url} → {resp.status}: {data}")
            return data
