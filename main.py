import os
import time
import logging
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN") or "PASTE_YOUR_TELEGRAM_BOT_TOKEN_HERE"
WB_API_TOKEN = os.getenv("WB_API_TOKEN") or "PASTE_YOUR_WB_API_TOKEN_HERE"

LIMIT = 20
WB_CARDS_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("wb_bot")


def build_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(
        {
            "Authorization": WB_API_TOKEN,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "wb-light-bot/1.0",
        }
    )
    return session


SESSION = build_session()


def request_cards(payload: Dict[str, Any], attempts: int = 6) -> Dict[str, Any]:
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            resp = SESSION.post(WB_CARDS_URL, json=payload, timeout=30)

            if resp.status_code == 429:
                retry_after = resp.headers.get("X-Ratelimit-Retry")
                if retry_after and retry_after.isdigit():
                    sleep_time = int(retry_after)
                else:
                    sleep_time = min(20, 0.6 * attempt + 1)

                logger.warning("WB вернул 429, жду %.1f сек", sleep_time)
                time.sleep(sleep_time)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as e:
            last_error = e
            if attempt == attempts:
                break

            sleep_time = min(20, 0.6 * attempt + 1)
            logger.warning("Ошибка WB API: %s. Повтор через %.1f сек", e, sleep_time)
            time.sleep(sleep_time)

    raise RuntimeError(f"Не удалось получить карточки WB: {last_error}")


def normalize_card(card: Dict[str, Any]) -> Dict[str, Any]:
    nm_id = card.get("nmID")
    title = card.get("title") or "Без названия"
    category = card.get("subjectName") or "Категория не найдена"

    image = None
    photos = card.get("photos") or []
    if photos and isinstance(photos, list):
        first = photos[0]
        if isinstance(first, dict):
            image = first.get("big") or first.get("c516x688") or first.get("c246x328")

    return {
        "nm_id": nm_id,
        "title": title,
        "category": category,
        "image": image,
        "created_at": card.get("createdAt"),
        "updated_at": card.get("updatedAt"),
        "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" if nm_id else None,
    }


def fetch_latest_cards(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Берем последнюю страницу карточек по updatedAt DESC и режем до limit.
    """
    payload = {
        "settings": {
            "sort": {
                "ascending": False
            },
            "cursor": {
                "limit": limit
            },
            "filter": {
                "withPhoto": -1
            }
        }
    }

    data = request_cards(payload)
    cards = data.get("cards") or []

    if not cards:
        return []

    return [normalize_card(card) for card in cards[:limit]]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Команда /novinki покажет последние 20 карточек из твоего WB кабинета."
    )


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Загружаю карточки из WB API...")

    try:
        items = fetch_latest_cards(limit=LIMIT)

        if not items:
            await update.message.reply_text("WB API не вернул карточки.")
            return

        for i, item in enumerate(items, start=1):
            text = (
                f"{i}. {item['title']}\n"
                f"Категория: {item['category']}\n"
                f"nmID: {item['nm_id']}\n"
                f"Карточка: {item['url']}"
            )

            if item["image"]:
                try:
                    await update.message.reply_photo(photo=item["image"], caption=text)
                    await context.application.create_task(_sleep_async(0.5))
                    continue
                except Exception as e:
                    logger.warning("Не удалось отправить фото %s: %s", item["image"], e)

            await update.message.reply_text(text)
            await context.application.create_task(_sleep_async(0.3))

    except Exception as e:
        logger.exception("Ошибка /novinki")
        await update.message.reply_text(f"Ошибка при загрузке карточек: {e}")


async def _sleep_async(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


def main() -> None:
    if not TOKEN or TOKEN == "PASTE_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        raise RuntimeError("Укажи TG_BOT_TOKEN или вставь токен Telegram в TOKEN")

    if not WB_API_TOKEN or WB_API_TOKEN == "PASTE_YOUR_WB_API_TOKEN_HERE":
        raise RuntimeError("Укажи WB_API_TOKEN или вставь токен WB API в WB_API_TOKEN")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()