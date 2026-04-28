import json
import hashlib
import hmac
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
ASSETS_DIR = BASE_DIR / "public" / "assets"
DEFAULT_CAMERA_TIMEOUT_SEC = 8
DEFAULT_MAX_FRAME_BYTES = 3_500_000
DEFAULT_INIT_DATA_MAX_AGE_SEC = 86_400
DEFAULT_PHOTO_CAPTION = "Кадр с робота получен."

CHAT_BINDINGS_BY_USER_ID: dict[int, int] = {}
LAST_KNOWN_CHAT_ID: int | None = None
CHAT_BINDINGS_LOCK = threading.Lock()


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


def telegram_request_multipart(
    token: str,
    method: str,
    fields: dict[str, Any],
    files: dict[str, tuple[str, bytes, str]],
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    boundary = f"ryanrover-{uuid.uuid4().hex}"
    body = bytearray()

    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for key, (filename, content, content_type) in files.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
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


def remember_chat_binding(message: dict[str, Any]) -> None:
    global LAST_KNOWN_CHAT_ID

    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = from_user.get("id")

    if not isinstance(chat_id, int):
        return

    with CHAT_BINDINGS_LOCK:
        LAST_KNOWN_CHAT_ID = chat_id
        if isinstance(user_id, int):
            CHAT_BINDINGS_BY_USER_ID[user_id] = chat_id


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

                remember_chat_binding(message)

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

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/start":
            self.handle_start_request()
            return

        self.send_error(HTTPStatus.NOT_FOUND)

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

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        content_length = env_int("MAX_POST_BODY_BYTES", 256_000)
        try:
            expected = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            expected = 0

        if expected <= 0 or expected > content_length:
            return {}

        raw = self.rfile.read(expected)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

        return parsed if isinstance(parsed, dict) else {}

    def handle_start_request(self) -> None:
        token = os.getenv("BOT_TOKEN")
        camera_url = os.getenv("ROBOT_CAMERA_URL")
        if not token:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "BOT_TOKEN is missing on backend"},
            )
            return

        if not camera_url:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "ROBOT_CAMERA_URL is missing on backend"},
            )
            return

        payload = self.read_json_body()
        init_data = payload.get("initData")
        user_id = extract_user_id_from_init_data(init_data, token) if isinstance(init_data, str) else None
        chat_id = resolve_chat_id(user_id)

        if not chat_id:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": "Не найден chat_id. Открой чат с ботом и отправь /start, затем повтори.",
                },
            )
            return

        timeout_sec = max(2, env_int("ROBOT_CAMERA_TIMEOUT_SEC", DEFAULT_CAMERA_TIMEOUT_SEC))
        max_bytes = max(300_000, env_int("ROBOT_MAX_FRAME_BYTES", DEFAULT_MAX_FRAME_BYTES))
        caption = os.getenv("ROBOT_SCREENSHOT_CAPTION", DEFAULT_PHOTO_CAPTION)

        try:
            frame_bytes = fetch_robot_frame(camera_url, timeout_sec=timeout_sec, max_bytes=max_bytes)
            response = telegram_request_multipart(
                token=token,
                method="sendPhoto",
                fields={"chat_id": chat_id, "caption": caption},
                files={"photo": ("robot.jpg", frame_bytes, "image/jpeg")},
            )
        except Exception as error:
            logging.exception("Failed to grab robot frame and send photo")
            self.send_json(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "error": f"Не удалось отправить кадр: {error}"},
            )
            return

        if not response.get("ok"):
            self.send_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": "Telegram sendPhoto failed"})
            return

        self.send_json(HTTPStatus.OK, {"ok": True, "chat_id": chat_id})

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


def resolve_chat_id(user_id: int | None) -> int | None:
    with CHAT_BINDINGS_LOCK:
        if user_id is not None and user_id in CHAT_BINDINGS_BY_USER_ID:
            return CHAT_BINDINGS_BY_USER_ID[user_id]

        if user_id is not None:
            return user_id

        fallback = os.getenv("DEFAULT_CHAT_ID")
        if fallback:
            try:
                return int(fallback)
            except ValueError:
                logging.warning("DEFAULT_CHAT_ID is invalid, expected integer.")

        return LAST_KNOWN_CHAT_ID


