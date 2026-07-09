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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect


IMAGE_SUFFIXES = {".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
PATTERN_LABELS: dict[int, str] = {
    0: "White",
    1: "Black",
    2: "Gray0",
    3: "Gray1",
    4: "Gray2",
    5: "Gray3",
    6: "Gray4",
    7: "Gray5",
    8: "Gray6",
    9: "Gray7",
    10: "Sine_000",
    11: "Sine_090",
    12: "Sine_180",
    13: "Sine_270",
    14: "Gray0_inv",
    15: "Gray1_inv",
    16: "Gray2_inv",
    17: "Gray3_inv",
    18: "Gray4_inv",
    19: "Gray5_inv",
    20: "Gray6_inv",
    21: "Gray7_inv",
}
FULL_PATTERN_IDS = tuple(range(22))
LEGACY_PATTERN_IDS = tuple(range(14))
INTERLEAVED_22_ORDER = (
    0,
    1,
    2,
    14,
    3,
    15,
    4,
    16,
    5,
    17,
    6,
    18,
    7,
    19,
    8,
    20,
    9,
    21,
    10,
    11,
    12,
    13,
)


@dataclass(frozen=True)
class PatternSpec:
    pattern_id: int
    label: str
    path: Path


@dataclass(frozen=True)
class ExposureBracket:
    label: str
    exposure_us: int
    iso: int

    @property
    def exposure_product(self) -> float:
        return float(max(1, self.exposure_us) * max(1, self.iso))


@dataclass(frozen=True)
class HdrSettings:
    enabled: bool
    brackets: tuple[ExposureBracket, ...]
    saturated_threshold: int
    dark_threshold: int
    bit_depth: int


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


def parse_bracket_spec(value: str) -> tuple[ExposureBracket, ...]:
    brackets: list[ExposureBracket] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split(":")]
        if len(parts) not in {2, 3}:
            raise argparse.ArgumentTypeError(
                "HDR brackets must use label:exposure_us[:iso], "
                "for example short:2500:100,mid:10000:100,long:40000:100"
            )
        label = safe_filename_stem(parts[0])
        try:
            exposure_us = int(parts[1])
            iso = int(parts[2]) if len(parts) == 3 else 100
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid HDR bracket numeric value in {item!r}"
            ) from exc
        if exposure_us <= 0 or iso <= 0:
            raise argparse.ArgumentTypeError(
                f"HDR bracket exposure_us and iso must be positive in {item!r}"
            )
        brackets.append(ExposureBracket(label=label, exposure_us=exposure_us, iso=iso))

    if not brackets:
        raise argparse.ArgumentTypeError("At least one HDR bracket is required")

    labels = [bracket.label for bracket in brackets]
    if len(labels) != len(set(labels)):
        raise argparse.ArgumentTypeError("HDR bracket labels must be unique")

    return tuple(brackets)


def safe_scan_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError("scan_id may contain only letters, numbers, '.', '_' and '-'")
    return value


def pattern_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"^(\d+)", path.name)
    index = int(match.group(1)) if match else 1_000_000
    return index, path.name.lower()


def expected_pattern_ids(mode: str) -> tuple[int, ...]:
    if mode == "legacy-14":
        return LEGACY_PATTERN_IDS
    if mode == "22":
        return FULL_PATTERN_IDS
    raise ValueError(f"unknown pattern mode: {mode}")


def label_from_filename(path: Path, pattern_id: int) -> str:
    if pattern_id in PATTERN_LABELS:
        return PATTERN_LABELS[pattern_id]
    stem = path.stem
    return re.sub(r"^\d+[_-]?", "", stem) or stem


def parse_pattern_id(path: Path, fallback: int) -> int:
    match = re.match(r"^(\d+)", path.name)
    return int(match.group(1)) if match else fallback


def load_patterns(pattern_dir: Path, mode: str) -> list[PatternSpec]:
    if not pattern_dir.exists():
        raise SystemExit(f"Pattern directory does not exist: {pattern_dir}")
    pattern_paths = sorted(
        [
            path
            for path in pattern_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ],
        key=pattern_sort_key,
    )
    if not pattern_paths:
        raise SystemExit(f"No pattern images found in {pattern_dir}")

    specs_by_id: dict[int, PatternSpec] = {}
    for fallback, path in enumerate(pattern_paths):
        pattern_id = parse_pattern_id(path, fallback)
        if pattern_id in specs_by_id:
            raise SystemExit(
                f"Duplicate pattern id {pattern_id:03d}: "
                f"{specs_by_id[pattern_id].path.name} and {path.name}"
            )
        specs_by_id[pattern_id] = PatternSpec(
            pattern_id=pattern_id,
            label=label_from_filename(path, pattern_id),
            path=path,
        )

    expected_ids = expected_pattern_ids(mode)
    missing = [pattern_id for pattern_id in expected_ids if pattern_id not in specs_by_id]
    if missing:
        missing_text = ", ".join(f"{pattern_id:03d}" for pattern_id in missing)
        raise SystemExit(
            f"Pattern directory {pattern_dir} is missing required pattern ids: {missing_text}. "
            "Run tools/generate_fpp_patterns.py to create the default 22-frame set, "
            "or pass --pattern-mode legacy-14 for the old 14-frame workflow."
        )

    unexpected = sorted(set(specs_by_id) - set(expected_ids))
    if unexpected:
        unexpected_text = ", ".join(f"{pattern_id:03d}" for pattern_id in unexpected)
        print(f"[patterns] ignoring ids outside {mode}: {unexpected_text}")

    return [specs_by_id[pattern_id] for pattern_id in expected_ids]


