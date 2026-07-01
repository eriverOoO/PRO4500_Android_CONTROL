#!/usr/bin/env python3
"""Project a 14-pattern sequence at one or more rotation angles.

Default flow:

    angle_000: project 00..13
    prompt: rotate disk to 180 degrees and press Enter
    angle_180: project 00..13

This is a projection-only tool. It does not open a camera. Future hardware
automation can be attached with --rotation-command and --project-command.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATTERN_DIR = PROJECT_ROOT / "generated_patterns"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "projection_runs"
IMAGE_SUFFIXES = {".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def import_cv2_numpy():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "OpenCV and NumPy are required for direct PC-screen projection. "
            "Install opencv-python and numpy in the Python environment used here."
        ) from exc
    return cv2, np


def parse_csv_ints(value: str, label: str) -> list[int]:
    try:
        result = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be comma-separated integers") from exc
    if not result:
        raise argparse.ArgumentTypeError(f"{label} cannot be empty")
    return result


def pattern_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"^(\d+)", path.name)
    index = int(match.group(1)) if match else 10_000
    return index, path.name.lower()


def load_patterns(pattern_dir: Path, expected_count: int) -> list[Path]:
    if not pattern_dir.exists():
        raise SystemExit(f"Pattern directory does not exist: {pattern_dir}")

    patterns = sorted(
        (
            path
            for path in pattern_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=pattern_sort_key,
    )

    if expected_count > 0 and len(patterns) != expected_count:
        raise SystemExit(
            f"Expected {expected_count} pattern images in {pattern_dir}, "
            f"but found {len(patterns)}."
        )

    if not patterns:
        raise SystemExit(f"No pattern images found in {pattern_dir}")

    return patterns


def read_image(cv2, np, path: Path):
    # cv2.imread can fail on non-ASCII Windows paths. Reading bytes and using
    # imdecode keeps this tool reliable inside the current Korean path workspace.
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Could not decode pattern image: {path}")
    return image


def preload_images(cv2, np, pattern_paths: list[Path]) -> list[tuple[Path, Any]]:
    return [(path, read_image(cv2, np, path)) for path in pattern_paths]


def open_projection_window(
    cv2,
    window_name: str,
    fullscreen: bool,
    window_x: int | None,
    window_y: int | None,
) -> None:
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    if window_x is not None and window_y is not None:
        cv2.moveWindow(window_name, window_x, window_y)
    if fullscreen:
        cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)


def wait_with_escape(cv2, duration_ms: int) -> bool:
    deadline = time.monotonic() + max(0, duration_ms) / 1000.0
    while time.monotonic() < deadline:
        key = cv2.waitKey(20) & 0xFF
        if key == 27:
            return False
    return True


def show_frame(cv2, window_name: str, image, duration_ms: int) -> bool:
    cv2.imshow(window_name, image)
    if not wait_with_escape(cv2, duration_ms):
        return False
    return True


def format_command(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format(**values)
    except KeyError as exc:
        raise SystemExit(f"Unknown command placeholder: {exc}") from exc


def run_hook(name: str, command_template: str | None, values: dict[str, Any]) -> dict | None:
    if not command_template:
        return None

    command = format_command(command_template, values)
    print(f"[{name}] {command}")
    started = time.monotonic()
    completed = subprocess.run(command, shell=True)
    elapsed_ms = round((time.monotonic() - started) * 1000.0)
    if completed.returncode != 0:
        raise SystemExit(f"{name} failed with exit code {completed.returncode}")

    return {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "elapsed_ms": elapsed_ms,
    }


def maybe_run_hook(
    name: str,
    command_template: str | None,
    values: dict[str, Any],
    dry_run: bool,
) -> dict | None:
    if not command_template:
        return None

    command = format_command(command_template, values)
    if dry_run:
        print(f"[dry-run][{name}] {command}")
        return {
            "name": name,
            "command": command,
            "dry_run": True,
        }

    return run_hook(name, command_template, values)


def command_values(
    *,
    angle: int,
    angle_index: int,
    previous_angle: int | None,
    pattern_dir: Path,
    pattern_count: int,
    run_dir: Path,
) -> dict[str, Any]:
    pattern_dir_text = str(pattern_dir)
    run_dir_text = str(run_dir)
    return {
        "angle": angle,
        "angle_index": angle_index,
        "previous_angle": "" if previous_angle is None else previous_angle,
        "pattern_dir": pattern_dir_text,
        "pattern_dir_q": subprocess.list2cmdline([pattern_dir_text]),
        "pattern_count": pattern_count,
        "run_dir": run_dir_text,
        "run_dir_q": subprocess.list2cmdline([run_dir_text]),
    }


def prompt_for_angle(angle: int, first_angle: bool, pause_before_first_angle: bool) -> None:
    if first_angle and not pause_before_first_angle:
        return
    input(f"Rotate disk to {angle} degrees, then press Enter to project...")


def project_with_opencv(
    cv2,
    *,
    window_name: str,
    images: list[tuple[Path, Any]],
    angle: int,
    pattern_ms: int,
    dark_ms: int,
    blank_between_patterns: bool,
    log_patterns: list[dict],
) -> bool:
    blank = None
    if images:
        _, first_image = images[0]
        blank = first_image.copy()
        blank[:] = 0

    for pattern_index, (path, image) in enumerate(images):
        print(f"angle={angle:03d} pattern={pattern_index:02d} file={path.name}")
        shown_at = time.monotonic()
        if not show_frame(cv2, window_name, image, pattern_ms):
            return False

        log_patterns.append(
            {
                "angle_deg": angle,
                "pattern_index": pattern_index,
                "filename": path.name,
                "shown_at_monotonic": shown_at,
                "pattern_ms": pattern_ms,
            }
        )

        if blank_between_patterns and dark_ms > 0 and blank is not None:
            if not show_frame(cv2, window_name, blank, dark_ms):
                return False

    return True


def run_projection(args: argparse.Namespace) -> int:
    angles = parse_csv_ints(args.angles, "angles")
    pattern_dir = args.pattern_dir.resolve()
    pattern_paths = load_patterns(pattern_dir, args.expected_count)

    run_id = datetime.now().strftime("projection_%Y%m%d_%H%M%S")
    run_dir = args.output.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    use_opencv_projection = args.project_command is None and not args.dry_run
    cv2 = None
    images: list[tuple[Path, Any]] = []

    if use_opencv_projection:
        cv2, np = import_cv2_numpy()
        images = preload_images(cv2, np, pattern_paths)
        open_projection_window(
            cv2,
            args.window_name,
            fullscreen=not args.windowed,
            window_x=args.window_x,
            window_y=args.window_y,
        )

    log: dict[str, Any] = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pattern_dir": str(pattern_dir),
        "pattern_count": len(pattern_paths),
        "patterns": [path.name for path in pattern_paths],
        "angles_deg": angles,
        "pattern_ms": args.pattern_ms,
        "dark_ms": args.dark_ms,
        "project_command": args.project_command,
        "rotation_command": args.rotation_command,
        "events": [],
        "projected_patterns": [],
    }

    try:
        previous_angle: int | None = None
        for angle_index, angle in enumerate(angles):
            values = command_values(
                angle=angle,
                angle_index=angle_index,
                previous_angle=previous_angle,
                pattern_dir=pattern_dir,
                pattern_count=len(pattern_paths),
                run_dir=run_dir,
            )

            first_angle = angle_index == 0
            if args.rotation_command and (not first_angle or args.rotate_first_angle):
                event = maybe_run_hook("rotation", args.rotation_command, values, args.dry_run)
                if event:
                    event["angle_deg"] = angle
                    log["events"].append(event)
            elif not args.dry_run:
                prompt_for_angle(angle, first_angle, args.pause_before_first_angle)

            event = maybe_run_hook(
                "before-angle", args.before_angle_command, values, args.dry_run
            )
            if event:
                event["angle_deg"] = angle
                log["events"].append(event)

            if args.dry_run:
                print(f"[dry-run] angle={angle:03d}")
                for pattern_index, path in enumerate(pattern_paths):
                    print(f"[dry-run]   pattern={pattern_index:02d} file={path.name}")
                event = maybe_run_hook("project", args.project_command, values, args.dry_run)
                if event:
                    event["angle_deg"] = angle
                    log["events"].append(event)
            elif args.project_command:
                event = maybe_run_hook("project", args.project_command, values, args.dry_run)
                if event:
                    event["angle_deg"] = angle
                    log["events"].append(event)
            else:
                assert cv2 is not None
                ok = project_with_opencv(
                    cv2,
                    window_name=args.window_name,
                    images=images,
                    angle=angle,
                    pattern_ms=args.pattern_ms,
                    dark_ms=args.dark_ms,
                    blank_between_patterns=not args.no_blank_between_patterns,
                    log_patterns=log["projected_patterns"],
                )
                if not ok:
                    print("Stopped by ESC.")
                    return 130

            event = maybe_run_hook("after-angle", args.after_angle_command, values, args.dry_run)
            if event:
                event["angle_deg"] = angle
                log["events"].append(event)

            previous_angle = angle

    finally:
        if cv2 is not None:
            if args.finish_black_ms > 0 and images:
                _, first_image = images[0]
                blank = first_image.copy()
                blank[:] = 0
                cv2.imshow(args.window_name, blank)
                wait_with_escape(cv2, args.finish_black_ms)
            cv2.destroyAllWindows()

        (run_dir / "projection_log.json").write_text(
            json.dumps(log, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(f"Projection run log: {run_dir / 'projection_log.json'}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project generated structured-light patterns at 0/180 degrees."
    )
    parser.add_argument("--pattern-dir", default=DEFAULT_PATTERN_DIR, type=Path)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, type=Path)
    parser.add_argument("--angles", default="0,180", help="Comma-separated angle list.")
    parser.add_argument("--expected-count", default=14, type=int)
    parser.add_argument("--pattern-ms", default=700, type=int)
    parser.add_argument("--dark-ms", default=0, type=int)
    parser.add_argument("--finish-black-ms", default=300, type=int)
    parser.add_argument("--window-name", default="PRO4500 Projection")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--window-x", type=int)
    parser.add_argument("--window-y", type=int)
    parser.add_argument("--pause-before-first-angle", action="store_true")
    parser.add_argument("--no-blank-between-patterns", action="store_true")
    parser.add_argument(
        "--rotation-command",
        help=(
            "Optional command to move the rotation disk before an angle. "
            "Placeholders: {angle}, {previous_angle}, {angle_index}, "
            "{pattern_dir}, {pattern_dir_q}, {pattern_count}, {run_dir}, {run_dir_q}."
        ),
    )
    parser.add_argument(
        "--rotate-first-angle",
        action="store_true",
        help="Run --rotation-command for the first angle too.",
    )
    parser.add_argument(
        "--project-command",
        help=(
            "Optional external projector command. If set, this replaces OpenCV "
            "screen projection for each angle and uses the same placeholders."
        ),
    )
    parser.add_argument("--before-angle-command")
    parser.add_argument("--after-angle-command")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate/list the angle and pattern order without opening a window.",
    )
    return parser.parse_args()


def main() -> int:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")
    return run_projection(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
