import os
import time
import random
import logging
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN") or "PASTE_YOUR_BOT_TOKEN_HERE"

SELLER_ID = 92351
PAGE = 1
LIMIT = 20

WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v13/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://www.wildberries.ru/seller/{SELLER_ID}?sort=newly&page={PAGE}",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("wb_bot")


def build_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


SESSION = build_session()


def safe_get(dct: Dict[str, Any], *keys, default=None):
    cur = dct
    for key in keys:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def extract_products_from_search_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = [
        safe_get(payload, "data", "products", default=[]),
        safe_get(payload, "products", default=[]),
        safe_get(payload, "data", "cards", default=[]),
        safe_get(payload, "cards", default=[]),
        safe_get(payload, "data", "catalog", "products", default=[]),
    ]
    for item in candidates:
        if isinstance(item, list) and item:
            return item
    return []


def extract_title(product: Dict[str, Any]) -> str:
    return (
        product.get("name")
        or product.get("title")
        or product.get("imt_name")
        or product.get("goodsName")
        or "Без названия"
    )


def extract_category(product: Dict[str, Any]) -> str:
    return (
        product.get("subject")
        or product.get("subjectName")
        or product.get("entity")
        or product.get("category")
        or product.get("root")
        or "Категория не найдена"
    )


def get_nm_id(product: Dict[str, Any]) -> Optional[int]:
    nm = product.get("id") or product.get("nmId") or product.get("nmID")
    try:
        return int(nm) if nm is not None else None
    except Exception:
        return None


def build_wb_image_url(nm_id: int, image_index: int = 1) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{image_index}.webp"


def find_image_url_in_product(product: Dict[str, Any]) -> Optional[str]:
    for key in ["image", "img", "imageUrl", "image_url", "thumb", "photo"]:
        val = product.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val

    for key in ["images", "photos", "mediaFiles"]:
        val = product.get(key)
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                for subkey in ["big", "original", "url", "img", "image"]:
                    v = first.get(subkey)
                    if isinstance(v, str) and v.startswith("http"):
                        return v

    return None


def request_with_backoff(url: str, params: Dict[str, Any], attempts: int = 6) -> Dict[str, Any]:
    for attempt in range(1, attempts + 1):
        try:
            resp = SESSION.get(url, params=params, timeout=25)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep_time = int(retry_after)
                else:
                    sleep_time = min(60, (2 ** attempt) + random.uniform(0.5, 2.5))

                logger.warning("WB вернул 429. Жду %.1f сек. Попытка %s/%s", sleep_time, attempt, attempts)
                time.sleep(sleep_time)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as e:
            if attempt == attempts:
                raise

            sleep_time = min(60, (2 ** attempt) + random.uniform(0.5, 2.5))
            logger.warning("Ошибка запроса: %s. Жду %.1f сек. Попытка %s/%s", e, sleep_time, attempt, attempts)
            time.sleep(sleep_time)

    raise RuntimeError("Не удалось получить ответ от WB после повторов")


def fetch_new_products_from_seller(
    seller_id: int,
    page: int = 1,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    params = {
        "ab_testing": "false",
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "hide_dtype": 13,
        "lang": "ru",
        "page": page,
        "query": "",
        "resultset": "catalog",
        "sort": "newly",
        "spp": 30,
        "supplier": seller_id,
    }

    payload = request_with_backoff(WB_SEARCH_URL, params=params)
    raw_products = extract_products_from_search_payload(payload)

    result = []

    for product in raw_products[:limit]:
        nm_id = get_nm_id(product)
        title = extract_title(product)
        category = extract_category(product)

        image = find_image_url_in_product(product)
        if not image and nm_id:
            image = build_wb_image_url(nm_id, 1)

        result.append({
            "nm_id": nm_id,
            "title": title,
            "category": category,
            "image": image,
            "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" if nm_id else None,
        })

        # мягкая пауза между обработкой товаров
        time.sleep(random.uniform(0.4, 1.0))

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Команда /novinki покажет 20 новинок продавца 92351."
    )


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Собираю 20 новинок магазина...")

    try:
        items = fetch_new_products_from_seller(
            seller_id=SELLER_ID,
            page=PAGE,
            limit=LIMIT,
        )

        if not items:
            await update.message.reply_text(
                "Не удалось получить товары. WB мог изменить структуру ответа или временно ограничил доступ."
            )
            return

        for i, item in enumerate(items, start=1):
            text = (
                f"{i}. {item['title']}\n"
                f"Категория: {item['category']}\n"
                f"nmID: {item['nm_id']}\n"
                f"Карточка: {item['url']}"
            )

            image_url = item.get("image")
            if image_url:
                try:
                    await update.message.reply_photo(photo=image_url, caption=text)
                    time.sleep(random.uniform(0.8, 1.5))
                    continue
                except Exception as e:
                    logger.warning("Не удалось отправить фото %s: %s", image_url, e)

            await update.message.reply_text(text)
            time.sleep(random.uniform(0.5, 1.0))

    except Exception as e:
        logger.exception("Ошибка в /novinki")
        await update.message.reply_text(f"Ошибка при парсинге: {e}")


def main() -> None:
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Укажи токен в TG_BOT_TOKEN или прямо в TOKEN")

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("novinki", novinki))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()