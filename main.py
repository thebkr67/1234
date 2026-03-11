import os
import re
import time
import logging
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN") or "PASTE_YOUR_TOKEN"

SELLER_ID = 92351
LIMIT = 20

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v13/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wb_bot")


def build_image_url(nm_id):
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/1.webp"


def fetch_products():
    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "page": 1,
        "sort": "newly",
        "supplier": SELLER_ID,
    }

    r = requests.get(SEARCH_URL, params=params, headers=HEADERS)
    data = r.json()

    products = data.get("data", {}).get("products", [])

    result = []

    for p in products[:LIMIT]:

        nm_id = p.get("id")
        title = p.get("name")
        category = p.get("subject")

        result.append({
            "nm_id": nm_id,
            "title": title,
            "category": category,
            "image": build_image_url(nm_id),
            "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        })

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Команда /novinki покажет 20 новинок магазина")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("Парсим товары...")

    try:

        items = fetch_products()

        for i, item in enumerate(items, 1):

            text = f"""{i}. {item['title']}
Категория: {item['category']}
{item['url']}"""

            await update.message.reply_photo(item["image"], caption=text)

            time.sleep(0.5)

    except Exception as e:

        await update.message.reply_text(f"Ошибка: {e}")


def main():

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    print("Bot started")

    app.run_polling()


if __name__ == "__main__":
    main()