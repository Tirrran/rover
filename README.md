# Райн-ровер Telegram Mini App

Telegram Mini App без Node.js: Python backend отдаёт статический HTML/CSS/JS фронт и запускает Telegram-бота через long polling.

## Стек

- Python 3.10+
- только стандартная библиотека Python
- HTML + CSS + JavaScript

## Структура

```text
app.py              # HTTP server + Telegram Bot API long polling
web/index.html     # Mini App HTML
web/styles.css     # Figma-like layout and animation
web/app.js         # screen flow: welcome -> start -> loading -> result
public/assets/     # локальные ассеты из Figma
.env.example       # пример переменных окружения
```

## Локальный запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
python app.py
```

Mini App откроется на `http://localhost:3000`.

Проверка сервера:

```bash
curl http://localhost:3000/health
```

Если `.env` не заполнен, web-сервер всё равно стартует, но бот не запускается.

## Переменные окружения

Создай `.env`:

```bash
cp .env.example .env
nano .env
```

```env
BOT_TOKEN=123456789:your-telegram-bot-token
WEBAPP_URL=https://your-domain.example
PORT=3000
```

`WEBAPP_URL` должен быть публичным HTTPS-адресом. Telegram Mini Apps в продакшене требуют HTTPS.

## Деплой на ВМ без Docker

1. Установи Python 3.10+ и Git.

2. Склонируй репозиторий:

```bash
git clone https://github.com/ObamaObama444/-_-.git
cd -_-
```

3. Создай виртуальное окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

4. Заполни `.env`:

```bash
cp .env.example .env
nano .env
```

5. Запусти приложение:

```bash
python app.py
```

## Запуск через systemd

Пример `/etc/systemd/system/ryan-rover.service`:

```ini
[Unit]
Description=Ryan Rover Telegram Mini App
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/-_-
ExecStart=/path/to/-_-/.venv/bin/python /path/to/-_-/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Команды:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ryan-rover
sudo systemctl start ryan-rover
sudo systemctl status ryan-rover
```

После обновления кода:

```bash
git pull
source .venv/bin/activate
sudo systemctl restart ryan-rover
```

## Telegram BotFather

1. Создай бота через `@BotFather`.
2. Запиши токен в `BOT_TOKEN`.
3. Укажи публичный HTTPS-домен в `WEBAPP_URL`.
4. Запусти `python app.py`.
5. Напиши боту `/start`: он пришлёт кнопку Mini App и установит кнопку меню.
