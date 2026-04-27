# Райн-ровер Telegram Mini App

Миниапп для Telegram с ботом: приветственный экран, кнопка запуска, 15-секундная заглушка аналитики и экран статистики по макету Figma.

## Стек

- React + Vite + TypeScript
- Express
- Telegraf
- Node.js 20+

## Локальный запуск веб-части

```bash
npm ci
npm run dev
```

Vite откроет Mini App на `http://localhost:5173`.

## Переменные окружения

Создай `.env` по примеру:

```bash
cp .env.example .env
```

```env
BOT_TOKEN=123456789:your-telegram-bot-token
WEBAPP_URL=https://your-domain.example
PORT=3000
```

`WEBAPP_URL` должен быть публичным HTTPS-адресом. Telegram Mini Apps не открываются по обычному HTTP-домену в продакшене.

## Деплой на ВМ без Docker

1. Установи Node.js 20+ и Git.

2. Склонируй репозиторий:

```bash
git clone https://github.com/ObamaObama444/-_-.git
cd -_-
```

3. Установи зависимости:

```bash
npm ci
```

4. Создай `.env`:

```bash
cp .env.example .env
nano .env
```

5. Собери проект:

```bash
npm run build
```

6. Запусти сервер и бота:

```bash
npm start
```

Проверка сервера:

```bash
curl http://localhost:3000/health
```

## Запуск через PM2

```bash
npm install -g pm2
pm2 start npm --name ryan-rover -- start
pm2 save
pm2 startup
```

После изменения кода:

```bash
git pull
npm ci
npm run build
pm2 restart ryan-rover
```

## Запуск через systemd

Создай `/etc/systemd/system/ryan-rover.service`:

```ini
[Unit]
Description=Ryan Rover Telegram Mini App
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/-_-
ExecStart=/usr/bin/npm start
Restart=always
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

Затем:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ryan-rover
sudo systemctl start ryan-rover
sudo systemctl status ryan-rover
```

## Telegram BotFather

1. Создай бота через `@BotFather`.
2. Сохрани токен в `BOT_TOKEN`.
3. Укажи публичный HTTPS-адрес в `WEBAPP_URL`.
4. Запусти приложение.
5. Напиши боту `/start`: он отправит кнопку Mini App и установит кнопку меню.
