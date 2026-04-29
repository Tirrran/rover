import json
import hashlib
import hmac
import logging
import mimetypes
import os
import re
import base64
import shutil
import subprocess
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
DEFAULT_ROBOT_FRAME_MAX_AGE_SEC = 300
DEFAULT_ROBOT_CAPTURE_FRAME_COUNT = 4
DEFAULT_ROBOT_CAPTURE_WAIT_TIMEOUT_SEC = 30
DEFAULT_ROBOT_CAPTURE_POLL_TIMEOUT_SEC = 25
DEFAULT_ROBOT_CAPTURE_MAX_BODY_BYTES = 8_000_000
DEFAULT_ROBOT_MISSION_MAX_BODY_BYTES = 50_000_000
DEFAULT_BUBA_GATE_TIMEOUT_SEC = 45
DEFAULT_BUBA_GATE_KNOWN_THRESHOLD = 0.70
DEFAULT_BUBA_TIMEOUT_SEC = 90

CHAT_BINDINGS_BY_USER_ID: dict[int, int] = {}
LAST_KNOWN_CHAT_ID: int | None = None
CHAT_BINDINGS_LOCK = threading.Lock()
LATEST_ROBOT_FRAME: dict[str, Any] = {}
LATEST_ROBOT_FRAME_LOCK = threading.Lock()
CAPTURE_CONDITION = threading.Condition()
CAPTURE_REQUESTS: dict[str, dict[str, Any]] = {}
CAPTURE_QUEUE: list[str] = []


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


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        logging.warning("%s must be a number, using %s", name, default)
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

        if parsed.path == "/api/robot/frame":
            self.handle_robot_frame_upload()
            return

        if parsed.path == "/api/robot/capture/next":
            self.handle_robot_capture_next()
            return

        if parsed.path == "/api/robot/capture/result":
            self.handle_robot_capture_result()
            return

        if parsed.path == "/api/robot/mission/classify-point":
            self.handle_robot_mission_classify_point()
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        if self.path == "/health":
            self.send_health(head_only=True)
            return

        self.serve_file_for_path(head_only=True)

    def send_health(self, head_only: bool = False) -> None:
        robot_frame_age_sec = get_latest_robot_frame_age_sec()
        capture_counts = get_capture_counts()
        body = json.dumps(
            {
                "ok": True,
                "service": "ryan-rover-miniapp",
                "bot": bool(os.getenv("BOT_TOKEN") and os.getenv("WEBAPP_URL")),
                "robot_frame": robot_frame_age_sec is not None,
                "robot_frame_age_sec": robot_frame_age_sec,
                "capture": capture_counts,
                "buba": buba_is_configured(),
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

    def read_json_body(self, max_bytes: int | None = None) -> dict[str, Any]:
        content_length = max_bytes if max_bytes is not None else env_int("MAX_POST_BODY_BYTES", 256_000)
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

    def authenticate_robot(self) -> bool:
        configured_token = os.getenv("ROBOT_PUSH_TOKEN")
        incoming_token = self.headers.get("X-Robot-Token") or ""

        if not configured_token:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "ROBOT_PUSH_TOKEN is missing on backend"},
            )
            return False

        if not hmac.compare_digest(incoming_token, configured_token):
            self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Invalid robot token"})
            return False

        return True

    def handle_robot_frame_upload(self) -> None:
        if not self.authenticate_robot():
            return

        try:
            expected = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            expected = 0

        max_bytes = max(300_000, env_int("ROBOT_MAX_FRAME_BYTES", DEFAULT_MAX_FRAME_BYTES))
        if expected <= 0 or expected > max_bytes:
            self.send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": f"Image must be between 1 and {max_bytes} bytes"},
            )
            return

        content_type = (self.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "Expected image/*"})
            return

        image_bytes = self.rfile.read(expected)
        if len(image_bytes) != expected:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Could not read full image body"})
            return

        remember_robot_frame(image_bytes, content_type)
        self.send_json(HTTPStatus.OK, {"ok": True, "bytes": len(image_bytes)})

    def handle_robot_capture_next(self) -> None:
        if not self.authenticate_robot():
            return

        timeout_sec = max(1.0, env_float("ROBOT_CAPTURE_POLL_TIMEOUT_SEC", DEFAULT_ROBOT_CAPTURE_POLL_TIMEOUT_SEC))
        request_payload = claim_capture_request(timeout_sec=timeout_sec)
        if not request_payload:
            self.send_json(HTTPStatus.OK, {"ok": True, "idle": True})
            return

        self.send_json(HTTPStatus.OK, {"ok": True, **request_payload})

    def handle_robot_capture_result(self) -> None:
        if not self.authenticate_robot():
            return

        max_bytes = max(1_000_000, env_int("ROBOT_CAPTURE_MAX_BODY_BYTES", DEFAULT_ROBOT_CAPTURE_MAX_BODY_BYTES))
        payload = self.read_json_body(max_bytes=max_bytes)
        request_id = payload.get("request_id")
        frames = payload.get("frames")

        if not isinstance(request_id, str) or not isinstance(frames, list):
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Expected request_id and frames[]"})
            return

        try:
            capture_dir = save_capture_frames(request_id, frames)
            complete_capture_request(request_id, capture_dir, len(frames))
        except KeyError:
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown capture request"})
            return
        except Exception as error:
            fail_capture_request(request_id, str(error))
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
            return

        self.send_json(HTTPStatus.OK, {"ok": True, "request_id": request_id, "frames": len(frames)})

    def handle_robot_mission_classify_point(self) -> None:
        if not self.authenticate_robot():
            return

        max_bytes = max(1_000_000, env_int("ROBOT_MISSION_MAX_BODY_BYTES", DEFAULT_ROBOT_MISSION_MAX_BODY_BYTES))
        payload = self.read_json_body(max_bytes=max_bytes)
        mission_id = payload.get("mission_id")
        point_id = payload.get("point_id")
        point = payload.get("point")
        frames = payload.get("frames")

        if not isinstance(mission_id, str) or not isinstance(point_id, str) or not isinstance(frames, list):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Expected mission_id, point_id and frames[]"},
            )
            return
        if point is not None and not isinstance(point, dict):
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "point must be an object"})
            return

        try:
            capture_dir = save_mission_frames(mission_id, point_id, frames)
            report = run_buba_inference(
                capture_dir,
                request_id=f"mission_{safe_path_part(mission_id)}_{safe_path_part(point_id)}",
            )
        except Exception as error:
            logging.exception("Failed to classify mission point %s/%s", mission_id, point_id)
            self.send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "ok": False,
                    "mission_id": mission_id,
                    "point_id": point_id,
                    "error": str(error),
                },
            )
            return

        self.send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "mission_id": mission_id,
                "point_id": point_id,
                "point": point or {},
                "capture_dir": str(capture_dir),
                **summarize_report(report),
            },
        )

    def handle_start_request(self) -> None:
        token = os.getenv("BOT_TOKEN")
        if not token:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "BOT_TOKEN is missing on backend"},
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

        frame_count = max(1, env_int("ROBOT_CAPTURE_FRAME_COUNT", DEFAULT_ROBOT_CAPTURE_FRAME_COUNT))
        wait_timeout_sec = max(2.0, env_float("ROBOT_CAPTURE_WAIT_TIMEOUT_SEC", DEFAULT_ROBOT_CAPTURE_WAIT_TIMEOUT_SEC))
        request_id = create_capture_request(frame_count=frame_count)

        try:
            capture = wait_for_capture_request(request_id, timeout_sec=wait_timeout_sec)
            report = run_buba_inference(capture["capture_dir"], request_id=request_id)
            message = format_analysis_message(report)
            response = telegram_request(
                token=token,
                method="sendMessage",
                payload={"chat_id": chat_id, "text": message},
            )
        except Exception as error:
            logging.exception("Failed to capture robot frames and run analysis")
            notify_text = f"Не удалось выполнить анализ: {error}"
            try:
                telegram_request(token=token, method="sendMessage", payload={"chat_id": chat_id, "text": notify_text})
            except Exception:
                logging.exception("Failed to notify Telegram about analysis error")
            self.send_json(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "request_id": request_id, "error": notify_text},
            )
            return

        if not response.get("ok"):
            self.send_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "request_id": request_id, "error": "Telegram sendMessage failed"})
            return

        self.send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "chat_id": chat_id,
                "request_id": request_id,
                "result": summarize_report(report),
            },
        )

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


