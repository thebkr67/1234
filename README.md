# Deepseek Telegram Bot

Телеграм-бот с поддержкой DeepSeek API, Function Calling и веб-поиска через SerpApi.

## Структура проекта

- `bot.py` — основной код бота
- `requirements.txt` — зависимости Python
- `.env.example` — пример файла переменных окружения
- `README.md` — эта инструкция

## Быстрый старт локально

1. Клонируйте репозиторий:
   ```sh
   git clone https://github.com/reinekes/Deepseek_TG_bot_2025.git
   cd Deepseek_TG_bot_2025
   ```
2. Создайте файл `.env` на основе `.env.example` и пропишите свои ключи:
   ```
   cp .env.example .env
   # Откройте .env и вставьте свои токены
   ```
3. Установите Python 3.11+ и зависимости:
   ```sh
   python3.11 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. Запустите бота:
   ```sh
   python bot.py
   ```

## Развёртывание на сервере (VPS)

1. Установите Python 3.11+, git, pip:
   ```sh
   sudo apt update && sudo apt install python3.11 python3.11-venv git
   ```
2. Клонируйте репозиторий и настройте переменные окружения (см. выше).
3. Установите зависимости и запустите бота (см. выше).
4. **Для автозапуска используйте systemd:**
   - Создайте файл `/etc/systemd/system/deepseek_bot.service`:
     ```ini
     [Unit]
     Description=Deepseek Telegram Bot
     After=network.target

     [Service]
     User=ubuntu  # или ваш пользователь
     WorkingDirectory=/path/to/Deepseek_TG_bot_2025
     Environment="PATH=/path/to/Deepseek_TG_bot_2025/venv/bin"
     ExecStart=/path/to/Deepseek_TG_bot_2025/venv/bin/python bot.py
     Restart=always

     [Install]
     WantedBy=multi-user.target
     ```
   - Перезапустите systemd:
     ```sh
     sudo systemctl daemon-reload
     sudo systemctl enable deepseek_bot
     sudo systemctl start deepseek_bot
     sudo systemctl status deepseek_bot
     ```

## Развёртывание через Docker

1. Создайте файл `Dockerfile`:
   ```Dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY . .
   RUN pip install --no-cache-dir -r requirements.txt
   CMD ["python", "bot.py"]
   ```
2. Соберите и запустите контейнер:
   ```sh
   docker build -t deepseek-bot .
   docker run --env-file .env deepseek-bot
   ```

## Развёртывание на Render, Railway, Fly.io

1. Подключите репозиторий через веб-интерфейс сервиса.
2. Укажите команду запуска: `python bot.py`
3. Добавьте переменные окружения через веб-интерфейс (TELEGRAM_TOKEN, DEEPSEEK_API_KEY, SERPAPI_KEY).

## Важно
- Никогда не публикуйте свои токены в открытом доступе!
- Для стабильной работы используйте Python 3.11+.
- Для production-режима рекомендуется автозапуск через systemd или Docker.

---

Если возникнут вопросы по деплою — пиши в Issues или в Telegram! 