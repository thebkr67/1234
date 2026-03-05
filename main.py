import asyncio
import aiohttp
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
import logging
import io
import re
import os
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
# Рекомендуется хранить токен в переменной окружения BOT_TOKEN
TOKEN = os.getenv("BOT_TOKEN", "ВАШ_TELEGRAM_BOT_TOKEN")

SELLER_URLS = [
    "https://www.wildberries.ru/seller/92351",
    "https://www.wildberries.ru/seller/870386?sort=newly&page=1"
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0'
}


async def parse_wildberries(session: aiohttp.ClientSession, url: str):
    """Парсинг товаров с одной страницы продавца (HTML).
    Важно: WB часто подгружает товары через API. Если товаров не находит — лучше перейти на API-парсинг.
    """
    products = []

    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=25)) as response:
            if response.status == 200:
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

                # Поиск карточек товаров (селекторы могут меняться)
                product_cards = soup.find_all('div', {'class': re.compile(r'product-card')})
                if not product_cards:
                    product_cards = soup.find_all('article', {'class': re.compile(r'product-card')})

                for card in product_cards:
                    try:
                        # Название
                        name_elem = (
                            card.find('span', {'class': re.compile(r'product-name')}) or
                            card.find('a', {'class': re.compile(r'product-name')}) or
                            card.find('h3')
                        )
                        product_name = name_elem.get_text(strip=True) if name_elem else "Название не найдено"

                        # Картинка
                        img_elem = (
                            card.find('img', {'class': re.compile(r'thumbnail')}) or
                            card.find('img', {'class': re.compile(r'product-image')}) or
                            card.find('img')
                        )

                        img_url = ""
                        if img_elem:
                            # WB может отдавать src, data-src, srcset
                            if img_elem.get('src'):
                                img_url = img_elem['src']
                            elif img_elem.get('data-src'):
                                img_url = img_elem['data-src']
                            elif img_elem.get('srcset'):
                                # берем первый url из srcset
                                img_url = img_elem['srcset'].split(',')[0].strip().split(' ')[0]

                        if img_url:
                            if img_url.startswith('//'):
                                img_url = 'https:' + img_url
                            elif img_url.startswith('/'):
                                img_url = 'https://www.wildberries.ru' + img_url
                        else:
                            img_url = "Изображение не найдено"

                        products.append({
                            'Наименование': product_name,
                            'Картинка': img_url,
                            'Источник': url
                        })

                    except Exception as e:
                        logger.error(f"Ошибка при парсинге карточки: {e}")
                        continue

                logger.info(f"Найдено {len(products)} товаров с {url}")
            else:
                logger.error(f"Ошибка HTTP {response.status} для {url}")

    except Exception as e:
        logger.error(f"Ошибка при запросе к {url}: {e}")

    return products


async def parse_all_sellers():
    """Парсинг всех продавцов"""
    all_products = []
    async with aiohttp.ClientSession() as session:
        tasks = [parse_wildberries(session, url) for url in SELLER_URLS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Ошибка задачи парсинга: {r}")
                continue
            all_products.extend(r)

    return all_products


def create_excel(products):
    """Создание Excel файла с товарами (без pandas — чтобы не падало из-за отсутствия зависимости)."""
    if not products:
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = "Товары"

    headers = ["Наименование", "Картинка", "Источник"]
    ws.append(headers)

    for p in products:
        ws.append([p.get("Наименование", ""), p.get("Картинка", ""), p.get("Источник", "")])

    # Ширина колонок
    widths = [50, 60, 40]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    # Сохраняем в память
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_text = (
        "👋 Привет! Я бот для парсинга товаров с Wildberries.\n\n"
        "Доступные команды:\n"
        "/start — Показать это сообщение\n"
        "/parse — Начать парсинг товаров\n"
        "/help — Помощь\n\n"
        "Я соберу данные с продавцов и отправлю Excel-файл."
    )
    await update.message.reply_text(welcome_text)


async def parse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /parse"""
    await update.message.reply_text("🔄 Начинаю парсинг товаров с Wildberries...")

    try:
        products = await parse_all_sellers()

        if not products:
            await update.message.reply_text(
                "❌ Не удалось найти товары.\n"
                "Возможные причины:\n"
                "• Wildberries изменил структуру HTML\n"
                "• товары подгружаются через API\n"
                "• временная блокировка/капча\n\n"
                "Если нужно — перепишу парсер на API (самый надежный вариант)."
            )
            return

        await update.message.reply_text(f"✅ Найдено {len(products)} товаров. Формирую Excel-файл...")
        excel_file = create_excel(products)

        if excel_file:
            await update.message.reply_document(
                document=InputFile(excel_file, filename="wildberries_products.xlsx"),
                caption=f"📊 Файл с {len(products)} товарами"
            )
        else:
            await update.message.reply_text("❌ Ошибка при создании файла.")

    except Exception as e:
        logger.error(f"Ошибка при парсинге: {e}")
        await update.message.reply_text(f"❌ Произошла ошибка: {str(e)}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📚 Помощь по использованию бота:\n\n"
        "1) /parse — запускает парсинг товаров с Wildberries\n"
        "2) Бот собирает данные с продавцов из списка SELLER_URLS\n"
        "3) Результат отправляется в Excel с колонками:\n"
        "   • Наименование\n"
        "   • Ссылка на картинку\n"
        "   • Источник (URL продавца)\n\n"
        "⚠️ Если парсинг не работает, WB мог изменить структуру сайта — тогда лучше парсить через API."
    )
    await update.message.reply_text(help_text)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")


def main():
    """Основная функция запуска бота"""
    if not TOKEN or TOKEN == "ВАШ_TELEGRAM_BOT_TOKEN":
        logger.warning("⚠️ Не задан BOT_TOKEN. Установите переменную окружения BOT_TOKEN или пропишите TOKEN в коде.")

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("parse", parse_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_UPDATES)


if __name__ == '__main__':
    main()
