import os
import re
import time
import json
import random
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN") or "PASTE_YOUR_BOT_TOKEN_HERE"

SELLER_URL = "https://www.wildberries.ru/seller/92351?sort=newly&page=1"
LIMIT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/json,text/plain,*/*",
    "Referer": "https://www.wildberries.ru/",
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


def request_json(url: str, params: Optional[Dict[str, Any]] = None, attempts: int = 5) -> Dict[str, Any]:
    for attempt in range(1, attempts + 1):
        try:
            resp = SESSION.get(url, params=params, timeout=25)

            if resp.status_code == 429:
                sleep_time = min(60, (2 ** attempt) + random.uniform(0.5, 2.0))
                logger.warning("429 от WB, жду %.1f сек", sleep_time)
                time.sleep(sleep_time)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as e:
            if attempt == attempts:
                raise
            sleep_time = min(30, (2 ** attempt) + random.uniform(0.5, 2.0))
            logger.warning("Ошибка запроса %s, повтор через %.1f сек", e, sleep_time)
            time.sleep(sleep_time)

    raise RuntimeError("Не удалось получить JSON")


def request_text(url: str, attempts: int = 5) -> str:
    for attempt in range(1, attempts + 1):
        try:
            resp = SESSION.get(url, timeout=25)

            if resp.status_code == 429:
                sleep_time = min(60, (2 ** attempt) + random.uniform(0.5, 2.0))
                logger.warning("429 от WB page, жду %.1f сек", sleep_time)
                time.sleep(sleep_time)
                continue

            resp.raise_for_status()
            return resp.text

        except requests.RequestException as e:
            if attempt == attempts:
                raise
            sleep_time = min(30, (2 ** attempt) + random.uniform(0.5, 2.0))
            logger.warning("Ошибка страницы %s, повтор через %.1f сек", e, sleep_time)
            time.sleep(sleep_time)

    raise RuntimeError("Не удалось получить HTML")


def parse_seller_id(url: str) -> Optional[int]:
    m = re.search(r"/seller/(\\d+)", url)
    return int(m.group(1)) if m else None


def parse_page(url: str) -> int:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    try:
        return int(qs.get("page", ["1"])[0])
    except Exception:
        return 1


def parse_sort(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return qs.get("sort", ["newly"])[0]


def build_image_url(nm_id: int, image_index: int = 1) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"https://basket-01.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{image_index}.webp"


def extract_products(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = [
        safe_get(payload, "data", "products", default=[]),
        safe_get(payload, "products", default=[]),
        safe_get(payload, "data", "cards", default=[]),
        safe_get(payload, "cards", default=[]),
    ]
    for c in candidates:
        if isinstance(c, list) and c:
            return c
    return []


def get_title(product: Dict[str, Any]) -> str:
    return (
        product.get("name")
        or product.get("title")
        or product.get("imt_name")
        or "Без названия"
    )


def get_category(product: Dict[str, Any]) -> str:
    return (
        product.get("subject")
        or product.get("subjectName")
        or product.get("entity")
        or product.get("category")
        or "Категория не найдена"
    )


def get_nm_id(product: Dict[str, Any]) -> Optional[int]:
    raw = product.get("id") or product.get("nmId") or product.get("nmID")
    try:
        return int(raw) if raw is not None else None
    except Exception:
        return None


def get_catalog_params_from_page(seller_url: str) -> Dict[str, str]:
    """
    Пытаемся вытащить shard/query из HTML страницы продавца.
    """
    html = request_text(seller_url)

    # Иногда WB кладет данные прямо в HTML/скрипты
    shard_match = re.search(r'"shard":"([^"]+)"', html)
    query_match = re.search(r'"query":"([^"]*)"', html)

    if shard_match:
        shard = shard_match.group(1).replace("\\/", "/")
        query = query_match.group(1).replace("\\/", "/") if query_match else ""
        return {"shard": shard, "query": query}

    # fallback: ищем catalog URL в HTML
    url_match = re.search(r'https://catalog\\.wb\\.ru/[^"\\s]+', html)
    if url_match:
        found_url = url_match.group(0)
        parsed = urlparse(found_url)
        qs = parse_qs(parsed.query)
        shard = parsed.path.lstrip("/")
        query = qs.get("query", [""])[0]
        return {"shard": shard, "query": query}

    # если ничего не нашли — используем наиболее типичный вариант
    return {"shard": "catalog", "query": ""}


def fetch_products_from_seller_page(seller_url: str, limit: int = 20) -> List[Dict[str, Any]]:
    seller_id = parse_seller_id(seller_url)
    page = parse_page(seller_url)
    sort = parse_sort(seller_url)

    if not seller_id:
        raise RuntimeError("Не удалось определить sellerId из ссылки")

    catalog_meta = get_catalog_params_from_page(seller_url)
    shard = catalog_meta["shard"]
    query = catalog_meta["query"]

    api_url = f"https://catalog.wb.ru/{shard}/v6/catalog"

    params = {
        "ab_testing": "false",
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "lang": "ru",
        "page": page,
        "query": query,
        "sort": sort,
        "supplier": seller_id,
        "limit": limit,
    }

    payload = request_json(api_url, params=params)
    raw_products = extract_products(payload)

    if not raw_products:
        raise RuntimeError("WB не вернул товары по каталожному запросу")

    result = []
    for p in raw_products[:limit]:
        nm_id = get_nm_id(p)
        result.append({
            "nm_id": nm_id,
            "title": get_title(p),
            "category": get_category(p),
            "image": build_image_url(nm_id) if nm_id else None,
            "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" if nm_id else None,
        })
        time.sleep(random.uniform(0.2, 0.6))

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Команда /novinki покажет 20 новинок магазина WB."
    )


async def novinki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Собираю товары...")

    try:
        items = fetch_products_from_seller_page(SELLER_URL, limit=LIMIT)

        for i, item in enumerate(items, start=1):
            text = (
                f"{i}. {item['title']}\n"
                f"Категория: {item['category']}\n"
                f"Карточка: {item['url']}"
            )

            if item["image"]:
                try:
                    await update.message.reply_photo(photo=item["image"], caption=text)
                    time.sleep(random.uniform(0.8, 1.4))
                    continue
                except Exception as e:
                    logger.warning("Фото не отправилось: %s", e)

            await update.message.reply_text(text)
            time.sleep(random.uniform(0.5, 1.0))

    except Exception as e:
        logger.exception("Ошибка /novinki")
        await update.message.reply_text(
            f"Не удалось получить товары: {e}\n"
            f"Скорее всего WB временно режет выдачу или поменял структуру страницы."
        )


def main() -> None:
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Укажи TG_BOT_TOKEN или вставь токен в TOKEN")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("novinki", novinki))
    app.run_polling()


if __name__ == "__main__":
    main()