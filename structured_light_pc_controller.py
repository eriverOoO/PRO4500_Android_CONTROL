#!/usr/bin/env python3
"""PC master controller for phone-camera structured-light capture.

This controller owns the pattern display timing and the Android capture
handshake. It advances to the next pattern only after the current phone image
has been uploaded and the phone has sent capture_done.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect


IMAGE_SUFFIXES = {".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean value, got {value!r}")


def parse_csv_ints(value: str, label: str) -> list[int]:
    try:
        items = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be comma-separated integers") from exc
    if not items:
        raise argparse.ArgumentTypeError(f"{label} cannot be empty")
    return items


def safe_scan_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError("scan_id may contain only letters, numbers, '.', '_' and '-'")
    return value


def pattern_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"^(\d+)", path.name)
    index = int(match.group(1)) if match else 1_000_000
    return index, path.name.lower()


def load_patterns(pattern_dir: Path) -> list[Path]:
    if not pattern_dir.exists():
        raise SystemExit(f"Pattern directory does not exist: {pattern_dir}")
    patterns = sorted(
        [
            path
            for path in pattern_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ],
        key=pattern_sort_key,
    )
    if not patterns:
        raise SystemExit(f"No pattern images found in {pattern_dir}")
    return patterns


def read_image(path: Path) -> np.ndarray:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Could not decode image: {path}")
    return image


def guess_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def local_url_host(args: argparse.Namespace) -> str:
    if args.public_host:
        return args.public_host
    if args.host not in {"0.0.0.0", "::"}:
        return args.host
    return guess_lan_ip()


@dataclass
class PendingCapture:
    scan_id: str
    pattern_id: int
    capture_id: int
    angle_deg: int
    attempt: int
    result_future: asyncio.Future
    upload_record: dict[str, Any] | None = None
    done_message: dict[str, Any] | None = None
    error_message: dict[str, Any] | None = None

    @property
    def key(self) -> tuple[str, int, int]:
        return self.scan_id, self.pattern_id, self.capture_id

    def mark_upload(self, record: dict[str, Any]) -> None:
        self.upload_record = record
        self._complete_if_ready()

    def mark_done(self, message: dict[str, Any]) -> None:
        self.done_message = message
        self._complete_if_ready()

    def mark_error(self, message: dict[str, Any]) -> None:
        self.error_message = message
        if not self.result_future.done():
            self.result_future.set_exception(RuntimeError(message.get("error", "capture_error")))

    def _complete_if_ready(self) -> None:
        if self.result_future.done():
            return
        if self.upload_record is not None and self.done_message is not None:
            self.result_future.set_result(
                {
                    "upload": self.upload_record,
                    "done": self.done_message,
                }
            )


class ControllerState:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.websocket: WebSocket | None = None
        self.client_label = ""
        self.connected_event = asyncio.Event()
        self.pending: dict[tuple[str, int, int], PendingCapture] = {}
        self.lock = asyncio.Lock()
        self.messages: list[dict[str, Any]] = []
        self.pong_event = asyncio.Event()

    async def set_connection(self, websocket: WebSocket) -> None:
        async with self.lock:
            self.websocket = websocket
            client = websocket.client
            self.client_label = f"{client.host}:{client.port}" if client else "unknown"
            self.connected_event.set()
            print(f"[ws] Android connected: {self.client_label}")

    async def clear_connection(self, websocket: WebSocket) -> None:
        async with self.lock:
            if self.websocket is websocket:
                self.websocket = None
                self.client_label = ""
                self.connected_event.clear()
                print("[ws] Android disconnected")

    async def wait_for_phone(self) -> None:
        await self.connected_event.wait()

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.websocket is None:
            raise RuntimeError("Android phone is not connected")
        await self.websocket.send_text(json.dumps(message, separators=(",", ":")))

    def register_pending(
        self,
        *,
        scan_id: str,
        pattern_id: int,
        capture_id: int,
        angle_deg: int,
        attempt: int,
    ) -> PendingCapture:
        future = asyncio.get_running_loop().create_future()
        pending = PendingCapture(
            scan_id=scan_id,
            pattern_id=pattern_id,
            capture_id=capture_id,
            angle_deg=angle_deg,
            attempt=attempt,
            result_future=future,
        )
        self.pending[pending.key] = pending
        return pending

    def finish_pending(self, key: tuple[str, int, int]) -> None:
        self.pending.pop(key, None)

    def resolve_upload(self, record: dict[str, Any]) -> None:
        key = (
            record["scan_id"],
            int(record["pattern_id"]),
            int(record["capture_id"]),
        )
        pending = self.pending.get(key)
        if pending is None:
            print(f"[upload] Received image for non-pending capture: {key}")
            return
        pending.mark_upload(record)

    def resolve_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        msg_type = message.get("type")
        if msg_type == "pong":
            print("[ws] pong")
            self.pong_event.set()
            return

        if msg_type not in {"capture_done", "capture_error"}:
            print(f"[ws] ignored message: {message}")
            return

        try:
            key = (
                str(message["scan_id"]),
                int(message["pattern_id"]),
                int(message["capture_id"]),
            )
        except (KeyError, TypeError, ValueError):
            print(f"[ws] malformed capture message: {message}")
            return

        pending = self.pending.get(key)
        if pending is None:
            print(f"[ws] message for non-pending capture: {key}")
            return

        if msg_type == "capture_error":
            pending.mark_error(message)
        else:
            pending.mark_done(message)


def create_app(state: ControllerState) -> FastAPI:
    app = FastAPI(title="Structured Light PC Controller")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "phone_connected": state.websocket is not None,
            "client": state.client_label,
        }

    @app.post("/upload")
    async def upload(
        scan_id: str = Form(...),
        pattern_id: int = Form(...),
        capture_id: int = Form(...),
        angle_deg: int | None = Form(None),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        scan_id = safe_scan_id(scan_id)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".dng"}:
            suffix = ".jpg"

        angle_text = "" if angle_deg is None else f"_angle_{angle_deg:03d}"
        filename = f"{scan_id}{angle_text}_pattern_{pattern_id:03d}_capture_{capture_id:03d}{suffix}"
        scan_dir = state.output_root / scan_id
        scan_dir.mkdir(parents=True, exist_ok=True)
        destination = scan_dir / filename

        size_bytes = 0
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                output.write(chunk)

        upload_record = {
            "scan_id": scan_id,
            "pattern_id": pattern_id,
            "capture_id": capture_id,
            "angle_deg": angle_deg,
            "filename": filename,
            "path": str(destination),
            "size_bytes": size_bytes,
            "upload_timestamp_pc_ms": now_ms(),
        }
        state.resolve_upload(upload_record)
        print(f"[upload] saved {filename} ({size_bytes} bytes)")
        return {"status": "ok", "filename": filename, "size_bytes": size_bytes}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await state.set_connection(websocket)
        try:
            while True:
                text = await websocket.receive_text()
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    print(f"[ws] invalid JSON: {text}")
                    continue
                state.resolve_message(message)
        except WebSocketDisconnect:
            pass
        finally:
            await state.clear_connection(websocket)

    return app


@dataclass
class MonitorBounds:
    x: int
    y: int
    width: int
    height: int


class PatternDisplay:
    def __init__(self, args: argparse.Namespace, first_image: np.ndarray) -> None:
        self.window_name = args.window_name
        self.windowed = args.windowed
        self.monitor_index = args.monitor
        self.window_x = args.window_x
        self.window_y = args.window_y
        self.keep_aspect = not args.stretch
        self.bounds = self._detect_bounds(first_image)

    def _detect_bounds(self, first_image: np.ndarray) -> MonitorBounds:
        height, width = first_image.shape[:2]
        if self.windowed:
            return MonitorBounds(
                x=self.window_x or 80,
                y=self.window_y or 80,
                width=width,
                height=height,
            )

        try:
            from screeninfo import get_monitors

            monitors = get_monitors()
            if self.monitor_index < 0 or self.monitor_index >= len(monitors):
                raise IndexError
            monitor = monitors[self.monitor_index]
            return MonitorBounds(monitor.x, monitor.y, monitor.width, monitor.height)
        except Exception:
            print(
                "[display] Could not read monitor geometry. "
                "Using image size; pass --window-x/--window-y or install screeninfo if needed."
            )
            return MonitorBounds(
                x=self.window_x or 0,
                y=self.window_y or 0,
                width=width,
                height=height,
            )

    def open(self) -> None:
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.moveWindow(self.window_name, self.bounds.x, self.bounds.y)
        cv2.resizeWindow(self.window_name, self.bounds.width, self.bounds.height)
        if not self.windowed:
            cv2.setWindowProperty(
                self.window_name,
                cv2.WND_PROP_FULLSCREEN,
                cv2.WINDOW_FULLSCREEN,
            )
        print(
            "[display] window="
            f"{self.window_name!r} x={self.bounds.x} y={self.bounds.y} "
            f"w={self.bounds.width} h={self.bounds.height}"
        )

    def render(self, image: np.ndarray) -> np.ndarray:
        if not self.keep_aspect:
            return cv2.resize(
                image,
                (self.bounds.width, self.bounds.height),
                interpolation=cv2.INTER_NEAREST,
            )

        image_h, image_w = image.shape[:2]
        scale = min(self.bounds.width / image_w, self.bounds.height / image_h)
        out_w = max(1, int(round(image_w * scale)))
        out_h = max(1, int(round(image_h * scale)))
        resized = cv2.resize(image, (out_w, out_h), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((self.bounds.height, self.bounds.width, 3), dtype=np.uint8)
        x = (self.bounds.width - out_w) // 2
        y = (self.bounds.height - out_h) // 2
        canvas[y : y + out_h, x : x + out_w] = resized
        return canvas

    def show(self, image: np.ndarray) -> None:
        cv2.imshow(self.window_name, self.render(image))
        cv2.waitKey(1)

    def black(self) -> None:
        image = np.zeros((self.bounds.height, self.bounds.width, 3), dtype=np.uint8)
        cv2.imshow(self.window_name, image)
        cv2.waitKey(1)

    def close(self) -> None:
        cv2.destroyWindow(self.window_name)


def make_capture_message(
    args: argparse.Namespace,
    *,
    scan_id: str,
    pattern_id: int,
    capture_id: int,
    angle_deg: int,
    attempt: int,
    upload_url: str,
) -> dict[str, Any]:
    return {
        "type": "capture",
        "scan_id": scan_id,
        "pattern_id": pattern_id,
        "capture_id": capture_id,
        "angle_deg": angle_deg,
        "attempt": attempt,
        "upload_url": upload_url,
        "settings": {
            "manual": args.manual,
            "exposure_us": args.exposure_us,
            "iso": args.iso,
            "focus_diopters": args.focus_diopters,
            "settle_ms_before_capture": args.phone_settle_ms,
        },
    }


async def run_rotation_command(
    command_template: str,
    *,
    angle: int,
    angle_index: int,
    previous_angle: int | None,
    scan_dir: Path,
) -> None:
    command = command_template.format(
        angle=angle,
        angle_index=angle_index,
        previous_angle="" if previous_angle is None else previous_angle,
        scan_dir=str(scan_dir),
    )
    print(f"[rotation] {command}")
    completed = await asyncio.to_thread(subprocess.run, command, shell=True)
    if completed.returncode != 0:
        raise RuntimeError(f"rotation command failed with exit code {completed.returncode}")


def read_angle_advance_token(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


async def wait_for_angle_advance(path: Path, *, angle: int, angle_index: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wait_started_ms = now_ms()
    print(
        f"[angle] Waiting for rotation to angle={angle:03d} "
        f"(index={angle_index}). Click Next Angle in the PC controller.",
        flush=True,
    )
    while True:
        token = read_angle_advance_token(path)
        if token is not None and token >= wait_started_ms:
            print(f"[angle] Continue angle={angle:03d}", flush=True)
            return
        await asyncio.sleep(0.2)


def append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = [
        "scan_id",
        "angle_deg",
        "pattern_id",
        "capture_id",
        "attempt",
        "pattern_filename",
        "pattern_display_timestamp_pc_ms",
        "capture_command_timestamp_pc_ms",
        "upload_timestamp_pc_ms",
        "timestamp_phone_ms",
        "received_image_filename",
        "status",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


async def run_scan(args: argparse.Namespace) -> int:
    pattern_dir = args.patterns.resolve()
    patterns = load_patterns(pattern_dir)
    first_image = read_image(patterns[0])
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    scan_id = safe_scan_id(args.scan_id or datetime.now().strftime("scan_%Y%m%d_%H%M%S"))
    scan_dir = output_root / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)

    state = ControllerState(output_root=output_root)
    app = create_app(state)
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level=args.log_level)
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    upload_host = local_url_host(args)
    upload_url = f"http://{upload_host}:{args.port}/upload"
    ws_url = f"ws://{upload_host}:{args.port}/ws"
    angles = parse_csv_ints(args.angles, "angles")
    scan_rows: list[dict[str, Any]] = []
    display: PatternDisplay | None = None
    capture_id = 0
    aborted = False

    print(f"[server] WebSocket URL for Android: {ws_url}")
    print(f"[server] Upload URL sent to Android: {upload_url}")
    print(f"[scan] scan_id={scan_id} patterns={len(patterns)} angles={angles}")

    try:
        await asyncio.sleep(0.5)
        if args.server_only:
            print("[server] Running in server-only mode. Press Ctrl+C to stop.")
            while True:
                await asyncio.sleep(3600)

        print("[scan] Waiting for Android app WebSocket connection...")
        await state.wait_for_phone()
        if not args.no_ping_check:
            state.pong_event.clear()
            await state.send_json({"type": "ping", "timestamp_pc_ms": now_ms()})
            try:
                await asyncio.wait_for(state.pong_event.wait(), args.ping_timeout)
                print("[scan] ping/pong check ok")
            except asyncio.TimeoutError:
                print("[scan] ping/pong check timed out; continuing with capture handshake")

        if not args.no_display:
            display = PatternDisplay(args, first_image)
            display.open()
            display.black()
            await asyncio.sleep(args.pre_black_ms / 1000.0)

        previous_angle: int | None = None
        for angle_index, angle in enumerate(angles):
            if args.rotation_command and (angle_index > 0 or args.rotate_first_angle):
                if display is not None:
                    display.black()
                await run_rotation_command(
                    args.rotation_command,
                    angle=angle,
                    angle_index=angle_index,
                    previous_angle=previous_angle,
                    scan_dir=scan_dir,
                )
            elif angle_index > 0 or args.pause_before_first_angle:
                if display is not None:
                    display.black()
                if args.angle_advance_file:
                    await wait_for_angle_advance(
                        args.angle_advance_file,
                        angle=angle,
                        angle_index=angle_index,
                    )
                elif not args.no_angle_prompt:
                    await asyncio.to_thread(
                        input,
                        f"Set rotation stage to {angle} degrees, then press Enter...",
                    )

            for pattern_id, pattern_path in enumerate(patterns):
                image = read_image(pattern_path)
                success = False
                last_error = ""

                for attempt in range(1, args.retries + 2):
                    if display is not None:
                        display.show(image)
                    display_ts = now_ms()
                    await asyncio.sleep(args.settle_ms / 1000.0)

                    pending = state.register_pending(
                        scan_id=scan_id,
                        pattern_id=pattern_id,
                        capture_id=capture_id,
                        angle_deg=angle,
                        attempt=attempt,
                    )
                    message = make_capture_message(
                        args,
                        scan_id=scan_id,
                        pattern_id=pattern_id,
                        capture_id=capture_id,
                        angle_deg=angle,
                        attempt=attempt,
                        upload_url=upload_url,
                    )
                    command_ts = now_ms()
                    await state.send_json(message)
                    print(
                        f"[capture] angle={angle:03d} pattern={pattern_id:03d} "
                        f"capture={capture_id:03d} attempt={attempt}"
                    )

                    row: dict[str, Any] = {
                        "scan_id": scan_id,
                        "angle_deg": angle,
                        "pattern_id": pattern_id,
                        "capture_id": capture_id,
                        "attempt": attempt,
                        "pattern_filename": pattern_path.name,
                        "pattern_display_timestamp_pc_ms": display_ts,
                        "capture_command_timestamp_pc_ms": command_ts,
                    }

                    try:
                        timeout_s = args.capture_timeout + args.upload_timeout
                        result = await asyncio.wait_for(pending.result_future, timeout_s)
                        upload = result["upload"]
                        done = result["done"]
                        row.update(
                            {
                                "upload_timestamp_pc_ms": upload.get("upload_timestamp_pc_ms"),
                                "timestamp_phone_ms": done.get("timestamp_phone_ms"),
                                "received_image_filename": upload.get("filename"),
                                "status": "ok",
                                "error": "",
                            }
                        )
                        scan_rows.append(row)
                        success = True
                        state.finish_pending(pending.key)
                        capture_id += 1
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        row.update(
                            {
                                "status": "retry" if attempt <= args.retries else "failed",
                                "error": last_error,
                            }
                        )
                        scan_rows.append(row)
                        state.finish_pending(pending.key)
                        print(
                            f"[capture] failed angle={angle:03d} pattern={pattern_id:03d} "
                            f"capture={capture_id:03d}: {last_error}"
                        )
                        capture_id += 1
                        if attempt <= args.retries:
                            await asyncio.sleep(args.retry_delay_ms / 1000.0)

                if not success:
                    aborted = True
                    raise RuntimeError(
                        f"scan aborted at angle={angle} pattern={pattern_id}: {last_error}"
                    )

            previous_angle = angle

    except KeyboardInterrupt:
        aborted = True
        print("[scan] Interrupted by user")
    except Exception as exc:
        aborted = True
        print(f"[scan] ERROR: {exc}")
    finally:
        if display is not None:
            display.black()
            await asyncio.sleep(args.finish_black_ms / 1000.0)
            display.close()

        log = {
            "scan_id": scan_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "aborted" if aborted else "ok",
            "pattern_dir": str(pattern_dir),
            "patterns": [path.name for path in patterns],
            "angles_deg": angles,
            "settings": {
                "settle_ms": args.settle_ms,
                "phone_settle_ms": args.phone_settle_ms,
                "manual": args.manual,
                "exposure_us": args.exposure_us,
                "iso": args.iso,
                "focus_diopters": args.focus_diopters,
                "capture_timeout": args.capture_timeout,
                "upload_timeout": args.upload_timeout,
                "retries": args.retries,
            },
            "rows": scan_rows,
        }
        (scan_dir / "scan_log.json").write_text(
            json.dumps(log, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        append_csv(scan_dir / "scan_log.csv", scan_rows)
        print(f"[scan] log saved: {scan_dir / 'scan_log.json'}")
        print(f"[scan] csv saved: {scan_dir / 'scan_log.csv'}")

        server.should_exit = True
        await server_task

    return 1 if aborted else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display structured-light patterns and trigger Android captures."
    )
    parser.add_argument("--patterns", default="generated_patterns", type=Path)
    parser.add_argument("--output", default="captures", type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--public-host", help="LAN IP/hostname sent to Android upload_url.")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--monitor", default=1, type=int)
    parser.add_argument("--window-name", default="StructuredLight Projection")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--window-x", type=int)
    parser.add_argument("--window-y", type=int)
    parser.add_argument("--stretch", action="store_true", help="Stretch pattern to screen.")
    parser.add_argument("--settle-ms", default=300, type=int)
    parser.add_argument("--phone-settle-ms", default=0, type=int)
    parser.add_argument("--pre-black-ms", default=300, type=int)
    parser.add_argument("--finish-black-ms", default=300, type=int)
    parser.add_argument("--capture-timeout", default=10.0, type=float)
    parser.add_argument("--upload-timeout", default=10.0, type=float)
    parser.add_argument("--retries", default=2, type=int)
    parser.add_argument("--retry-delay-ms", default=300, type=int)
    parser.add_argument("--manual", default=True, type=parse_bool)
    parser.add_argument("--exposure-us", default=10000, type=int)
    parser.add_argument("--iso", default=100, type=int)
    parser.add_argument("--focus-diopters", default=0.0, type=float)
    parser.add_argument("--angles", default="0")
    parser.add_argument("--pause-before-first-angle", action="store_true")
    parser.add_argument("--no-angle-prompt", action="store_true")
    parser.add_argument("--angle-advance-file", type=Path)
    parser.add_argument("--rotation-command")
    parser.add_argument("--rotate-first-angle", action="store_true")
    parser.add_argument("--scan-id")
    parser.add_argument("--server-only", action="store_true")
    parser.add_argument("--no-display", action="store_true")
    parser.add_argument("--no-ping-check", action="store_true")
    parser.add_argument("--ping-timeout", default=2.0, type=float)
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
    )
    return parser.parse_args()


def main() -> int:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")
    return asyncio.run(run_scan(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
