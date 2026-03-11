import os
import re
import asyncio
import logging
from typing import List, Dict, Optional

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.drawing.image import Image as XLImage
from PIL import Image as PILImage

TG_TOKEN = os.getenv("TG_BOT_TOKEN")

SELLER_URL = "https://www.wildberries.ru/seller/92351?sort=newly&page=1"
LIMIT = 20
OUTPUT_XLSX = "wb_products.xlsx"
TEMP_IMG_DIR = "temp_images"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wb_bot")


def extract_nm_id(url: str) -> Optional[int]:
    m = re.search(r"/catalog/(\d+)/detail\.aspx", url)
    if m:
        return int(m.group(1))
    return None


def download_image(image_url: str, file_path: str) -> Optional[str]:
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        with open(file_path, "wb") as f:
            f.write(response.content)

        return file_path
    except Exception as e:
        logger.warning("Не удалось скачать картинку %s: %s", image_url, e)
        return None


def prepare_excel_image(image_path: str, max_width: int = 160, max_height: int = 160) -> Optional[str]:
    try:
        img = PILImage.open(image_path)
        img.thumbnail((max_width, max_height))

        prepared_path = image_path.rsplit(".", 1)[0] + "_prepared.png"
        img.save(prepared_path, format="PNG")
        return prepared_path
    except Exception as e:
        logger.warning("Не удалось подготовить картинку %s: %s", image_path, e)
        return None


def save_to_xlsx(items: List[Dict], path: str) -> str:
    os.makedirs(TEMP_IMG_DIR, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Товары WB"

    headers = ["Наименование", "Категория", "Картинка"]
    ws.append(headers)

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 28

    current_row = 2

    for idx, item in enumerate(items, start=1):
        ws.cell(row=current_row, column=1, value=item.get("title", ""))
        ws.cell(row=current_row, column=2, value=item.get("category", ""))

        ws.cell(row=current_row, column=1).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row=current_row, column=2).alignment = Alignment(vertical="top", wrap_text=True)

        image_url = item.get("image")
        if image_url:
            raw_path = os.path.join(TEMP_IMG_DIR, f"img_{idx}.jpg")
            downloaded = download_image(image_url, raw_path)

            if downloaded:
                prepared = prepare_excel_image(downloaded)
                if prepared:
                    try:
                        img = XLImage(prepared)
                        ws.add_image(img, f"C{current_row}")
                    except Exception as e:
                        logger.warning("Не удалось вставить картинку в Excel: %s", e)

        ws.row_dimensions[current_row].height = 130
        current_row += 1

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

    # сначала собираем ссылки без скролла, чтобы не потерять первый товар
    initial_links = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").evaluate_all(
        """elements => elements.map(el => el.href).filter(Boolean)"""
    )

    collected_links = list(initial_links)

    # потом дособираем после скролла
    for _ in range(5):
        await page.mouse.wheel(0, 2800)
        await page.wait_for_timeout(1200)

        new_links = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").evaluate_all(
            """elements => elements.map(el => el.href).filter(Boolean)"""
        )

        collected_links.extend(new_links)

        if len(collected_links) >= limit * 3:
            break

    result = []
    seen = set()

    for href in collected_links:
        nm_id = extract_nm_id(href)
        if not nm_id:
            continue

        if nm_id in seen:
            continue

        seen.add(nm_id)
        result.append(href)

        if len(result) >= limit:
            break

    logger.info("Итоговое количество уникальных ссылок: %s", len(result))
    if result:
        logger.info("Первая ссылка: %s", result[0])

    return result


async def get_text_by_selectors(page, selectors: List[str]) -> Optional[str]:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                text = await locator.inner_text()
                text = " ".join(text.split())
                if text:
                    return text
        except Exception:
            continue
    return None


async def get_image_by_selectors(page, selectors: List[str]) -> Optional[str]:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                src = await locator.get_attribute("src")
                current_src = await locator.get_attribute("currentSrc")
                candidate = current_src or src
                if candidate:
                    if candidate.startswith("//"):
                        candidate = "https:" + candidate
                    elif candidate.startswith("/"):
                        candidate = "https://www.wildberries.ru" + candidate
                    return candidate
        except Exception:
            continue
    return None


async def parse_product_page(context, url: str) -> Dict:
    page = await context.new_page()
    page.set_default_timeout(20000)

    try:
        logger.info("Открываю карточку: %s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        title = await get_text_by_selectors(page, [
            "h3",
            ".product-page h3",
            "[class*='product'] h3",
        ])

        category = await get_text_by_selectors(page, [
            "span.categoryLinkCategory--VSJ8c",
            "[class*='categoryLinkCategory']",
        ])

        image = await get_image_by_selectors(page, [
            ".swiper-slide-active img",
            ".product-page__slider img",
            ".photo-zoom__preview img",
            ".j-image-container img",
            "img[src*='wbbasket']",
            "img",
        ])

        nm_id = extract_nm_id(url)

        if not title:
            title = f"Товар {nm_id}" if nm_id else "Без названия"

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