import os
import re
import asyncio
import logging
from typing import List, Dict, Optional

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "PASTE_YOUR_TELEGRAM_TOKEN")

SELLER_URL = "https://www.wildberries.ru/seller/92351?sort=newly&page=1"
LIMIT = 20

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

WB_CARD_URL = "https://card.wb.ru/cards/v2/detail"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("wb_browser_bot")

session = requests.Session()
session.headers.update(HEADERS)


def extract_nm_id(url: str) -> Optional[int]:
    match = re.search(r"/catalog/(\d+)/detail\.aspx", url)
    if match:
        return int(match.group(1))
    return None


def build_image_url(nm_id: int, image_index: int = 1) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{image_index}.webp"


def get_card_detail(nm_id: int) -> Dict:
    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "spp": 30,
        "nm": nm_id,
    }

    response = session.get(WB_CARD_URL, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    for path in [
        ("data", "products"),
        ("products",),
        ("data", "cards"),
        ("cards",),
    ]:
        node = data
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and isinstance(node, list) and node:
            return node[0]

    return {}


def extract_title(detail: Dict, fallback: str = "") -> str:
    return (
        detail.get("name")
        or detail.get("title")
        or detail.get("imt_name")
        or detail.get("goodsName")
        or fallback
        or "Без названия"
    )


def extract_category(detail: Dict) -> str:
    return (
        detail.get("subject")
        or detail.get("subjectName")
        or detail.get("entity")
        or detail.get("category")
        or detail.get("root")
        or "Категория не найдена"
    )


async def scrape_20_products_from_seller(url: str, limit: int = 20) -> List[Dict]:
    results: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1440, "height": 2200},
            user_agent=HEADERS["User-Agent"],
            locale="ru-RU",
        )

        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)

            selectors = [
                "a.product-card__link",
                "article a[href*='/catalog/'][href*='/detail.aspx']",
                "a[href*='/catalog/'][href*='/detail.aspx']",
            ]

            found = False
            for selector in selectors:
                try:
                    await page.wait_for_selector(selector, timeout=15000)
                    found = True
                    break
                except PlaywrightTimeoutError:
                    continue

            if not found:
                raise RuntimeError("На странице продавца не найдены карточки товаров")

            for _ in range(4):
                await page.mouse.wheel(0, 3000)
                await page.wait_for_timeout(1500)

            links = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").evaluate_all(
                """elements => elements.map(el => ({
                    href: el.href,
                    text: (el.innerText || '').trim()
                }))"""
            )

            seen = set()

            for item in links:
                href = item.get("href")
                if not href:
                    continue

                nm_id = extract_nm_id(href)
                if not nm_id or nm_id in seen:
                    continue

                seen.add(nm_id)

                title_dom = " ".join((item.get("text") or "").split())
                results.append({
                    "nm_id": nm_id,
                    "url": href,
                    "title_dom": title_dom[:180] if title_dom else "",
                })

                if len(results) >= limit:
                    break

        finally:
            await context.close()
            await browser.close()

    return results


async def fetch_products(limit: int = 20) -> List[Dict]:
    raw_items = await scrape_20_products_from_seller(SELLER_URL, limit=limit)

    if not raw_items:
        raise RuntimeError("Не удалось собрать товары со страницы продавца")

    final_items: List[Dict] = []

    for item in raw_items[:limit]:
        nm_id = item["nm_id"]

        detail = {}
        try:
            detail = get_card_detail(nm_id)
        except Exception as e:
            logger.warning("Не удалось получить detail для %s: %s", nm_id, e)

        title = extract_title(detail, fallback=item.get("title_dom", ""))
        category = extract_category(detail)
        image = build_image_url(nm_id, 1)

        final_items.append({
            "nm_id": nm_id,
            "title": title,
            "category": category,
            "image": image,
            "url": item["url"],
        })

    return final_items


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши /novinki чтобы получить 20 товаров магазина")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Загружаю товары...")

    try:
        items = await fetch_products(limit=LIMIT)

        for i, item in enumerate(items, 1):
            text = (
                f"{i}. {item['title']}\n"
                f"Категория: {item['category']}\n"
                f"{item['url']}"
            )

            try:
                await update.message.reply_photo(
                    photo=item["image"],
                    caption=text
                )
            except Exception:
                await update.message.reply_text(text)

            await asyncio.sleep(0.7)

    except Exception as e:
        logger.exception("Ошибка парсинга")
        await update.message.reply_text(f"Ошибка: {e}")


def main():
    if TG_TOKEN == "PASTE_YOUR_TELEGRAM_TOKEN":
        raise RuntimeError("Вставь Telegram токен в переменную TG_BOT_TOKEN")

    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()