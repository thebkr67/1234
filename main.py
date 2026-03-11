import os
import re
import asyncio
import logging
from typing import List, Dict, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

TG_TOKEN = os.getenv("TG_BOT_TOKEN")

SELLER_URL = "https://www.wildberries.ru/seller/92351?sort=newly&page=1"
LIMIT = 20
OUTPUT_XLSX = "wb_products.xlsx"

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


def save_to_xlsx(items: List[Dict], path: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Товары WB"

    headers = ["Наименование", "Категория", "Картинка"]
    ws.append(headers)

    for item in items:
        ws.append([
            item.get("title", ""),
            item.get("category", ""),
            item.get("image", ""),
        ])

    # Оформление заголовка
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Ширина колонок
    widths = {
        "A": 45,
        "B": 30,
        "C": 80,
    }

    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    # Выравнивание
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(path)
    return path


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
                raise RuntimeError("Карточки товаров не найдены на странице")

            for _ in range(4):
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(1200)

            cards = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").evaluate_all(
                """
                elements => elements.map(el => ({
                    href: el.href,
                    text: (el.innerText || '').trim()
                }))
                """
            )

            logger.info("Найдено карточек: %s", len(cards))

            seen = set()

            for card in cards:
                href = card.get("href")
                if not href:
                    continue

                nm_id = extract_nm_id(href)
                if not nm_id or nm_id in seen:
                    continue

                seen.add(nm_id)

                title = " ".join((card.get("text") or "").split())
                if not title:
                    title = f"Товар {nm_id}"

                results.append({
                    "title": title[:180],
                    "category": "Категория не найдена",
                    "image": build_image_url(nm_id),
                    "url": href,
                    "nm_id": nm_id,
                })

                if len(results) >= LIMIT:
                    break

            return results

        finally:
            await context.close()
            await browser.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши /novinki и я пришлю Excel-файл с 20 товарами")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("Собираю товары...")

    try:
        items = await asyncio.wait_for(scrape_products(), timeout=90)

        if not items:
            await status_msg.edit_text("Не удалось найти товары.")
            return

        file_path = save_to_xlsx(items, OUTPUT_XLSX)

        await status_msg.edit_text("Формирую Excel-файл...")

        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="wb_products.xlsx",
                caption="Готово: 20 товаров в Excel"
            )

        await status_msg.edit_text("Готово.")

    except asyncio.TimeoutError:
        logger.exception("Таймаут парсинга")
        await status_msg.edit_text("Ошибка: парсинг завис по таймауту.")
    except Exception as e:
        logger.exception("Ошибка")
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