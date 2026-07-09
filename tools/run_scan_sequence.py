#!/usr/bin/env python3
"""Project a pattern sequence and capture camera frames with OpenCV."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path


def import_cv2():
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "OpenCV is required for capture/display. Install opencv-python in "
            "the Python environment you use for this script."
        ) from exc
    return cv2


def parse_csv_ints(value: str, label: str) -> list[int]:
    try:
        result = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be comma-separated integers") from exc
    if not result:
        raise argparse.ArgumentTypeError(f"{label} cannot be empty")
    return result


def camera_source(value: str):
    return int(value) if re.fullmatch(r"\d+", value) else value


def pattern_id(pattern: dict, fallback: int) -> int:
    value = pattern.get("pattern_id", pattern.get("index", fallback))
    return int(value)


def selected_analysis_angles(args: argparse.Namespace, angles: list[int]) -> list[int]:
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


def set_camera_exposure(cv2, cap, exposure_ms: int, mode: str) -> dict:
    if mode == "none":
        return {"requested_ms": exposure_ms, "mode": mode, "applied": False}

    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
    if mode == "opencv-ms":
        sent_value = exposure_ms / 1000.0
    elif mode == "opencv-raw":
        sent_value = float(exposure_ms)
    else:
        raise ValueError(f"unknown exposure mode: {mode}")

    applied = cap.set(cv2.CAP_PROP_EXPOSURE, sent_value)
    actual = cap.get(cv2.CAP_PROP_EXPOSURE)
    return {
        "requested_ms": exposure_ms,
        "mode": mode,
        "sent_value": sent_value,
        "reported_value": actual,
        "applied": bool(applied),
    }


def open_display_window(cv2, name: str, fullscreen: bool, x: int | None, y: int | None):
    cv2.namedWindow(name, cv2.WINDOW_NORMAL)
    if x is not None and y is not None:
        cv2.moveWindow(name, x, y)
    if fullscreen:
        cv2.setWindowProperty(name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)


def show_image(cv2, window_name: str, image, settle_ms: int) -> bool:
    cv2.imshow(window_name, image)
    key = cv2.waitKey(1) & 0xFF
    if key == 27:
        return False
    time.sleep(settle_ms / 1000.0)
    return True


def flush_camera(cap, flush_frames: int) -> None:
    for _ in range(max(0, flush_frames)):
        cap.read()
        time.sleep(0.005)


def capture_frame(cv2, cap, output_path: Path) -> bool:
    ok, frame = cap.read()
    if not ok:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(output_path), frame))


def run_sequence(args: argparse.Namespace) -> int:
    cv2 = import_cv2()
    sequence_path = args.sequence.resolve()
    sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
    pattern_dir = sequence_path.parent
    patterns = sequence["patterns"]

    session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S")
    session_dir = args.output.resolve() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    cap = None
    if not args.no_camera:
        cap = cv2.VideoCapture(camera_source(args.camera))
        if not cap.isOpened():
            raise SystemExit(f"Could not open camera source: {args.camera}")

    open_display_window(
        cv2,
        args.window_name,
        fullscreen=not args.windowed,
        x=args.window_x,
        y=args.window_y,
    )

    exposures = parse_csv_ints(args.exposures, "exposures")
    angles = parse_csv_ints(args.angles, "angles")
    analysis_angles = selected_analysis_angles(args, angles)
    metadata = {
        "session_id": session_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sequence": str(sequence_path),
        "camera": args.camera,
        "exposures_ms": exposures,
        "angles_deg": angles,
        "analysis_mode": args.analysis_mode,
        "analysis_angles_deg": analysis_angles,
        "pattern_count": len(patterns),
        "settle_ms": args.settle_ms,
        "flush_frames": args.flush_frames,
        "exposure_control": args.exposure_control,
        "captures": [],
    }

    try:
        for angle_index, angle in enumerate(angles):
            if angle_index > 0 or args.pause_before_first_angle:
                input(f"Set rotation disk to {angle} degrees, then press Enter...")

            for exposure_ms in exposures:
                exposure_meta = None
                if cap is not None:
                    exposure_meta = set_camera_exposure(
                        cv2, cap, exposure_ms, args.exposure_control
                    )
                    time.sleep(args.exposure_settle_ms / 1000.0)

                for sequence_index, pattern in enumerate(patterns):
                    current_pattern_id = pattern_id(pattern, sequence_index)
                    image_path = pattern_dir / pattern["filename"]
                    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                    if image is None:
                        raise SystemExit(f"Could not read pattern image: {image_path}")

                    if not show_image(cv2, args.window_name, image, args.settle_ms):
                        print("Stopped by ESC.")
                        return 130

                    capture_name = (
                        f"{current_pattern_id:02d}_{Path(pattern['filename']).stem}.png"
                    )
                    rel_capture = (
                        Path(f"angle_{angle:03d}")
                        / f"exposure_{exposure_ms:03d}ms"
                        / capture_name
                    )
                    capture_path = session_dir / rel_capture

                    saved = True
                    if cap is not None:
                        flush_camera(cap, args.flush_frames)
                        saved = capture_frame(cv2, cap, capture_path)
                        if not saved:
                            raise SystemExit(f"Failed to capture {capture_path}")

                    metadata["captures"].append(
                        {
                            "angle_deg": angle,
                            "angle_index": angle_index,
                            "angle_count": len(angles),
                            "exposure_ms": exposure_ms,
                            "pattern_sequence_index": sequence_index,
                            "pattern_count": len(patterns),
                            "pattern_id": current_pattern_id,
                            "pattern": pattern,
                            "capture": str(rel_capture) if cap is not None else None,
                            "timestamp": time.monotonic(),
                            "exposure_meta": exposure_meta,
                            "saved": saved,
                        }
                    )
                    print(
                        f"angle={angle:03d} exposure={exposure_ms:03d}ms "
                        f"pattern={sequence_index + 1:02d}/{len(patterns):02d} "
                        f"id={current_pattern_id:02d} saved={saved}"
                    )
    finally:
        (session_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        (session_dir / "analysis_manifest.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "analysis_mode": args.analysis_mode,
                    "analysis_angles_deg": analysis_angles,
                    "capture_angles_deg": angles,
                    "targets": [
                        {
                            "angle_deg": angle,
                            "relative_decode_dir": f"angle_{angle:03d}",
                            "pattern_count": len(patterns),
                        }
                        for angle in analysis_angles
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()

    print(f"Scan session saved at {session_dir}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display FPP patterns and capture one camera frame per pattern."
    )
    parser.add_argument("--sequence", default="generated_patterns/sequence.json", type=Path)
    parser.add_argument("--camera", default="0", help="Webcam index or camera URL.")
    parser.add_argument("--output", default="scans", type=Path)
    parser.add_argument("--exposures", default="10,30,80")
    parser.add_argument("--angles", default="0,180")
    parser.add_argument(
        "--analysis-mode",
        default="bidirectional",
        choices=("single", "bidirectional"),
    )
    parser.add_argument("--single-analysis-angle", type=int)
    parser.add_argument("--settle-ms", default=120, type=int)
    parser.add_argument("--exposure-settle-ms", default=250, type=int)
    parser.add_argument("--flush-frames", default=2, type=int)
    parser.add_argument(
        "--exposure-control",
        choices=("none", "opencv-ms", "opencv-raw"),
        default="none",
        help="How to send exposure values to OpenCV. Default only labels sets.",
    )
    parser.add_argument("--window-name", default="PRO4500 Scan")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--window-x", type=int)
    parser.add_argument("--window-y", type=int)
    parser.add_argument("--pause-before-first-angle", action="store_true")
    parser.add_argument(
        "--no-camera",
        action="store_true",
        help="Project patterns and write metadata without opening a camera.",
    )
    return parser.parse_args()


def main() -> int:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")
    return run_sequence(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
