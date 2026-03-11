import os
import time
import random
import logging
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN") or "PASTE_YOUR_TELEGRAM_TOKEN"

SELLER_ID = 92351
LIMIT = 20

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v13/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wb_bot")

SESSION = requests.Session()


def build_image_url(nm_id):
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/1.webp"


def request_with_retry():

    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "page": 1,
        "sort": "newly",
        "supplier": SELLER_ID,
        "resultset": "catalog",
        "lang": "ru",
        "spp": 30,
    }

    for attempt in range(5):

        response = SESSION.get(
            SEARCH_URL,
            params=params,
            headers=HEADERS,
            timeout=20
        )

        if response.status_code == 429:

            sleep_time = random.uniform(5, 10)

            logger.warning(f"WB вернул 429, жду {sleep_time:.1f} сек")

            time.sleep(sleep_time)

            continue

        response.raise_for_status()

        return response.json()

    raise RuntimeError("WB долго блокирует запросы (429)")


def fetch_products():

    data = request_with_retry()

    products = data.get("data", {}).get("products", [])

    if not products:
        raise RuntimeError("WB не вернул товары")

    result = []

    for p in products[:LIMIT]:

        nm_id = p.get("id")

        result.append({
            "title": p.get("name", "Без названия"),
            "category": p.get("subject", "Категория не найдена"),
            "image": build_image_url(nm_id),
            "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        })

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Напиши /novinki чтобы получить 20 товаров магазина"
    )


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("Загружаю товары...")

    try:

        items = fetch_products()

        for i, item in enumerate(items, 1):

            text = f"""{i}. {item['title']}
Категория: {item['category']}
{item['url']}"""

            try:
                await update.message.reply_photo(
                    photo=item["image"],
                    caption=text
                )
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