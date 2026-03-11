import os
import re
import asyncio
import logging
from typing import List, Dict, Optional

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

TG_TOKEN = os.getenv("TG_BOT_TOKEN")

SELLER_URL = "https://www.wildberries.ru/seller/92351?sort=newly&page=1"
LIMIT = 20

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wb_bot")


def extract_nm_id(url: str) -> Optional[int]:
    m = re.search(r"/catalog/(\d+)/detail\.aspx", url)
    if m:
        return int(m.group(1))
    return None


def build_image_url(nm_id: int):
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/1.webp"


async def scrape_products():
    results: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(SELLER_URL)
        await page.wait_for_timeout(5000)

        links = await page.locator("a[href*='/catalog/']").evaluate_all(
            "els => els.map(e => e.href)"
        )

        seen = set()

        for href in links:
            nm = extract_nm_id(href)
            if not nm or nm in seen:
                continue

            seen.add(nm)

            results.append({
                "nm_id": nm,
                "url": href,
                "image": build_image_url(nm)
            })

            if len(results) >= LIMIT:
                break

        await browser.close()

    return results


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши /novinki")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("Собираю товары...")

    items = await scrape_products()

    for i, item in enumerate(items, 1):

        text = f"{i}. {item['url']}"

        await update.message.reply_photo(
            photo=item["image"],
            caption=text
        )

        await asyncio.sleep(0.6)


def main():

    if not TG_TOKEN:
        raise RuntimeError("Добавь TG_BOT_TOKEN в Railway Variables")

    app = Application.builder().token(TG_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    print("Bot started")

    app.run_polling()


if __name__ == "__main__":
    main()