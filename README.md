# Райн-ровер Telegram Mini App

Python backend + чистый HTML/CSS/JS frontend для Telegram Mini App.

Бот: `https://t.me/RaianRoverYandex_bot`  
Mini App URL: `https://www.adolanna.ru`

## Что внутри

```text
app.py              # HTTP server + Telegram Bot API long polling
.env                # готовые переменные для бота и домена
robot_push_frame.py # агент для Raspberry Pi: пушит кадры на ВМ по HTTP
robot_capture_agent.py # агент для Raspberry Pi: снимает кадры только по заданию ВМ
web/index.html      # HTML
web/styles.css      # CSS
web/app.js          # JS flow + POST /api/start по кнопке "Начать"
public/assets/      # ассеты из Figma
```

Проект не требует Node.js, npm, pip и Docker. Нужен только Python 3.10+.

## Переменные окружения

Минимально необходимые:

```text
BOT_TOKEN=...
WEBAPP_URL=https://your-domain
ROBOT_PUSH_TOKEN=long-random-secret
```

Дополнительно:

```text
ROBOT_CAMERA_URL=http://192.168.1.33:8889/cam/  # fallback для старого pull-режима
ROBOT_FRAME_MAX_AGE_SEC=300
ROBOT_CAMERA_TIMEOUT_SEC=8
ROBOT_MAX_FRAME_BYTES=3500000
ROBOT_SCREENSHOT_CAPTION=Кадр с робота получен.
INIT_DATA_MAX_AGE_SEC=86400
DEFAULT_CHAT_ID=123456789  # опционально, fallback для отладки
```

Кнопка `Начать` в Mini App вызывает `POST /api/start`: backend создаёт задание на захват, робот-агент забирает его через `POST /api/robot/capture/next`, отправляет пачку кадров на `POST /api/robot/capture/result`, backend сначала прогоняет gate-модель из `/opt/apps/buba`, затем при известном классе запускает большую burst-модель и отправляет в Telegram текст с классом и accuracy. Если gate видит `unknown`, большая модель не запускается, а бот пишет, что класс неизвестный.

Для автономной миссии лабиринта робот может отправлять пачки кадров по точкам напрямую:

```text
POST /api/robot/mission/classify-point
X-Robot-Token: <ROBOT_PUSH_TOKEN>
```

Backend сохраняет кадры в `/tmp/ryan-rover-missions/<mission_id>/<point_id>`, прогоняет тот же gate + burst пайплайн и возвращает JSON с классом, confidence, status и путями к отчётам.

## Push-режим для робота за NAT

Если робот находится в локальной сети, ВМ обычно не видит адреса вида `192.168.1.x`. В этом режиме Raspberry Pi сам делает исходящий HTTP-запрос на ВМ и отправляет кадр:

```bash
export ROBOT_CAMERA_URL=http://192.168.1.73:8889/cam/
export MINIAPP_FRAME_ENDPOINT=https://www.adolanna.ru/api/robot/frame
export ROBOT_PUSH_TOKEN=long-random-secret
python3 robot_push_frame.py
```

Тот же `ROBOT_PUSH_TOKEN` должен быть указан в `.env` на ВМ. Проверить, что ВМ получила кадр:

```bash
curl https://www.adolanna.ru/health
```

В ответе `robot_frame` должен стать `true`, а `robot_frame_age_sec` должен быть небольшим числом.

## On-demand анализ через модель

На ВМ должны быть заданы:

```bash
ROBOT_PUSH_TOKEN=long-random-secret
ROBOT_CAPTURE_FRAME_COUNT=4
ROBOT_CAPTURE_WAIT_TIMEOUT_SEC=30
BUBA_DIR=/opt/apps/buba
BUBA_GATE_TIMEOUT_SEC=45
BUBA_GATE_KNOWN_THRESHOLD=0.70
BUBA_TIMEOUT_SEC=90
ROBOT_MISSION_MAX_BODY_BYTES=50000000
DEFAULT_CHAT_ID=123456789
```

На роботе запусти агента:

```bash
export MINIAPP_BASE_URL=https://www.adolanna.ru
export ROBOT_PUSH_TOKEN=long-random-secret
export ROBOT_RTSP_URL=rtsp://172.18.0.2:8554/cam
python3 robot_capture_agent.py
```

Агент не шлёт кадры постоянно: он ждёт задание от ВМ и снимает пачку кадров только после нажатия `Начать`.

## Быстрый деплой на ВМ

```bash
cd /opt
sudo git clone https://github.com/ObamaObama444/-_-.git ryan-rover
sudo chown -R $USER:$USER /opt/ryan-rover
cd /opt/ryan-rover
python3 app.py
```

Проверка на самой ВМ:

```bash
curl http://127.0.0.1:3000/health
```

Ожидаемо:

```json
{"ok": true, "service": "ryan-rover-miniapp", "bot": true}
```

## Nginx для adolanna.ru

Если Nginx уже стоит, создай конфиг:

```bash
sudo nano /etc/nginx/sites-available/ryan-rover
```

Вставь:

```nginx
server {
    server_name adolanna.ru www.adolanna.ru;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Включи сайт:

```bash
sudo ln -sf /etc/nginx/sites-available/ryan-rover /etc/nginx/sites-enabled/ryan-rover
sudo nginx -t
sudo systemctl reload nginx
```

Включи HTTPS:

```bash
sudo certbot --nginx -d adolanna.ru -d www.adolanna.ru
```

После этого проверь:

```bash
curl https://www.adolanna.ru/health
```

## Запуск через systemd

Создай сервис:

```bash
sudo nano /etc/systemd/system/ryan-rover.service
```

Вставь:

```ini
[Unit]
Description=Ryan Rover Telegram Mini App
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/ryan-rover
ExecStart=/usr/bin/python3 /opt/ryan-rover/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Запусти:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ryan-rover
sudo systemctl restart ryan-rover
sudo systemctl status ryan-rover
```

Логи:

```bash
journalctl -u ryan-rover -f
```

## Обновление

```bash
cd /opt/ryan-rover
git pull
sudo systemctl restart ryan-rover
```

## Как проверить в Telegram

1. Открой `https://t.me/RaianRoverYandex_bot`.
2. Напиши `/start`.
3. Нажми кнопку `Открыть Райн-ровер`.
4. В Mini App нажми `Начать`.
5. Проверь, что бот прислал текстовый результат анализа в чат.
6. Если меню Telegram уже обновилось, можно открыть Mini App через кнопку меню бота.
