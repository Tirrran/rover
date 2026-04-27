import json
import logging
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
ASSETS_DIR = BASE_DIR / "public" / "assets"


def load_env(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        logging.warning("%s must be an integer, using %s", name, default)
        return default


def telegram_request(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=35) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not data.get("ok"):
        logging.warning("Telegram %s failed: %s", method, data)

    return data


def miniapp_keyboard(webapp_url: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть Райн-ровер",
                    "web_app": {"url": webapp_url},
                }
            ]
        ]
    }


def configure_menu_button(token: str, webapp_url: str) -> None:
    telegram_request(
        token,
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": "Райн-ровер",
                "web_app": {"url": webapp_url},
            }
        },
    )


def send_start_message(token: str, chat_id: int, webapp_url: str) -> None:
    telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "Райн-ровер готов к аналитике.",
            "reply_markup": miniapp_keyboard(webapp_url),
        },
    )


def bot_polling(token: str, webapp_url: str) -> None:
    logging.info("Telegram bot is running in long polling mode.")
    offset = 0

    try:
        configure_menu_button(token, webapp_url)
    except Exception:
        logging.exception("Failed to configure Telegram menu button")

    while True:
        try:
            data = telegram_request(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                },
            )

            for update in data.get("result", []):
                offset = max(offset, update["update_id"] + 1)
                message = update.get("message") or {}
                text = message.get("text", "")
                chat = message.get("chat") or {}
                chat_id = chat.get("id")

                if chat_id and text in {"/start", "/app"}:
                    send_start_message(token, chat_id, webapp_url)
        except urllib.error.HTTPError:
            logging.exception("Telegram HTTP error")
            time.sleep(5)
        except urllib.error.URLError:
            logging.exception("Telegram network error")
            time.sleep(5)
        except Exception:
            logging.exception("Telegram polling error")
            time.sleep(5)


class MiniAppHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_health()
            return

        self.serve_file_for_path()

    def do_HEAD(self) -> None:
        if self.path == "/health":
            self.send_health(head_only=True)
            return

        self.serve_file_for_path(head_only=True)

    def send_health(self, head_only: bool = False) -> None:
        body = json.dumps(
            {
                "ok": True,
                "service": "ryan-rover-miniapp",
                "bot": bool(os.getenv("BOT_TOKEN") and os.getenv("WEBAPP_URL")),
            },
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        if not head_only:
            self.wfile.write(body)

    def serve_file_for_path(self, head_only: bool = False) -> None:
        parsed = urllib.parse.urlparse(self.path)
        requested_path = urllib.parse.unquote(parsed.path)

        if requested_path == "/":
            file_path = WEB_DIR / "index.html"
        elif requested_path.startswith("/assets/"):
            file_path = ASSETS_DIR / requested_path.removeprefix("/assets/")
        else:
            file_path = WEB_DIR / requested_path.removeprefix("/")

        try:
            resolved = file_path.resolve(strict=True)
            allowed_roots = (WEB_DIR.resolve(), ASSETS_DIR.resolve())
            if not any(resolved.is_relative_to(root) for root in allowed_roots) or not resolved.is_file():
                raise FileNotFoundError
        except FileNotFoundError:
            if requested_path.count(".") == 0:
                resolved = WEB_DIR / "index.html"
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        size = resolved.stat().st_size

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.end_headers()

        if not head_only:
            with resolved.open("rb") as file:
                self.copyfile(file, self.wfile)


def start_bot_if_configured() -> None:
    token = os.getenv("BOT_TOKEN")
    webapp_url = os.getenv("WEBAPP_URL")

    if not token or not webapp_url:
        logging.warning("BOT_TOKEN or WEBAPP_URL is missing. HTTP server will run without Telegram bot.")
        return

    thread = threading.Thread(target=bot_polling, args=(token, webapp_url), daemon=True)
    thread.start()


def main() -> None:
    load_env()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s:%(message)s")

    port = env_int("PORT", 3000)
    start_bot_if_configured()

    server = ThreadingHTTPServer(("0.0.0.0", port), MiniAppHandler)
    logging.info("HTTP server is listening on http://0.0.0.0:%s", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