def extract_user_id_from_init_data(init_data: str, token: str) -> int | None:
    parsed = parse_and_validate_init_data(init_data, token)
    if not parsed:
        return None

    user = parsed.get("user")
    if not isinstance(user, dict):
        return None

    user_id = user.get("id")
    return user_id if isinstance(user_id, int) else None


def parse_and_validate_init_data(init_data: str, bot_token: str) -> dict[str, Any] | None:
    if not init_data:
        return None

    items = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    incoming_hash = items.pop("hash", "")
    if not incoming_hash:
        return None

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(items.items()))
    calculated_hash = hmac.new(secret_key, data_check.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, incoming_hash):
        logging.warning("Telegram Mini App initData hash mismatch.")
        return None

    max_age_sec = max(60, env_int("INIT_DATA_MAX_AGE_SEC", DEFAULT_INIT_DATA_MAX_AGE_SEC))
    auth_date = items.get("auth_date")
    if auth_date and auth_date.isdigit():
        if int(time.time()) - int(auth_date) > max_age_sec:
            logging.warning("Telegram Mini App initData is too old.")
            return None

    parsed: dict[str, Any] = {}
    for key, value in items.items():
        if key in {"user", "chat", "receiver"}:
            try:
                parsed[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                parsed[key] = None
                continue

        parsed[key] = value

    return parsed


def fetch_robot_frame(camera_url: str, timeout_sec: int, max_bytes: int, depth: int = 0) -> bytes:
    if depth > 1:
        raise ValueError("Camera URL resolved recursively too many times")

    request = urllib.request.Request(
        camera_url,
        headers={
            "User-Agent": "ryan-rover-miniapp/1.0",
            "Accept": "image/jpeg,image/*,multipart/x-mixed-replace,text/html",
        },
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if content_type.startswith("image/"):
            image = response.read(max_bytes + 1)
            if len(image) > max_bytes:
                raise ValueError("Camera image is too large")
            return image

        if "multipart/x-mixed-replace" in content_type or "mjpeg" in content_type:
            return read_first_jpeg_frame(response, max_bytes=max_bytes)

        if "text/html" in content_type:
            html = response.read(64_000).decode("utf-8", errors="ignore")
            stream_url = extract_stream_url_from_html(html, base_url=camera_url)
            if not stream_url:
                raise ValueError("Could not find stream URL in camera HTML page")
            return fetch_robot_frame(stream_url, timeout_sec=timeout_sec, max_bytes=max_bytes, depth=depth + 1)

        fallback = response.read(max_bytes + 1)
        if len(fallback) > max_bytes:
            raise ValueError(f"Unsupported camera response content-type: {content_type or 'unknown'}")
        return fallback


def extract_stream_url_from_html(html: str, base_url: str) -> str | None:
    image_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if image_match:
        return urllib.parse.urljoin(base_url, image_match.group(1))

    generic_match = re.search(
        r'["\']([^"\']*(?:mjpg|mjpeg|stream|video)[^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if generic_match:
        return urllib.parse.urljoin(base_url, generic_match.group(1))

    return None


def read_first_jpeg_frame(response: Any, max_bytes: int) -> bytes:
    buffer = bytearray()
    chunk_size = 4096

    while len(buffer) <= max_bytes:
        chunk = response.read(chunk_size)
        if not chunk:
            break

        buffer.extend(chunk)

        start = buffer.find(b"\xff\xd8")
        if start == -1:
            if len(buffer) > 128_000:
                del buffer[:-4]
            continue

        end = buffer.find(b"\xff\xd9", start + 2)
        if end != -1:
            return bytes(buffer[start : end + 2])

        if start > 0:
            del buffer[:start]

    raise ValueError("Could not parse JPEG frame from MJPEG stream")


def start_bot_if_configured() -> None:
    if os.getenv("DISABLE_BOT") == "1":
        logging.warning("DISABLE_BOT=1 is set. HTTP server will run without Telegram bot.")
        return

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
