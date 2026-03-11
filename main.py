import os
import time
import random
import logging
from typing import Any, Dict, List

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN") or "PASTE_YOUR_TELEGRAM_TOKEN"

SELLER_ID = 92351
LIMIT = 20

# Более актуальный витринный endpoint
SEARCH_URL = "https://u-search.wb.ru/exactmatch/ru/common/v18/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wb_bot")

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def build_image_url(nm_id: int) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/1.webp"


def extract_products(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = [
        data.get("data", {}).get("products", []),
        data.get("products", []),
        data.get("data", {}).get("cards", []),
        data.get("cards", []),
        data.get("data", {}).get("catalog", {}).get("products", []),
    ]
    for candidate in candidates:
        if isinstance(candidate, list) and candidate:
            return candidate
    return []


def request_with_retry() -> Dict[str, Any]:
    params = {
        "ab_testid": "new_benefit_sort",
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "inheritFilters": "false",
        "lang": "ru",
        "page": 1,
        "query": "",
        "resultset": "catalog",
        "sort": "newly",
        "spp": 30,
        "supplier": SELLER_ID,
        "suppressSpellcheck": "false",
    }

    last_error = None

    for attempt in range(1, 6):
        try:
            response = SESSION.get(SEARCH_URL, params=params, timeout=20)

            if response.status_code == 429:
                sleep_time = random.uniform(5, 10)
                logger.warning("WB вернул 429, жду %.1f сек", sleep_time)
                time.sleep(sleep_time)
                continue

            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" not in content_type:
                preview = response.text[:300].strip()
                raise RuntimeError(
                    f"WB вернул не JSON, а {content_type or 'неизвестный тип'}. "
                    f"Начало ответа: {preview}"
                )

            return response.json()

        except Exception as e:
            last_error = e
            if attempt < 5:
                sleep_time = random.uniform(3, 7)
                logger.warning("Попытка %s не удалась: %s. Жду %.1f сек", attempt, e, sleep_time)
                time.sleep(sleep_time)
            else:
                break

    raise RuntimeError(f"Не удалось получить ответ от WB: {last_error}")


def fetch_products() -> List[Dict[str, Any]]:
    data = request_with_retry()
    products = extract_products(data)

    if not products:
        raise RuntimeError("WB вернул JSON, но список товаров пустой")

    result = []

    for p in products[:LIMIT]:
        nm_id = p.get("id") or p.get("nmId") or p.get("nmID")
        if not nm_id:
            continue

        try:
            nm_id = int(nm_id)
        except Exception:
            continue

        title = (
            p.get("name")
            or p.get("title")
            or p.get("imt_name")
            or "Без названия"
        )

        category = (
            p.get("subject")
            or p.get("subjectName")
            or p.get("entity")
            or "Категория не найдена"
        )

        result.append({
            "title": title,
            "category": category,
            "image": build_image_url(nm_id),
            "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
        })

    if not result:
        raise RuntimeError("Из ответа WB не удалось собрать ни одного товара")

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши /novinki чтобы получить 20 товаров магазина")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Загружаю товары...")

    try:
        items = fetch_products()

        for i, item in enumerate(items, 1):
            text = f"""{i}. {item['title']}
Категория: {item['category']}
{item['url']}"""

            try:
                await update.message.reply_photo(photo=item["image"], caption=text)
            except Exception:
                await update.message.reply_text(text)

            time.sleep(random.uniform(0.5, 1.2))

    except Exception as e:
        logger.exception("Ошибка")
        await update.message.reply_text(f"Ошибка: {e}")


def main():
    if TOKEN == "PASTE_YOUR_TELEGRAM_TOKEN":
        raise RuntimeError("Вставь токен Telegram")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()