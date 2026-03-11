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

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["C"].width = 100

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(path)
    return path


async def collect_product_links(page, limit: int) -> List[str]:
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
            logger.info("Найден селектор списка товаров: %s", selector)
            break
        except PlaywrightTimeoutError:
            logger.info("Селектор не найден: %s", selector)

    if not found_selector:
        raise RuntimeError("На странице продавца не найдены карточки товаров")

    for _ in range(5):
        await page.mouse.wheel(0, 2800)
        await page.wait_for_timeout(1200)

    links = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").evaluate_all(
        """elements => elements.map(el => el.href).filter(Boolean)"""
    )

    result = []
    seen = set()

    for href in links:
        nm_id = extract_nm_id(href)
        if not nm_id or nm_id in seen:
            continue

        seen.add(nm_id)
        result.append(href)

        if len(result) >= limit:
            break

    return result


async def parse_product_page(context, url: str) -> Dict:
    page = await context.new_page()
    page.set_default_timeout(20000)

    try:
        logger.info("Открываю карточку: %s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Наименование
        title = None
        title_selectors = [
            "h1",
            "h1.product-page__title",
            ".product-page__title",
            "[data-link='text{:product^name}']",
        ]

        for selector in title_selectors:
            try:
                el = page.locator(selector).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip()
                    if text:
                        title = " ".join(text.split())
                        break
            except Exception:
                continue

        # Категория
        category = None
        category_selectors = [
            "a.breadcrumbs__link",
            ".breadcrumbs__item",
            ".breadcrumbs a",
        ]

        breadcrumb_texts = []
        for selector in category_selectors:
            try:
                nodes = page.locator(selector)
                count = await nodes.count()
                if count > 0:
                    for i in range(count):
                        txt = (await nodes.nth(i).inner_text()).strip()
                        txt = " ".join(txt.split())
                        if txt and txt not in breadcrumb_texts:
                            breadcrumb_texts.append(txt)
                    if breadcrumb_texts:
                        break
            except Exception:
                continue

        if breadcrumb_texts:
            # обычно последняя крошка - товар, предпоследняя - категория
            if len(breadcrumb_texts) >= 2:
                category = breadcrumb_texts[-2]
            else:
                category = breadcrumb_texts[0]

        # Картинка - берем именно src/currentSrc с товара
        image = None
        image_selectors = [
            ".swiper-slide-active img",
            ".product-page__slider img",
            ".photo-zoom__preview img",
            ".j-image-container img",
            "img[src*='wbbasket']",
        ]

        for selector in image_selectors:
            try:
                img = page.locator(selector).first
                if await img.count() > 0:
                    src = await img.get_attribute("src")
                    current_src = await img.get_attribute("currentSrc")
                    candidate = current_src or src
                    if candidate:
                        if candidate.startswith("//"):
                            candidate = "https:" + candidate
                        elif candidate.startswith("/"):
                            candidate = "https://www.wildberries.ru" + candidate
                        image = candidate
                        break
            except Exception:
                continue

        # Фолбэки
        nm_id = extract_nm_id(url)

        if not title and nm_id:
            title = f"Товар {nm_id}"

        if not category:
            category = "Категория не найдена"

        if not image and nm_id:
            vol = nm_id // 100000
            part = nm_id // 1000
            image = f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/1.webp"

        return {
            "title": title,
            "category": category,
            "image": image,
            "url": url,
            "nm_id": nm_id,
        }

    finally:
        await page.close()


async def scrape_products() -> List[Dict]:
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

        seller_page = await context.new_page()
        seller_page.set_default_timeout(20000)

        try:
            logger.info("Открываю страницу продавца")
            await seller_page.goto(SELLER_URL, wait_until="domcontentloaded", timeout=30000)
            await seller_page.wait_for_timeout(5000)

            product_links = await collect_product_links(seller_page, LIMIT)
            logger.info("Собрано ссылок на товары: %s", len(product_links))

            if not product_links:
                raise RuntimeError("Не удалось собрать ссылки на товары")

            results = []

            for url in product_links[:LIMIT]:
                try:
                    item = await parse_product_page(context, url)
                    results.append(item)
                    await asyncio.sleep(0.7)
                except Exception as e:
                    logger.warning("Не удалось обработать %s: %s", url, e)

            return results

        finally:
            await seller_page.close()
            await context.close()
            await browser.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши /novinki и я пришлю Excel-файл с 20 товарами")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("Собираю товары...")

    try:
        items = await asyncio.wait_for(scrape_products(), timeout=180)

        if not items:
            await status_msg.edit_text("Не удалось найти товары.")
            return

        await status_msg.edit_text(f"Собрано {len(items)} товаров. Формирую Excel...")

        file_path = save_to_xlsx(items, OUTPUT_XLSX)

        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="wb_products.xlsx",
                caption="Готово: файл с товарами WB"
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