def get_capture_root() -> Path:
    return Path(os.getenv("ROBOT_CAPTURE_DIR", "/tmp/ryan-rover-captures"))


def cleanup_old_capture_requests(max_age_sec: int = 600) -> None:
    now = time.time()
    stale_ids = [
        request_id
        for request_id, request in CAPTURE_REQUESTS.items()
        if now - float(request.get("created_at", now)) > max_age_sec
    ]
    for request_id in stale_ids:
        request = CAPTURE_REQUESTS.pop(request_id, None)
        if not request:
            continue
        capture_dir = request.get("capture_dir")
        if isinstance(capture_dir, Path):
            shutil.rmtree(capture_dir, ignore_errors=True)
        if request_id in CAPTURE_QUEUE:
            CAPTURE_QUEUE.remove(request_id)


def create_capture_request(frame_count: int) -> str:
    request_id = uuid.uuid4().hex
    with CAPTURE_CONDITION:
        cleanup_old_capture_requests()
        CAPTURE_REQUESTS[request_id] = {
            "request_id": request_id,
            "frame_count": frame_count,
            "status": "waiting",
            "created_at": time.time(),
        }
        CAPTURE_QUEUE.append(request_id)
        CAPTURE_CONDITION.notify_all()
    logging.info("Created capture request %s for %s frames", request_id, frame_count)
    return request_id


