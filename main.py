import os
import re
import asyncio
import logging
from typing import List, Dict, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

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


def build_image_url(nm_id: int) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/1.webp"


async def scrape_products() -> List[Dict]:
    results: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1440, "height": 2200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
        )

        page = await context.new_page()
        page.set_default_timeout(20000)

        try:
            logger.info("Открываю страницу продавца")
            await page.goto(SELLER_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            selectors = [
                "a.product-card__link",
                "article a[href*='/catalog/'][href*='/detail.aspx']",
                "a[href*='/catalog/'][href*='/detail.aspx']",
            ]

            found_selector = None
            for selector in selectors:
                try:
                    await page.wait_for_selector(selector, timeout=8000)
                    found_selector = selector
                    logger.info("Найден селектор: %s", selector)
                    break
                except PlaywrightTimeoutError:
                    logger.info("Селектор не найден: %s", selector)

            if not found_selector:
                html_preview = await page.content()
                raise RuntimeError(
                    "Карточки товаров не найдены на странице. "
                    f"Длина HTML: {len(html_preview)}"
                )

            for _ in range(4):
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(1200)

            links = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").evaluate_all(
                """elements => elements.map(el => el.href).filter(Boolean)"""
            )

            logger.info("Найдено ссылок: %s", len(links))

            seen = set()

            for href in links:
                nm_id = extract_nm_id(href)
                if not nm_id or nm_id in seen:
                    continue

                seen.add(nm_id)
                results.append({
                    "nm_id": nm_id,
                    "url": href,
                    "image": build_image_url(nm_id),
                })

                if len(results) >= LIMIT:
                    break

            logger.info("Собрано товаров: %s", len(results))
            return results

        finally:
            await context.close()
            await browser.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши /novinki")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("Собираю товары...")

    try:
        await status_msg.edit_text("Открываю страницу магазина...")
        items = await asyncio.wait_for(scrape_products(), timeout=90)

        if not items:
            await status_msg.edit_text("Не удалось найти товары на странице продавца.")
            return

        await status_msg.edit_text(f"Нашёл {len(items)} товаров, отправляю...")

        for i, item in enumerate(items, 1):
            text = f"{i}. {item['url']}"

            try:
                await update.message.reply_photo(
                    photo=item["image"],
                    caption=text
                )
            except Exception:
                await update.message.reply_text(text)

            await asyncio.sleep(0.6)

        await status_msg.edit_text("Готово.")

    except asyncio.TimeoutError:
        logger.exception("Таймаут парсинга")
        await status_msg.edit_text("Ошибка: парсинг завис по таймауту.")
    except Exception as e:
        logger.exception("Ошибка парсинга")
        await status_msg.edit_text(f"Ошибка: {e}")


def main():
    if not TG_TOKEN:
        raise RuntimeError("Добавь TG_BOT_TOKEN в Railway Variables")

    app = Application.builder().token(TG_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()