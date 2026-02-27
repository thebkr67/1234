import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
import os
from dotenv import load_dotenv
import aiohttp
import sys
from telegram.constants import ChatAction
from serpapi import GoogleSearch
import json as pyjson
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY or not SERPAPI_KEY:
    print("Ошибка: Не заданы TELEGRAM_TOKEN, DEEPSEEK_API_KEY или SERPAPI_KEY в .env")
    sys.exit(1)

DEFAULT_MODEL = "deepseek-chat"
REASONER_MODEL = "deepseek-reasoner"

# Описание функции поиска для DeepSeek
search_tool = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for up-to-date information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот с DeepSeek. Напиши мне что-нибудь.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("Обычная модель", callback_data=DEFAULT_MODEL),
            InlineKeyboardButton("Думающая модель", callback_data=REASONER_MODEL)
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите модель:", reply_markup=reply_markup)

async def websearch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    web_search = context.user_data.get('web_search', False)
    status = "ВКЛЮЧЕН" if web_search else "ВЫКЛЮЧЕН"
    keyboard = [
        [
            InlineKeyboardButton(
                "Выключить веб-поиск" if web_search else "Включить веб-поиск",
                callback_data="websearch_toggle"
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Веб-поиск сейчас: {status}", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data in [DEFAULT_MODEL, REASONER_MODEL]:
        context.user_data['model'] = data
        model_name = "Обычная" if data == DEFAULT_MODEL else "Думающая"
        await query.edit_message_text(text=f"Вы выбрали: {model_name} модель")
    elif data == "websearch_toggle":
        web_search = context.user_data.get('web_search', False)
        context.user_data['web_search'] = not web_search
        status = "ВКЛЮЧЕН" if not web_search else "ВЫКЛЮЧЕН"
        keyboard = [
            [
                InlineKeyboardButton(
                    "Выключить веб-поиск" if not web_search else "Включить веб-поиск",
                    callback_data="websearch_toggle"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Веб-поиск теперь: {status}", reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    # Показываем, что бот печатает
    await update.message.chat.send_action(action=ChatAction.TYPING)

    # Получаем выбранную модель (по умолчанию обычная)
    model = context.user_data.get('model', DEFAULT_MODEL)

    # Получаем историю диалога пользователя
    history = context.user_data.get('history', [])
    # Добавляем новое сообщение пользователя
    history.append({"role": "user", "content": user_message})

    # Проверяем, включён ли веб-поиск
    web_search = context.user_data.get('web_search', False)
    json_data = {
        "model": model,
        "messages": history
    }
    if web_search:
        json_data["tools"] = search_tool

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json=json_data
        ) as response:
            if response.status == 200:
                data = await response.json()
                print("Ответ DeepSeek:", pyjson.dumps(data, ensure_ascii=False, indent=2))
                # Проверяем, есть ли tool_calls
                if web_search and "choices" in data and data["choices"][0]["message"].get("tool_calls"):
                    assistant_message = data["choices"][0]["message"]
                    # Добавляем assistant с tool_calls в history
                    history.append(assistant_message)
                    tool_call = assistant_message["tool_calls"][0]
                    if tool_call["function"]["name"] == "search_web":
                        query_str = tool_call["function"]["arguments"]
                        args = pyjson.loads(query_str)
                        search_query = args.get("query", "")
                        # Выполняем поиск через SerpApi
                        search = GoogleSearch({
                            "q": search_query,
                            "api_key": SERPAPI_KEY,
                            "num": 3
                        })
                        results = search.get_dict()
                        # Формируем краткий ответ из результатов поиска
                        answer_text = ""
                        if "organic_results" in results:
                            for res in results["organic_results"][:3]:
                                answer_text += f"{res.get('title')}: {res.get('snippet', '')}\n{res.get('link')}\n\n"
                        else:
                            answer_text = "Нет результатов поиска."
                        # Для повторного запроса: только assistant с tool_calls и tool
                        tool_call_id = tool_call["id"]
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": answer_text
                        }
                        history_for_tool = [assistant_message, tool_message]
                        print("История для повторного запроса:", pyjson.dumps(history_for_tool, ensure_ascii=False, indent=2))
                        json_data2 = {
                            "model": model,
                            "messages": history_for_tool
                        }
                        async with session.post(
                            "https://api.deepseek.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                            json=json_data2
                        ) as response2:
                            if response2.status == 200:
                                data2 = await response2.json()
                                answer = data2["choices"][0]["message"]["content"]
                                # Только теперь добавляем финальный ответ ассистента в основную историю
                                history.append({"role": "assistant", "content": answer})
                                # Очищаем историю: оставляем только user и последний assistant
                                history = [msg for msg in history if msg.get("role") == "user"]
                                history.append({"role": "assistant", "content": answer})
                                context.user_data['history'] = history
                            else:
                                answer = f"Ошибка при повторном обращении к DeepSeek API: {await response2.text()}"
                    else:
                        answer = "Неизвестный вызов функции."
                else:
                    answer = data["choices"][0]["message"]["content"]
                    # Добавляем ответ ассистента в историю
                    history.append({"role": "assistant", "content": answer})
                    context.user_data['history'] = history
            else:
                answer = f"Ошибка при обращении к DeepSeek API: {await response.text()}"

    await update.message.reply_text(answer)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("websearch", websearch_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()