def claim_capture_request(timeout_sec: float) -> dict[str, Any] | None:
    deadline = time.time() + timeout_sec
    with CAPTURE_CONDITION:
        while True:
            cleanup_old_capture_requests()
            while CAPTURE_QUEUE:
                request_id = CAPTURE_QUEUE.pop(0)
                request = CAPTURE_REQUESTS.get(request_id)
                if not request or request.get("status") != "waiting":
                    continue
                request["status"] = "assigned"
                request["assigned_at"] = time.time()
                logging.info("Assigned capture request %s", request_id)
                return {
                    "request_id": request_id,
                    "frame_count": int(request["frame_count"]),
                }

            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            CAPTURE_CONDITION.wait(timeout=remaining)


def wait_for_capture_request(request_id: str, timeout_sec: float) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    with CAPTURE_CONDITION:
        while True:
            request = CAPTURE_REQUESTS.get(request_id)
            if not request:
                raise RuntimeError("Задание захвата было удалено до завершения.")

            status = request.get("status")
            if status == "completed":
                return dict(request)
            if status == "failed":
                raise RuntimeError(str(request.get("error") or "Робот не смог снять кадры."))

            remaining = deadline - time.time()
            if remaining <= 0:
                request["status"] = "failed"
                request["error"] = "Робот не прислал кадры вовремя."
                if request_id in CAPTURE_QUEUE:
                    CAPTURE_QUEUE.remove(request_id)
                CAPTURE_CONDITION.notify_all()
                raise TimeoutError("Робот не прислал кадры вовремя.")

            CAPTURE_CONDITION.wait(timeout=remaining)


def complete_capture_request(request_id: str, capture_dir: Path, frame_count: int) -> None:
    with CAPTURE_CONDITION:
        request = CAPTURE_REQUESTS.get(request_id)
        if not request:
            shutil.rmtree(capture_dir, ignore_errors=True)
            raise KeyError(request_id)

        request.update(
            {
                "status": "completed",
                "capture_dir": capture_dir,
                "frames_received": frame_count,
                "completed_at": time.time(),
            }
        )
        CAPTURE_CONDITION.notify_all()
    logging.info("Completed capture request %s with %s frames", request_id, frame_count)