def read_image(path: Path) -> np.ndarray:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Could not decode image: {path}")
    return image


def read_gray_image(path: Path) -> np.ndarray:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"Could not decode grayscale image: {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise OSError(f"Could not encode image: {path}")
    path.write_bytes(encoded.tobytes())


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def safe_filename_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    stem = stem.strip("._")
    return stem or "frame"


def default_hdr_brackets(exposure_us: int, iso: int) -> tuple[ExposureBracket, ...]:
    mid = max(1, exposure_us)
    return (
        ExposureBracket("short", max(1, mid // 4), max(1, iso)),
        ExposureBracket("mid", mid, max(1, iso)),
        ExposureBracket("long", max(1, mid * 4), max(1, iso)),
    )


def read_bracket_config(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Could not read bracket config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON bracket config {path}: {exc}") from exc


def bracket_from_mapping(item: dict[str, Any]) -> ExposureBracket:
    label = safe_filename_stem(str(item.get("label", "")))
    try:
        exposure_us = int(item["exposure_us"])
        iso = int(item.get("iso", item.get("sensitivity_iso", 100)))
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            "Each bracket config item must include label, exposure_us, and optional iso"
        ) from exc
    if exposure_us <= 0 or iso <= 0:
        raise SystemExit("Bracket exposure_us and iso must be positive")
    return ExposureBracket(label=label, exposure_us=exposure_us, iso=iso)


def build_hdr_settings(args: argparse.Namespace) -> HdrSettings:
    config: dict[str, Any] = {}
    if args.bracket_config:
        config = read_bracket_config(args.bracket_config)

    saturated_threshold = int(
        config.get("saturated_threshold", args.saturated_threshold)
    )
    dark_threshold = int(config.get("dark_threshold", args.dark_threshold))
    bit_depth = int(config.get("hdr_bit_depth", args.hdr_bit_depth))
    if bit_depth not in {8, 16}:
        raise SystemExit("--hdr-bit-depth must be 8 or 16")
    if not (0 <= dark_threshold <= 255 and 0 <= saturated_threshold <= 255):
        raise SystemExit("HDR thresholds must be in the 0..255 range")
    if dark_threshold >= saturated_threshold:
        raise SystemExit("dark threshold must be lower than saturated threshold")

    hdr_requested = bool(args.enable_hdr or args.hdr_brackets or args.bracket_config)
    single_exposure_requested = args.single_exposure or args.legacy_single_exposure

    if single_exposure_requested or not hdr_requested:
        brackets = (
            ExposureBracket(
                label="single",
                exposure_us=max(1, args.exposure_us),
                iso=max(1, args.iso),
            ),
        )
        return HdrSettings(
            enabled=False,
            brackets=brackets,
            saturated_threshold=saturated_threshold,
            dark_threshold=dark_threshold,
            bit_depth=bit_depth,
        )

    if "brackets" in config:
        config_brackets = config["brackets"]
        if not isinstance(config_brackets, list) or not config_brackets:
            raise SystemExit("bracket config 'brackets' must be a non-empty list")
        brackets = tuple(bracket_from_mapping(item) for item in config_brackets)
    elif args.hdr_brackets:
        brackets = parse_bracket_spec(args.hdr_brackets)
    else:
        brackets = default_hdr_brackets(args.exposure_us, args.iso)

    return HdrSettings(
        enabled=len(brackets) > 1,
        brackets=brackets,
        saturated_threshold=saturated_threshold,
        dark_threshold=dark_threshold,
        bit_depth=bit_depth,
    )


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
    pattern_label: str = ""
    pattern_sequence_index: int = 0
    pattern_count: int = 0
    angle_index: int = 0
    angle_count: int = 0
    bracket_label: str = ""
    bracket_index: int = 0
    exposure_us: int | None = None
    iso: int | None = None
    focus_diopters: float | None = None
    decode_dir: Path | None = None
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
        pattern_label: str = "",
        pattern_sequence_index: int = 0,
        pattern_count: int = 0,
        angle_index: int = 0,
        angle_count: int = 0,
        bracket_label: str = "",
        bracket_index: int = 0,
        exposure_us: int | None = None,
        iso: int | None = None,
        focus_diopters: float | None = None,
        decode_dir: Path | None = None,
    ) -> PendingCapture:
        future = asyncio.get_running_loop().create_future()
        pending = PendingCapture(
            scan_id=scan_id,
            pattern_id=pattern_id,
            capture_id=capture_id,
            angle_deg=angle_deg,
            attempt=attempt,
            result_future=future,
            pattern_label=pattern_label,
            pattern_sequence_index=pattern_sequence_index,
            pattern_count=pattern_count,
            angle_index=angle_index,
            angle_count=angle_count,
            bracket_label=bracket_label,
            bracket_index=bracket_index,
            exposure_us=exposure_us,
            iso=iso,
            focus_diopters=focus_diopters,
            decode_dir=decode_dir,
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
        pattern_sequence_index: int | None = Form(None),
        pattern_count: int | None = Form(None),
        angle_index: int | None = Form(None),
        angle_count: int | None = Form(None),
        bracket_label: str | None = Form(None),
        exposure_us: int | None = Form(None),
        iso: int | None = Form(None),
        focus_diopters: float | None = Form(None),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        scan_id = safe_scan_id(scan_id)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".dng"}:
            suffix = ".png"

        scan_dir = state.output_root / scan_id
        key = (scan_id, int(pattern_id), int(capture_id))
        pending = state.pending.get(key)

        if pending is not None and pending.decode_dir is not None:
            label = safe_filename_stem(pending.bracket_label or bracket_label or "frame")
            filename = f"{label}{suffix}"
            destination = (
                pending.decode_dir
                / "exposures"
                / f"pattern_{pattern_id:03d}"
                / filename
            )
        else:
            angle_text = "" if angle_deg is None else f"_angle_{angle_deg:03d}"
            filename = (
                f"{scan_id}{angle_text}_pattern_{pattern_id:03d}_"
                f"capture_{capture_id:03d}{suffix}"
            )
            destination = scan_dir / filename

        destination.parent.mkdir(parents=True, exist_ok=True)

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
            "pattern_sequence_index": (
                pending.pattern_sequence_index if pending else pattern_sequence_index
            ),
            "pattern_count": pending.pattern_count if pending else pattern_count,
            "angle_index": pending.angle_index if pending else angle_index,
            "angle_count": pending.angle_count if pending else angle_count,
            "filename": filename,
            "path": str(destination),
            "relative_path": rel_posix(destination, scan_dir),
            "size_bytes": size_bytes,
            "upload_timestamp_pc_ms": now_ms(),
            "pattern_label": pending.pattern_label if pending else None,
            "bracket_label": pending.bracket_label if pending else bracket_label,
            "bracket_index": pending.bracket_index if pending else None,
            "exposure_us": pending.exposure_us if pending else exposure_us,
            "iso": pending.iso if pending else iso,
            "focus_diopters": pending.focus_diopters if pending else focus_diopters,
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
    pattern: PatternSpec,
    pattern_sequence_index: int,
    pattern_count: int,
    bracket: ExposureBracket,
    bracket_index: int,
    capture_id: int,
    angle_deg: int,
    angle_index: int,
    angle_count: int,
    attempt: int,
    upload_url: str,
) -> dict[str, Any]:
    return {
        "type": "capture",
        "scan_id": scan_id,
        "pattern_id": pattern.pattern_id,
        "pattern_label": pattern.label,
        "pattern_sequence_index": pattern_sequence_index,
        "pattern_count": pattern_count,
        "capture_id": capture_id,
        "angle_deg": angle_deg,
        "angle_index": angle_index,
        "angle_count": angle_count,
        "attempt": attempt,
        "upload_url": upload_url,
        "bracket_label": bracket.label,
        "bracket": {
            "index": bracket_index,
            "label": bracket.label,
            "exposure_us": bracket.exposure_us,
            "iso": bracket.iso,
        },
        "settings": {
            "manual": args.manual,
            "manual_focus": args.manual_focus,
            "awb_locked": args.awb_locked,
            "exposure_us": bracket.exposure_us,
            "iso": bracket.iso,
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
        "angle_index",
        "angle_count",
        "pattern_id",
        "pattern_label",
        "pattern_sequence_index",
        "pattern_count",
        "bracket_label",
        "bracket_index",
        "capture_id",
        "attempt",
        "pattern_filename",
        "pattern_display_timestamp_pc_ms",
        "capture_command_timestamp_pc_ms",
        "upload_timestamp_pc_ms",
        "timestamp_phone_ms",
        "received_image_filename",
        "received_image_relative_path",
        "exposure_us",
        "iso",
        "focus_diopters",
        "status",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def decode_dir_for_angle(scan_dir: Path, angles: list[int], angle: int) -> Path:
    if len(angles) == 1:
        return scan_dir
    return scan_dir / f"angle_{angle:03d}"


def ordered_patterns(patterns: list[PatternSpec], args: argparse.Namespace) -> list[PatternSpec]:
    by_id = {pattern.pattern_id: pattern for pattern in patterns}
    if args.capture_order == "id" or args.pattern_mode == "legacy-14":
        order = [pattern.pattern_id for pattern in patterns]
    else:
        order = [pattern_id for pattern_id in INTERLEAVED_22_ORDER if pattern_id in by_id]
    return [by_id[pattern_id] for pattern_id in order]


def selected_counts(selected_index: np.ndarray, brackets: list[ExposureBracket]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for index, bracket in enumerate(brackets):
        counts[bracket.label] = int(np.count_nonzero(selected_index == index))
    return counts


def merge_hdr_pattern(
    *,
    decode_dir: Path,
    scan_dir: Path,
    pattern: PatternSpec,
    bracket_records: list[dict[str, Any]],
    hdr_settings: HdrSettings,
) -> dict[str, Any]:
    if not bracket_records:
        raise RuntimeError(f"No bracket frames for pattern {pattern.pattern_id:03d}")

    records = sorted(
        bracket_records,
        key=lambda record: (
            float(max(1, int(record["exposure_us"])) * max(1, int(record["iso"]))),
            int(record.get("bracket_index", 0)),
        ),
    )
    images = [read_gray_image(Path(record["path"])) for record in records]
    shape = images[0].shape
    if any(image.shape != shape for image in images):
        raise RuntimeError(f"HDR bracket image sizes do not match for {pattern.label}")

    exposure_products = np.array(
        [
            float(max(1, int(record["exposure_us"])) * max(1, int(record["iso"])))
            for record in records
        ],
        dtype=np.float64,
    )

    selected = images[0].astype(np.float64)
    selected_eff = np.full(shape, exposure_products[0], dtype=np.float64)
    selected_index = np.zeros(shape, dtype=np.uint8)
    for index, image in enumerate(images):
        not_saturated = image < hdr_settings.saturated_threshold
        selected[not_saturated] = image[not_saturated]
        selected_eff[not_saturated] = exposure_products[index]
        selected_index[not_saturated] = index

    stack = np.stack(images, axis=0)
    saturated_mask = np.all(stack >= hdr_settings.saturated_threshold, axis=0)
    dark_mask = np.all(stack <= hdr_settings.dark_threshold, axis=0)

    reference_eff = float(np.max(exposure_products))
    normalized = np.clip((selected / selected_eff) * reference_eff / 255.0, 0.0, 1.0)
    max_value = 65535 if hdr_settings.bit_depth == 16 else 255
    dtype = np.uint16 if hdr_settings.bit_depth == 16 else np.uint8
    final_image = np.rint(normalized * max_value).astype(dtype)

    final_path = decode_dir / f"pattern_{pattern.pattern_id:03d}.png"
    mask_dir = decode_dir / "hdr_masks"
    saturated_path = mask_dir / f"pattern_{pattern.pattern_id:03d}_saturated.png"
    dark_path = mask_dir / f"pattern_{pattern.pattern_id:03d}_dark.png"
    write_image(final_path, final_image)
    write_image(saturated_path, saturated_mask.astype(np.uint8) * 255)
    write_image(dark_path, dark_mask.astype(np.uint8) * 255)

    ordered_brackets = [
        ExposureBracket(
            label=str(record["bracket_label"]),
            exposure_us=int(record["exposure_us"]),
            iso=int(record["iso"]),
        )
        for record in records
    ]

    return {
        "pattern_id": pattern.pattern_id,
        "label": pattern.label,
        "filename": final_path.name,
        "bracket_filenames": [rel_posix(Path(record["path"]), decode_dir) for record in records],
        "exposure_us": [int(record["exposure_us"]) for record in records],
        "iso": [int(record["iso"]) for record in records],
        "focus_diopters": records[0].get("focus_diopters"),
        "merge": {
            "algorithm": "longest_unsaturated_radiance_normalized",
            "enabled": hdr_settings.enabled,
            "bit_depth": hdr_settings.bit_depth,
            "saturated_threshold": hdr_settings.saturated_threshold,
            "dark_threshold": hdr_settings.dark_threshold,
            "reference_exposure_product": reference_eff,
            "selected_pixel_counts": selected_counts(selected_index, ordered_brackets),
            "saturated_mask": rel_posix(saturated_path, decode_dir),
            "dark_mask": rel_posix(dark_path, decode_dir),
        },
        "captures": [
            {
                "bracket_label": record.get("bracket_label"),
                "bracket_index": record.get("bracket_index"),
                "filename": rel_posix(Path(record["path"]), decode_dir),
                "exposure_us": record.get("exposure_us"),
                "iso": record.get("iso"),
                "focus_diopters": record.get("focus_diopters"),
                "angle_index": record.get("angle_index"),
                "angle_count": record.get("angle_count"),
                "pattern_sequence_index": record.get("pattern_sequence_index"),
                "pattern_count": record.get("pattern_count"),
                "capture_id": record.get("capture_id"),
                "capture_command_timestamp_pc_ms": record.get(
                    "capture_command_timestamp_pc_ms"
                ),
                "upload_timestamp_pc_ms": record.get("upload_timestamp_pc_ms"),
                "timestamp_phone_ms": record.get("timestamp_phone_ms"),
            }
            for record in records
        ],
    }


def write_decode_logs(
    *,
    scan_dir: Path,
    decode_records: dict[str, list[dict[str, Any]]],
    base_log: dict[str, Any],
) -> None:
    for decode_dir_text, records in decode_records.items():
        decode_dir = Path(decode_dir_text)
        records_sorted = sorted(records, key=lambda item: int(item["pattern_id"]))
        log = dict(base_log)
        log["decode_dir"] = str(decode_dir)
        log["patterns"] = records_sorted
        (decode_dir / "scan_log.json").write_text(
            json.dumps(log, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (decode_dir / "hdr_merge_report.json").write_text(
            json.dumps(
                {
                    "scan_id": base_log["scan_id"],
                    "decode_dir": str(decode_dir),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "patterns": [
                        {
                            "pattern_id": record["pattern_id"],
                            "label": record["label"],
                            "filename": record["filename"],
                            "merge": record["merge"],
                        }
                        for record in records_sorted
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def validate_decode_folder(decode_dir: Path, expected_ids: tuple[int, ...]) -> None:
    missing = [
        pattern_id
        for pattern_id in expected_ids
        if not (decode_dir / f"pattern_{pattern_id:03d}.png").exists()
    ]
    if not missing:
        return
    missing_text = ", ".join(f"{pattern_id:03d}" for pattern_id in missing)
    raise RuntimeError(f"Decode folder {decode_dir} is missing pattern ids: {missing_text}")


def select_analysis_angles(args: argparse.Namespace, angles: list[int]) -> list[int]:
    if args.analysis_mode == "bidirectional":
        return list(angles)

    selected = angles[0]
    if args.single_analysis_angle is not None:
        if args.single_analysis_angle not in angles:
            raise SystemExit(
                f"--single-analysis-angle {args.single_analysis_angle} is not in --angles"
            )
        selected = args.single_analysis_angle
    return [selected]


def build_analysis_manifest(
    *,
    args: argparse.Namespace,
    scan_id: str,
    scan_dir: Path,
    angles: list[int],
    decode_records: dict[str, list[dict[str, Any]]],
    expected_ids: tuple[int, ...],
) -> dict[str, Any]:
    analysis_angles = select_analysis_angles(args, angles)
    targets: list[dict[str, Any]] = []
    for angle in analysis_angles:
        decode_dir = decode_dir_for_angle(scan_dir, angles, angle)
        records = decode_records.get(str(decode_dir), [])
        targets.append(
            {
                "angle_deg": angle,
                "decode_dir": str(decode_dir),
                "relative_decode_dir": rel_posix(decode_dir, scan_dir)
                if decode_dir != scan_dir
                else ".",
                "pattern_count": len(records),
                "expected_pattern_ids": list(expected_ids),
            }
        )

    return {
        "scan_id": scan_id,
        "analysis_mode": args.analysis_mode,
        "analysis_angles_deg": analysis_angles,
        "capture_angles_deg": angles,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "targets": targets,
    }


def make_synthetic_capture(
    *,
    pattern: PatternSpec,
    bracket: ExposureBracket,
    brackets: tuple[ExposureBracket, ...],
    destination: Path,
) -> None:
    base = read_gray_image(pattern.path).astype(np.float64)
    sorted_brackets = sorted(brackets, key=lambda item: item.exposure_product)
    reference = sorted_brackets[len(sorted_brackets) // 2].exposure_product
    simulated = np.clip(base * bracket.exposure_product / reference, 0.0, 255.0)
    write_image(destination, np.rint(simulated).astype(np.uint8))


async def run_scan(args: argparse.Namespace) -> int:
    pattern_dir = args.patterns.resolve()
    patterns = load_patterns(pattern_dir, args.pattern_mode)
    capture_patterns = ordered_patterns(patterns, args)
    first_image = read_image(capture_patterns[0].path)
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    scan_id = safe_scan_id(args.scan_id or datetime.now().strftime("scan_%Y%m%d_%H%M%S"))
    scan_dir = output_root / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    expected_ids = expected_pattern_ids(args.pattern_mode)
    angles = parse_csv_ints(args.angles, "angles")
    select_analysis_angles(args, angles)
    hdr_settings = build_hdr_settings(args)

    scan_rows: list[dict[str, Any]] = []
    decode_records: dict[str, list[dict[str, Any]]] = {}
    display: PatternDisplay | None = None
    capture_id = 0
    aborted = False
    validation_error = ""
    state: ControllerState | None = None
    server: uvicorn.Server | None = None
    server_task: asyncio.Task | None = None

    print(
        f"[scan] scan_id={scan_id} mode={args.pattern_mode} "
        f"patterns={len(capture_patterns)} raw_captures_per_angle="
        f"{len(capture_patterns) * len(hdr_settings.brackets)} "
        f"angles={angles} analysis={args.analysis_mode} dry_run={args.dry_run}"
    )
    print(
        "[scan] HDR brackets="
        + ", ".join(
            f"{bracket.label}:{bracket.exposure_us}us:ISO{bracket.iso}"
            for bracket in hdr_settings.brackets
        )
    )

    def add_decode_record(decode_dir: Path, record: dict[str, Any]) -> None:
        decode_records.setdefault(str(decode_dir), []).append(record)

    def make_base_row(
        *,
        angle: int,
        angle_index: int,
        pattern: PatternSpec,
        pattern_sequence_index: int,
        bracket: ExposureBracket,
        bracket_index: int,
        current_capture_id: int,
        attempt: int,
        display_ts: int,
        command_ts: int,
    ) -> dict[str, Any]:
        return {
            "scan_id": scan_id,
            "angle_deg": angle,
            "angle_index": angle_index,
            "angle_count": len(angles),
            "pattern_id": pattern.pattern_id,
            "pattern_label": pattern.label,
            "pattern_sequence_index": pattern_sequence_index,
            "pattern_count": len(capture_patterns),
            "bracket_label": bracket.label,
            "bracket_index": bracket_index,
            "capture_id": current_capture_id,
            "attempt": attempt,
            "pattern_filename": pattern.path.name,
            "pattern_display_timestamp_pc_ms": display_ts,
            "capture_command_timestamp_pc_ms": command_ts,
            "exposure_us": bracket.exposure_us,
            "iso": bracket.iso,
            "focus_diopters": args.focus_diopters,
        }

    def merge_completed_pattern(
        *,
        decode_dir: Path,
        pattern: PatternSpec,
        bracket_records: list[dict[str, Any]],
    ) -> None:
        record = merge_hdr_pattern(
            decode_dir=decode_dir,
            scan_dir=scan_dir,
            pattern=pattern,
            bracket_records=bracket_records,
            hdr_settings=hdr_settings,
        )
        add_decode_record(decode_dir, record)
        print(
            f"[hdr] merged pattern={pattern.pattern_id:03d} "
            f"label={pattern.label} -> {decode_dir / record['filename']}"
        )

    try:
        if args.dry_run:
            for angle_index, angle in enumerate(angles):
                decode_dir = decode_dir_for_angle(scan_dir, angles, angle)
                decode_dir.mkdir(parents=True, exist_ok=True)
                print(f"[dry-run] angle={angle:03d} decode_dir={decode_dir}")
                for pattern_sequence_index, pattern in enumerate(capture_patterns):
                    bracket_records: list[dict[str, Any]] = []
                    for bracket_index, bracket in enumerate(hdr_settings.brackets):
                        display_ts = now_ms()
                        command_ts = display_ts
                        destination = (
                            decode_dir
                            / "exposures"
                            / f"pattern_{pattern.pattern_id:03d}"
                            / f"{bracket.label}.png"
                        )
                        make_synthetic_capture(
                            pattern=pattern,
                            bracket=bracket,
                            brackets=hdr_settings.brackets,
                            destination=destination,
                        )
                        upload_ts = now_ms()
                        row = make_base_row(
                            angle=angle,
                            angle_index=angle_index,
                            pattern=pattern,
                            pattern_sequence_index=pattern_sequence_index,
                            bracket=bracket,
                            bracket_index=bracket_index,
                            current_capture_id=capture_id,
                            attempt=1,
                            display_ts=display_ts,
                            command_ts=command_ts,
                        )
                        row.update(
                            {
                                "upload_timestamp_pc_ms": upload_ts,
                                "timestamp_phone_ms": None,
                                "received_image_filename": destination.name,
                                "received_image_relative_path": rel_posix(
                                    destination, scan_dir
                                ),
                                "status": "ok",
                                "error": "",
                            }
                        )
                        scan_rows.append(row)
                        record = {
                            **row,
                            "filename": destination.name,
                            "path": str(destination),
                            "relative_path": rel_posix(destination, scan_dir),
                            "size_bytes": destination.stat().st_size,
                        }
                        bracket_records.append(record)
                        capture_id += 1

                    merge_completed_pattern(
                        decode_dir=decode_dir,
                        pattern=pattern,
                        bracket_records=bracket_records,
                    )
        else:
            state = ControllerState(output_root=output_root)
            app = create_app(state)
            config = uvicorn.Config(
                app, host=args.host, port=args.port, log_level=args.log_level
            )
            server = uvicorn.Server(config)
            server_task = asyncio.create_task(server.serve())

            upload_host = local_url_host(args)
            upload_url = f"http://{upload_host}:{args.port}/upload"
            ws_url = f"ws://{upload_host}:{args.port}/ws"
            print(f"[server] WebSocket URL for Android: {ws_url}")
            print(f"[server] Upload URL sent to Android: {upload_url}")

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
                decode_dir = decode_dir_for_angle(scan_dir, angles, angle)
                decode_dir.mkdir(parents=True, exist_ok=True)
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

                for pattern_sequence_index, pattern in enumerate(capture_patterns):
                    image = read_image(pattern.path)
                    bracket_records: list[dict[str, Any]] = []

                    for bracket_index, bracket in enumerate(hdr_settings.brackets):
                        success = False
                        last_error = ""

                        for attempt in range(1, args.retries + 2):
                            if display is not None:
                                display.show(image)
                            display_ts = now_ms()
                            settle_ms = (
                                args.settle_ms
                                if bracket_index == 0
                                else args.bracket_settle_ms
                            )
                            await asyncio.sleep(settle_ms / 1000.0)

                            pending = state.register_pending(
                                scan_id=scan_id,
                                pattern_id=pattern.pattern_id,
                                capture_id=capture_id,
                                angle_deg=angle,
                                attempt=attempt,
                                pattern_label=pattern.label,
                                pattern_sequence_index=pattern_sequence_index,
                                pattern_count=len(capture_patterns),
                                angle_index=angle_index,
                                angle_count=len(angles),
                                bracket_label=bracket.label,
                                bracket_index=bracket_index,
                                exposure_us=bracket.exposure_us,
                                iso=bracket.iso,
                                focus_diopters=args.focus_diopters,
                                decode_dir=decode_dir,
                            )
                            message = make_capture_message(
                                args,
                                scan_id=scan_id,
                                pattern=pattern,
                                pattern_sequence_index=pattern_sequence_index,
                                pattern_count=len(capture_patterns),
                                bracket=bracket,
                                bracket_index=bracket_index,
                                capture_id=capture_id,
                                angle_deg=angle,
                                angle_index=angle_index,
                                angle_count=len(angles),
                                attempt=attempt,
                                upload_url=upload_url,
                            )
                            command_ts = now_ms()
                            await state.send_json(message)
                            print(
                                f"[capture] angle={angle:03d} "
                                f"pattern={pattern.pattern_id:03d} "
                                f"bracket={bracket.label} capture={capture_id:03d} "
                                f"attempt={attempt}"
                            )

                            row = make_base_row(
                                angle=angle,
                                angle_index=angle_index,
                                pattern=pattern,
                                pattern_sequence_index=pattern_sequence_index,
                                bracket=bracket,
                                bracket_index=bracket_index,
                                current_capture_id=capture_id,
                                attempt=attempt,
                                display_ts=display_ts,
                                command_ts=command_ts,
                            )

                            try:
                                timeout_s = args.capture_timeout + args.upload_timeout
                                result = await asyncio.wait_for(
                                    pending.result_future, timeout_s
                                )
                                upload = result["upload"]
                                done = result["done"]
                                row.update(
                                    {
                                        "upload_timestamp_pc_ms": upload.get(
                                            "upload_timestamp_pc_ms"
                                        ),
                                        "timestamp_phone_ms": done.get(
                                            "timestamp_phone_ms"
                                        ),
                                        "received_image_filename": upload.get("filename"),
                                        "received_image_relative_path": upload.get(
                                            "relative_path"
                                        ),
                                        "status": "ok",
                                        "error": "",
                                    }
                                )
                                scan_rows.append(row)
                                record = {
                                    **row,
                                    **upload,
                                    "timestamp_phone_ms": done.get("timestamp_phone_ms"),
                                    "capture_command_timestamp_pc_ms": command_ts,
                                }
                                bracket_records.append(record)
                                success = True
                                state.finish_pending(pending.key)
                                capture_id += 1
                                break
                            except Exception as exc:
                                last_error = str(exc)
                                row.update(
                                    {
                                        "status": (
                                            "retry"
                                            if attempt <= args.retries
                                            else "failed"
                                        ),
                                        "error": last_error,
                                    }
                                )
                                scan_rows.append(row)
                                state.finish_pending(pending.key)
                                print(
                                    f"[capture] failed angle={angle:03d} "
                                    f"pattern={pattern.pattern_id:03d} "
                                    f"bracket={bracket.label} capture={capture_id:03d}: "
                                    f"{last_error}"
                                )
                                capture_id += 1
                                if attempt <= args.retries:
                                    await asyncio.sleep(args.retry_delay_ms / 1000.0)

                        if not success:
                            aborted = True
                            raise RuntimeError(
                                f"scan aborted at angle={angle} "
                                f"pattern={pattern.pattern_id} bracket={bracket.label}: "
                                f"{last_error}"
                            )

                    merge_completed_pattern(
                        decode_dir=decode_dir,
                        pattern=pattern,
                        bracket_records=bracket_records,
                    )

                previous_angle = angle

        if not args.server_only:
            for decode_dir_text in decode_records:
                validate_decode_folder(Path(decode_dir_text), expected_ids)

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

        if not aborted and not args.server_only:
            try:
                for decode_dir_text in decode_records:
                    validate_decode_folder(Path(decode_dir_text), expected_ids)
            except Exception as exc:
                aborted = True
                validation_error = str(exc)
                print(f"[scan] ERROR: {validation_error}")

        analysis_manifest = build_analysis_manifest(
            args=args,
            scan_id=scan_id,
            scan_dir=scan_dir,
            angles=angles,
            decode_records=decode_records,
            expected_ids=expected_ids,
        )

        log = {
            "scan_id": scan_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "aborted" if aborted else "ok",
            "validation_error": validation_error,
            "pattern_dir": str(pattern_dir),
            "pattern_mode": args.pattern_mode,
            "pattern_order": [
                {
                    "pattern_id": pattern.pattern_id,
                    "label": pattern.label,
                    "filename": pattern.path.name,
                }
                for pattern in capture_patterns
            ],
            "expected_pattern_ids": list(expected_ids),
            "decode_folders": sorted(decode_records),
            "angles_deg": angles,
            "analysis": analysis_manifest,
            "scan_type": args.scan_type,
            "metadata": {
                "scan_type": args.scan_type,
                "projector_tilt_deg": args.projector_tilt_deg,
                "manual_focus_confirmed": args.manual_focus_confirmed,
                "phone_mount_id": args.phone_mount_id,
                "rig_id": args.rig_id,
                "calibration_id": args.calibration_id,
                "projector_brightness": args.projector_brightness,
                "keystone_predistortion": False,
            },
            "hdr": {
                "enabled": hdr_settings.enabled,
                "bit_depth": hdr_settings.bit_depth,
                "saturated_threshold": hdr_settings.saturated_threshold,
                "dark_threshold": hdr_settings.dark_threshold,
                "brackets": [
                    {
                        "label": bracket.label,
                        "exposure_us": bracket.exposure_us,
                        "iso": bracket.iso,
                    }
                    for bracket in hdr_settings.brackets
                ],
            },
            "decoder_contract": [
                {
                    "pattern_id": pattern_id,
                    "label": PATTERN_LABELS.get(pattern_id, f"Pattern{pattern_id}"),
                    "filename": f"pattern_{pattern_id:03d}.png",
                }
                for pattern_id in expected_ids
            ],
            "settings": {
                "settle_ms": args.settle_ms,
                "bracket_settle_ms": args.bracket_settle_ms,
                "phone_settle_ms": args.phone_settle_ms,
                "manual": args.manual,
                "manual_focus": args.manual_focus,
                "awb_locked": args.awb_locked,
                "focus_diopters": args.focus_diopters,
                "capture_timeout": args.capture_timeout,
                "upload_timeout": args.upload_timeout,
                "retries": args.retries,
            },
            "rows": scan_rows,
        }

        if str(scan_dir) not in decode_records:
            (scan_dir / "scan_log.json").write_text(
                json.dumps(log, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        write_decode_logs(scan_dir=scan_dir, decode_records=decode_records, base_log=log)
        (scan_dir / "analysis_manifest.json").write_text(
            json.dumps(analysis_manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        append_csv(scan_dir / "scan_log.csv", scan_rows)
        print(f"[scan] log saved: {scan_dir / 'scan_log.json'}")
        print(f"[scan] csv saved: {scan_dir / 'scan_log.csv'}")

        if server is not None and server_task is not None:
            server.should_exit = True
            await server_task

    return 1 if aborted else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display structured-light patterns and trigger Android captures."
    )
    parser.add_argument("--patterns", default="generated_patterns", type=Path)
    parser.add_argument("--output", default="captures", type=Path)
    parser.add_argument(
        "--pattern-mode",
        default="22",
        choices=("22", "legacy-14"),
        help="Default 22-frame decoder contract, or old 14-frame capture.",
    )
    parser.add_argument(
        "--capture-order",
        default="interleaved",
        choices=("interleaved", "id"),
        help="Use Gray/Gray_inv interleaving for 22-frame capture or numeric id order.",
    )
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
    parser.add_argument("--bracket-settle-ms", default=80, type=int)
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
    parser.add_argument(
        "--manual-focus",
        default=True,
        type=parse_bool,
        help="Request fixed Camera2 focus distance on Android when PC settings are used.",
    )
    parser.add_argument(
        "--awb-locked",
        default=True,
        type=parse_bool,
        help="Request AWB off/locked on Android when supported.",
    )
    parser.add_argument("--focus-diopters", default=0.0, type=float)
    parser.add_argument(
        "--hdr-brackets",
        help=(
            "Comma-separated label:exposure_us[:iso] list. "
            "Enables HDR capture and overrides default short/mid/long brackets."
        ),
    )
    parser.add_argument(
        "--enable-hdr",
        action="store_true",
        help="Capture HDR short/mid/long brackets per pattern instead of one frame.",
    )
    parser.add_argument(
        "--bracket-config",
        type=Path,
        help="JSON config with brackets and optional HDR thresholds.",
    )
    parser.add_argument("--saturated-threshold", default=250, type=int)
    parser.add_argument("--dark-threshold", default=5, type=int)
    parser.add_argument("--hdr-bit-depth", default=8, type=int, choices=(8, 16))
    parser.add_argument(
        "--legacy-single-exposure",
        action="store_true",
        help="Compatibility alias for the default single-exposure 22-frame workflow.",
    )
    parser.add_argument(
        "--single-exposure",
        action="store_true",
        help="Force one exposure per pattern even when HDR options are present.",
    )
    parser.add_argument("--scan-type", default="object", choices=("object", "reference"))
    parser.add_argument("--projector-tilt-deg", default=30.0, type=float)
    parser.add_argument("--manual-focus-confirmed", default=False, type=parse_bool)
    parser.add_argument("--phone-mount-id", default="")
    parser.add_argument("--rig-id", default="")
    parser.add_argument("--calibration-id", default="")
    parser.add_argument("--projector-brightness", default="")
    parser.add_argument("--angles", default="0,180")
    parser.add_argument(
        "--analysis-mode",
        default="bidirectional",
        choices=("single", "bidirectional"),
        help="Choose whether downstream analysis uses one angle or all captured angles.",
    )
    parser.add_argument(
        "--single-analysis-angle",
        type=int,
        help="Angle to analyze when --analysis-mode single. Defaults to the first angle.",
    )
    parser.add_argument("--pause-before-first-angle", action="store_true")
    parser.add_argument("--no-angle-prompt", action="store_true")
    parser.add_argument("--angle-advance-file", type=Path)
    parser.add_argument("--rotation-command")
    parser.add_argument("--rotate-first-angle", action="store_true")
    parser.add_argument("--scan-id")
    parser.add_argument("--server-only", action="store_true")
    parser.add_argument("--no-display", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create synthetic bracket frames, HDR merges, and scan_log.json without Android.",
    )
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
