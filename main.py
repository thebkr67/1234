import os
import re
import time
import asyncio
import logging
from typing import Any, Dict, List, Optional

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

TOKEN = os.getenv("TG_BOT_TOKEN") or "PASTE_YOUR_BOT_TOKEN_HERE"

SELLER_URL = "https://www.wildberries.ru/seller/92351?sort=newly&page=1"
LIMIT = 20

WB_CARD_URL = "https://card.wb.ru/cards/v2/detail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.wildberries.ru/",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("wb_bot")

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def extract_nm_id(url: str) -> Optional[int]:
    m = re.search(r"/catalog/(\d+)/detail\.aspx", url)
    if m:
        return int(m.group(1))
    return None


def build_image_url(nm_id: int, image_index: int = 1) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{image_index}.webp"


def safe_get(dct: Dict[str, Any], *keys, default=None):
    cur = dct
    for key in keys:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def get_card_detail(nm_id: int) -> Dict[str, Any]:
    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "spp": 30,
        "nm": nm_id,
    }

    resp = SESSION.get(WB_CARD_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    for path in [
        ("data", "products"),
        ("products",),
        ("data", "cards"),
        ("cards",),
    ]:
        node = data
        ok = True
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                ok = False
                break
        if ok and isinstance(node, list) and node:
            return node[0]

    return {}


def extract_title_from_detail(detail: Dict[str, Any]) -> Optional[str]:
    return (
        detail.get("name")
        or detail.get("title")
        or detail.get("imt_name")
        or detail.get("goodsName")
    )


def extract_category_from_detail(detail: Dict[str, Any]) -> Optional[str]:
    return (
        detail.get("subject")
        or detail.get("subjectName")
        or detail.get("entity")
        or detail.get("category")
        or detail.get("root")
    )


async def scrape_seller_products(url: str, limit: int = 20) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 2200},
            user_agent=HEADERS["User-Agent"],
            locale="ru-RU",
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)

            possible_selectors = [
                "a.product-card__link",
                "article.product-card a[href*='/catalog/']",
                "a[href*='/catalog/'][href*='/detail.aspx']",
            ]

            found = False
            for selector in possible_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=12000)
                    found = True
                    break
                except PlaywrightTimeoutError:
                    continue

            if not found:
                raise RuntimeError("На странице продавца не найдены карточки товаров")

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2500)

            anchors = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").element_handles()
            seen = set()

            for a in anchors:
                href = await a.get_attribute("href")
                if not href:
                    continue

                full_url = href if href.startswith("http") else f"https://www.wildberries.ru{href}"
                nm_id = extract_nm_id(full_url)

                if not nm_id or nm_id in seen:
                    continue

                seen.add(nm_id)

                title_dom = None
                try:
                    article = await a.evaluate_handle(
                        "el => el.closest('article') || el.closest('div')"
                    )
                    text = await article.text_content()
                    if text:
                        text = " ".join(text.split())
                        if text:
                            title_dom = text[:180]
                except Exception:
                    pass

                products.append({
                    "nm_id": nm_id,
                    "url": full_url,
                    "title_dom": title_dom,
                })

                if len(products) >= limit:
                    break

        finally:
            await context.close()
            await browser.close()

    return products


async def fetch_products(limit: int = 20) -> List[Dict[str, Any]]:
    raw = await scrape_seller_products(SELLER_URL, limit=limit)
    result = []

    for item in raw:
        nm_id = item["nm_id"]
        detail = {}

        try:
            detail = get_card_detail(nm_id)
        except Exception as e:
            logger.warning("Не удалось получить detail для %s: %s", nm_id, e)

        title = extract_title_from_detail(detail) or item.get("title_dom") or f"Товар {nm_id}"
        category = extract_category_from_detail(detail) or "Категория не найдена"
        image = build_image_url(nm_id, 1)

        result.append({
            "nm_id": nm_id,
            "title": title,
            "category": category,
            "image": image,
            "url": item["url"],
        })

        time.sleep(0.3)

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Команда /novinki покажет 20 новинок магазина WB."
    )


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Собираю товары...")

    try:
        items = await fetch_products(limit=LIMIT)

        if not items:
            await update.message.reply_text("Не удалось получить товары со страницы продавца.")
            return

        for i, item in enumerate(items, start=1):
            text = (
                f"{i}. {item['title']}\n"
                f"Категория: {item['category']}\n"
                f"Карточка: {item['url']}"
            )

            try:
                await update.message.reply_photo(photo=item["image"], caption=text)
            except Exception as e:
                logger.warning("Не удалось отправить фото %s: %s", item["image"], e)
                await update.message.reply_text(text)

            await asyncio.sleep(0.8)

    except Exception as e:
        logger.exception("Ошибка /novinki")
        await update.message.reply_text(f"Не удалось получить товары: {e}")


def main() -> None:
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Укажи TG_BOT_TOKEN или вставь токен в TOKEN")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()