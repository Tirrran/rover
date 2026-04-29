import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "https://www.adolanna.ru"
DEFAULT_RTSP_URL = "rtsp://172.18.0.2:8554/cam"
DEFAULT_POLL_TIMEOUT_SEC = 25
DEFAULT_INTERVAL_SEC = 0.25
DEFAULT_REQUEST_TIMEOUT_SEC = 35
DEFAULT_MAX_FRAME_BYTES = 3_500_000


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def post_json(url: str, token: str, payload: dict, timeout_sec: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Robot-Token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def capture_jpeg(rtsp_url: str, timeout_sec: int, max_bytes: int) -> bytes:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
    )
    if len(completed.stdout) > max_bytes:
        raise ValueError("Captured frame is too large")
    if not completed.stdout.startswith(b"\xff\xd8"):
        raise ValueError("ffmpeg did not return a JPEG frame")
    return completed.stdout


def capture_frames(rtsp_url: str, frame_count: int, interval_sec: float, timeout_sec: int, max_bytes: int) -> list[str]:
    frames: list[str] = []
    for idx in range(frame_count):
        frame = capture_jpeg(rtsp_url, timeout_sec=timeout_sec, max_bytes=max_bytes)
        frames.append(base64.b64encode(frame).decode("ascii"))
        if idx < frame_count - 1:
            time.sleep(interval_sec)
    return frames


def main() -> None:
    base_url = os.getenv("MINIAPP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    next_url = os.getenv("MINIAPP_CAPTURE_NEXT_URL", f"{base_url}/api/robot/capture/next")
    result_url = os.getenv("MINIAPP_CAPTURE_RESULT_URL", f"{base_url}/api/robot/capture/result")
    token = os.getenv("ROBOT_PUSH_TOKEN")
    rtsp_url = os.getenv("ROBOT_RTSP_URL", DEFAULT_RTSP_URL)
    poll_timeout_sec = env_int("ROBOT_CAPTURE_POLL_TIMEOUT_SEC", DEFAULT_POLL_TIMEOUT_SEC)
    interval_sec = env_float("ROBOT_CAPTURE_INTERVAL_SEC", DEFAULT_INTERVAL_SEC)
    request_timeout_sec = env_int("ROBOT_CAPTURE_REQUEST_TIMEOUT_SEC", DEFAULT_REQUEST_TIMEOUT_SEC)
    max_frame_bytes = env_int("ROBOT_MAX_FRAME_BYTES", DEFAULT_MAX_FRAME_BYTES)

    if not token:
        raise SystemExit("ROBOT_PUSH_TOKEN is required")

    print(f"robot capture agent polling {next_url}", flush=True)
    while True:
        try:
            payload = post_json(next_url, token=token, payload={}, timeout_sec=poll_timeout_sec + 5)
            if payload.get("idle"):
                continue
            if not payload.get("ok"):
                print(f"next request failed: {payload}", flush=True)
                time.sleep(2)
                continue

            request_id = payload.get("request_id")
            frame_count = int(payload.get("frame_count") or 1)
            if not isinstance(request_id, str):
                print(f"next request missing request_id: {payload}", flush=True)
                continue

            print(f"capturing request={request_id} frames={frame_count}", flush=True)
            frames = capture_frames(
                rtsp_url,
                frame_count=frame_count,
                interval_sec=interval_sec,
                timeout_sec=request_timeout_sec,
                max_bytes=max_frame_bytes,
            )
            result = post_json(
                result_url,
                token=token,
                payload={"request_id": request_id, "frames": frames},
                timeout_sec=request_timeout_sec,
            )
            print(f"uploaded request={request_id} result={result}", flush=True)
        except urllib.error.URLError as error:
            print(f"network error: {error}", flush=True)
            time.sleep(3)
        except subprocess.CalledProcessError as error:
            message = error.stderr.decode("utf-8", errors="ignore").strip()
            print(f"ffmpeg error: {message}", flush=True)
            time.sleep(2)
        except Exception as error:
            print(f"agent error: {error}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