def fail_capture_request(request_id: str, error: str) -> None:
    with CAPTURE_CONDITION:
        request = CAPTURE_REQUESTS.get(request_id)
        if request:
            request["status"] = "failed"
            request["error"] = error
            CAPTURE_CONDITION.notify_all()


def get_capture_counts() -> dict[str, int]:
    with CAPTURE_CONDITION:
        counts: dict[str, int] = {}
        for request in CAPTURE_REQUESTS.values():
            status = str(request.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
        return counts


def save_capture_frames(request_id: str, frames: list[Any]) -> Path:
    if not frames:
        raise ValueError("frames[] must not be empty")

    root = get_capture_root()
    capture_dir = root / request_id
    if capture_dir.exists():
        shutil.rmtree(capture_dir)
    capture_dir.mkdir(parents=True, exist_ok=True)

    max_frame_bytes = max(300_000, env_int("ROBOT_MAX_FRAME_BYTES", DEFAULT_MAX_FRAME_BYTES))
    for idx, frame in enumerate(frames):
        content_type = "image/jpeg"
        encoded: Any = frame
        if isinstance(frame, dict):
            encoded = frame.get("data")
            content_type = str(frame.get("content_type") or content_type)

        if not isinstance(encoded, str):
            raise ValueError("Each frame must be a base64 string or an object with data")

        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except Exception as error:
            raise ValueError(f"Frame {idx} is not valid base64") from error

        if not image_bytes or len(image_bytes) > max_frame_bytes:
            raise ValueError(f"Frame {idx} has invalid size")
        if not content_type.startswith("image/"):
            raise ValueError(f"Frame {idx} has invalid content type")
        if not image_bytes.startswith(b"\xff\xd8"):
            raise ValueError(f"Frame {idx} is not a JPEG image")

        (capture_dir / f"frame_{idx:03d}.jpg").write_bytes(image_bytes)

    return capture_dir


def get_mission_root() -> Path:
    return Path(os.getenv("ROBOT_MISSION_DIR", "/tmp/ryan-rover-missions"))


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    cleaned = cleaned.strip("._-")
    if not cleaned:
        raise ValueError("Invalid empty id")
    return cleaned[:96]


def save_mission_frames(mission_id: str, point_id: str, frames: list[Any]) -> Path:
    if not frames:
        raise ValueError("frames[] must not be empty")

    mission_part = safe_path_part(mission_id)
    point_part = safe_path_part(point_id)
    capture_dir = get_mission_root() / mission_part / point_part
    if capture_dir.exists():
        shutil.rmtree(capture_dir)
    capture_dir.mkdir(parents=True, exist_ok=True)

    max_frame_bytes = max(300_000, env_int("ROBOT_MAX_FRAME_BYTES", DEFAULT_MAX_FRAME_BYTES))
    for idx, frame in enumerate(frames):
        content_type = "image/jpeg"
        encoded: Any = frame
        if isinstance(frame, dict):
            encoded = frame.get("data")
            content_type = str(frame.get("content_type") or content_type)

        if not isinstance(encoded, str):
            raise ValueError("Each frame must be a base64 string or an object with data")

        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except Exception as error:
            raise ValueError(f"Frame {idx} is not valid base64") from error

        if not image_bytes or len(image_bytes) > max_frame_bytes:
            raise ValueError(f"Frame {idx} has invalid size")
        if not content_type.startswith("image/"):
            raise ValueError(f"Frame {idx} has invalid content type")
        if not image_bytes.startswith(b"\xff\xd8"):
            raise ValueError(f"Frame {idx} is not a JPEG image")

        (capture_dir / f"frame_{idx:03d}.jpg").write_bytes(image_bytes)

    return capture_dir


def buba_is_configured() -> bool:
    buba_dir = Path(os.getenv("BUBA_DIR", "/opt/apps/buba"))
    return (
        buba_dir.exists()
        and (buba_dir / ".venv/bin/python").exists()
        and (buba_dir / "scripts/infer_gate5.py").exists()
        and (buba_dir / "scripts/infer_burst.py").exists()
        and (buba_dir / "outputs_gate5/checkpoints/best.pt").exists()
        and (buba_dir / "outputs_swin_burst/checkpoints/best.pt").exists()
    )


def run_buba_inference(capture_dir: Path, request_id: str) -> dict[str, Any]:
    gate_report = run_buba_gate_inference(capture_dir, request_id=request_id)
    if not gate_allows_big_model(gate_report):
        return build_unknown_gate_report(gate_report)

    burst_report = run_buba_burst_inference(capture_dir, request_id=request_id)
    burst_report["gate_status"] = gate_report.get("status")
    burst_report["gate_frames_passed"] = gate_report.get("frames_passed")
    burst_report["gate_known_ratio"] = gate_report.get("known_ratio")
    burst_report["gate_report_path"] = gate_report.get("report_path")
    return burst_report


def run_buba_gate_inference(capture_dir: Path, request_id: str) -> dict[str, Any]:
    buba_dir = Path(os.getenv("BUBA_DIR", "/opt/apps/buba"))
    python_bin = Path(os.getenv("BUBA_PYTHON", str(buba_dir / ".venv/bin/python")))
    script_path = Path(os.getenv("BUBA_GATE_SCRIPT", str(buba_dir / "scripts/infer_gate5.py")))
    checkpoint_path = Path(os.getenv("BUBA_GATE_CHECKPOINT", str(buba_dir / "outputs_gate5/checkpoints/best.pt")))
    reports_dir = Path(os.getenv("BUBA_GATE_REPORTS_DIR", str(buba_dir / "outputs_gate5/reports")))
    report_path = reports_dir / f"miniapp_gate_{request_id}.json"
    timeout_sec = max(5, env_int("BUBA_GATE_TIMEOUT_SEC", DEFAULT_BUBA_GATE_TIMEOUT_SEC))
    known_threshold = min(
        1.0,
        max(0.0, env_float("BUBA_GATE_KNOWN_THRESHOLD", DEFAULT_BUBA_GATE_KNOWN_THRESHOLD)),
    )

    reports_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(python_bin),
        str(script_path),
        "--checkpoint",
        str(checkpoint_path),
        "--frames",
        str(capture_dir),
        "--known-threshold",
        str(known_threshold),
        "--out",
        str(report_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(buba_dir),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Gate-модель завершилась с ошибкой: {stderr[-1000:]}")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError("Gate-модель не записала корректный JSON-отчёт") from error

    report["report_path"] = str(report_path)
    report["known_threshold"] = known_threshold
    return report


def run_buba_burst_inference(capture_dir: Path, request_id: str) -> dict[str, Any]:
    buba_dir = Path(os.getenv("BUBA_DIR", "/opt/apps/buba"))
    python_bin = Path(os.getenv("BUBA_PYTHON", str(buba_dir / ".venv/bin/python")))
    script_path = Path(os.getenv("BUBA_INFER_SCRIPT", str(buba_dir / "scripts/infer_burst.py")))
    checkpoint_path = Path(os.getenv("BUBA_CHECKPOINT", str(buba_dir / "outputs_swin_burst/checkpoints/best.pt")))
    reports_dir = Path(os.getenv("BUBA_REPORTS_DIR", str(buba_dir / "outputs_swin_burst/reports")))
    report_path = reports_dir / f"miniapp_{request_id}.json"
    timeout_sec = max(5, env_int("BUBA_TIMEOUT_SEC", DEFAULT_BUBA_TIMEOUT_SEC))

    reports_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(python_bin),
        str(script_path),
        "--checkpoint",
        str(checkpoint_path),
        "--frames",
        str(capture_dir),
        "--out",
        str(report_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(buba_dir),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Модель завершилась с ошибкой: {stderr[-1000:]}")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError("Модель не записала корректный JSON-отчёт") from error

    report["report_path"] = str(report_path)
    return report


def gate_allows_big_model(gate_report: dict[str, Any]) -> bool:
    frames_passed = gate_report.get("frames_passed")
    if isinstance(frames_passed, int):
        return frames_passed > 0

    frames = gate_report.get("frames")
    if isinstance(frames, list):
        return any(isinstance(frame, dict) and bool(frame.get("passed_to_big_model")) for frame in frames)

    return gate_report.get("status") == "known"


def build_unknown_gate_report(gate_report: dict[str, Any]) -> dict[str, Any]:
    frames = gate_report.get("frames")
    frames_total = int(gate_report.get("frames_total") or (len(frames) if isinstance(frames, list) else 0))
    frames_passed = int(gate_report.get("frames_passed") or 0)

    return {
        "final_class": "unknown",
        "confidence": None,
        "status": "unknown",
        "reason": "gate-модель не нашла объект из известных классов",
        "frames_valid": frames_passed,
        "frames_total": frames_total,
        "gate_status": gate_report.get("status"),
        "gate_frames_passed": frames_passed,
        "gate_known_ratio": gate_report.get("known_ratio"),
        "gate_report_path": gate_report.get("report_path"),
    }


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "final_class": report.get("final_class"),
        "confidence": report.get("confidence"),
        "status": report.get("status"),
        "reason": report.get("reason"),
        "frames_valid": report.get("frames_valid"),
        "frames_total": report.get("frames_total"),
        "report_path": report.get("report_path"),
        "gate_status": report.get("gate_status"),
        "gate_frames_passed": report.get("gate_frames_passed"),
        "gate_known_ratio": report.get("gate_known_ratio"),
        "gate_report_path": report.get("gate_report_path"),
    }


def format_analysis_message(report: dict[str, Any]) -> str:
    final_class = str(report.get("final_class", "unknown"))
    status = str(report.get("status", "unknown"))
    frames_valid = int(report.get("frames_valid") or 0)
    frames_total = int(report.get("frames_total") or 0)
    display_class = "неизвестный" if final_class == "unknown" else final_class

    lines = [
        "Результат анализа:",
        f"Класс: {display_class}",
    ]

    confidence = report.get("confidence")
    if isinstance(confidence, (int, float)):
        lines.append(f"Accuracy: {float(confidence) * 100:.1f}%")

    lines.extend(
        [
            f"Статус: {status}",
            f"Кадры: {frames_valid}/{frames_total}",
        ]
    )

    reason = report.get("reason")
    if isinstance(reason, str) and reason:
        lines.append(f"Причина: {reason}.")
    elif status == "unknown":
        lines.append("Причина: gate-модель не нашла объект из известных классов.")
    elif status in {"uncertain", "retry"}:
        lines.append("Причина: мало уверенности или недостаточно резких кадров.")

    return "\n".join(lines)


def remember_robot_frame(image_bytes: bytes, content_type: str) -> None:
    with LATEST_ROBOT_FRAME_LOCK:
        LATEST_ROBOT_FRAME.clear()
        LATEST_ROBOT_FRAME.update(
            {
                "bytes": image_bytes,
                "content_type": content_type,
                "received_at": time.time(),
            }
        )


def get_latest_robot_frame(max_age_sec: int) -> tuple[bytes, str] | None:
    with LATEST_ROBOT_FRAME_LOCK:
        image_bytes = LATEST_ROBOT_FRAME.get("bytes")
        content_type = LATEST_ROBOT_FRAME.get("content_type")
        received_at = LATEST_ROBOT_FRAME.get("received_at")

        if not isinstance(image_bytes, bytes) or not isinstance(content_type, str):
            return None

        if not isinstance(received_at, (int, float)) or time.time() - received_at > max_age_sec:
            return None

        return image_bytes, content_type


def get_latest_robot_frame_age_sec() -> int | None:
    with LATEST_ROBOT_FRAME_LOCK:
        received_at = LATEST_ROBOT_FRAME.get("received_at")

    if not isinstance(received_at, (int, float)):
        return None

    return max(0, int(time.time() - received_at))


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
