import os
import time
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
        "supplier": SELLER_ID
    }

    r = requests.get(SEARCH_URL, params=params, headers=HEADERS)
    data = r.json()

    products = data.get("data", {}).get("products", [])

    result = []

    for p in products[:LIMIT]:

        nm_id = p.get("id")

        result.append({
            "title": p.get("name"),
            "category": p.get("subject"),
            "image": build_image_url(nm_id),
            "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        })

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

            await update.message.reply_photo(
                photo=item["image"],
                caption=text
            )

            time.sleep(0.4)

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