
import asyncio
import aiohttp
import pandas as pd
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
import logging
import io
import re

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = "ВАШ_TELEGRAM_BOT_TOKEN"
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

async def parse_wildberries(session, url):
    """Парсинг товаров с одной страницы продавца"""
    products = []
    
    try:
        async with session.get(url, headers=HEADERS) as response:
            if response.status == 200:
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Поиск карточек товаров (актуальные селекторы для Wildberries)
                product_cards = soup.find_all('div', {'class': re.compile(r'product-card')})
                
                if not product_cards:
                    # Альтернативный селектор
                    product_cards = soup.find_all('article', {'class': re.compile(r'product-card')})
                
                for card in product_cards:
                    try:
                        # Извлечение названия товара
                        name_elem = card.find('span', {'class': re.compile(r'product-name')})
                        if not name_elem:
                            name_elem = card.find('a', {'class': re.compile(r'product-name')})
                        if not name_elem:
                            name_elem = card.find('h3')
                        
                        product_name = name_elem.get_text(strip=True) if name_elem else "Название не найдено"
                        
                        # Извлечение ссылки на изображение
                        img_elem = card.find('img', {'class': re.compile(r'thumbnail')})
                        if not img_elem:
                            img_elem = card.find('img', {'class': re.compile(r'product-image')})
                        if not img_elem:
                            img_elem = card.find('img')
                        
                        if img_elem and 'src' in img_elem.attrs:
                            img_url = img_elem['src']
                            # Преобразование относительных ссылок в абсолютные
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

                logger.error(f"Ошибка HTTP {response.status} для {url}")
                
    except Exception as e:
        logger.error(f"Ошибка при запросе к {url}: {e}")
    
    return products

async def parse_all_sellers():
    """Парсинг всех продавцов"""
    all_products = []
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in SELLER_URLS:
            tasks.append(parse_wildberries(session, url))
        
        results = await asyncio.gather(*tasks)
        
        for products in results:
            all_products.extend(products)
    
    return all_products

def create_excel(products):
    """Создание Excel файла с товарами"""
    if not products:
        return None
    
    df = pd.DataFrame(products)
    
    # Создаем Excel файл в памяти
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Товары')
        
        # Настройка ширины колонок
        worksheet = writer.sheets['Товары']
        worksheet.column_dimensions['A'].width = 50  # Наименование
        worksheet.column_dimensions['B'].width = 60  # Картинка
        worksheet.column_dimensions['C'].width = 40  # Источник
    
    output.seek(0)
    return output

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_text = """
    👋 Привет! Я бот для парсинга товаров с Wildberries.
    
    Доступные команды:
    /start - Показать это сообщение
    /parse - Начать парсинг товаров
    /help - Помощь
    
    Я соберу данные с двух продавцов и отправлю вам Excel-файл.
    """
    await update.message.reply_text(welcome_text)

async def parse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /parse"""
    await update.message.reply_text("🔄 Начинаю парсинг товаров с Wildberries...")
    
    try:
        # Парсим данные
        products = await parse_all_sellers()
        
        if not products:
            await update.message.reply_text("❌ Не удалось найти товары. Возможно, изменилась структура сайта.")
            return
        
        # Создаем Excel файл
        await update.message.reply_text(f"✅ Найдено {len(products)} товаров. Формирую Excel-файл...")
        excel_file = create_excel(products)
        
        if excel_file:
            # Отправляем файл
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
    help_text = """
    📚 Помощь по использованию бота:
    
    1. Команда /parse - запускает парсинг товаров с Wildberries
    2. Бот собирает данные с двух продавцов:
       - https://www.wildberries.ru/seller/92351
       - https://www.wildberries.ru/seller/870386
    3. Результат отправляется в Excel-файле с колонками:
       - Наименование товара
       - Ссылка на картинку
       - Источник (URL продавца)
    
    ⚠️ Примечание: Если парсинг не работает, возможно Wildberries изменил структуру сайта.
    """
    await update.message.reply_text(help_text)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")

def main():
    """Основная функция запуска бота"""
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("parse", parse_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Регистрируем обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_UPDATES)

if __name__ == '__main__':
    main()


