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

    headers = ["Наименование", "Категория", "Картинка", "Ссылка"]
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
    ws.column_dimensions["D"].width = 60

    current_row = 2

    for idx, item in enumerate(items, start=1):

        ws.cell(row=current_row, column=1, value=item.get("title", ""))
        ws.cell(row=current_row, column=2, value=item.get("category", ""))

        link = item.get("url", "")
        link_cell = ws.cell(row=current_row, column=4, value=link)
        link_cell.hyperlink = link
        link_cell.style = "Hyperlink"

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
            break
        except PlaywrightTimeoutError:
            pass

    if not found_selector:
        raise RuntimeError("На странице продавца не найдены карточки товаров")

    initial_links = await page.locator("a[href*='/catalog/'][href*='/detail.aspx']").evaluate_all(
        """elements => elements.map(el => el.href).filter(Boolean)"""
    )

    collected_links = list(initial_links)

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
                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    return src
        except Exception:
            continue
    return None


async def parse_product_page(context, url: str) -> Dict:
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        title = await get_text_by_selectors(page, ["h3"])

        category = await get_text_by_selectors(page, [
            "span.categoryLinkCategory--VSJ8c",
            "[class*='categoryLinkCategory']",
        ])

        image = await get_image_by_selectors(page, [
            ".swiper-slide-active img",
            ".product-page__slider img",
            "img[src*='wbbasket']",
            "img",
        ])

        nm_id = extract_nm_id(url)

        if not title:
            title = f"Товар {nm_id}"

        if not category:
            category = "Категория не найдена"

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

        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context()

        page = await context.new_page()

        await page.goto(SELLER_URL)
        await page.wait_for_timeout(5000)

        product_links = await collect_product_links(page, LIMIT)

        results = []

        for url in product_links[:LIMIT]:

            item = await parse_product_page(context, url)

            results.append(item)

            await asyncio.sleep(0.7)

        await browser.close()

        return results


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши /novinki и я пришлю Excel")


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text("Собираю товары...")

    items = await scrape_products()

    file_path = save_to_xlsx(items, OUTPUT_XLSX)

    with open(file_path, "rb") as f:

        await update.message.reply_document(
            document=f,
            filename="wb_products.xlsx",
            caption="Готово"
        )

    await msg.edit_text("Готово")


def main():

    app = Application.builder().token(TG_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))

    app.run_polling()


if __name__ == "__main__":
    main()