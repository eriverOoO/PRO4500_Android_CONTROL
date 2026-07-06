#!/usr/bin/env python3
"""Generate numbered dummy projection patterns for phone-link testing."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise OSError(f"failed to write {path}")


def generate(output: Path, count: int, width: int, height: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        image = np.zeros((height, width, 3), dtype=np.uint8)
        if index % 4 == 0:
            image[:, :] = (255, 255, 255)
        elif index % 4 == 1:
            stripe = max(8, width // 32)
            for x in range(0, width, stripe * 2):
                image[:, x : x + stripe] = (255, 255, 255)
        elif index % 4 == 2:
            stripe = max(8, height // 32)
            for y in range(0, height, stripe * 2):
                image[y : y + stripe, :] = (255, 255, 255)
        else:
            gradient = np.linspace(0, 255, width, dtype=np.uint8)
            image[:, :, 0] = gradient[np.newaxis, :]
            image[:, :, 1] = np.roll(gradient, width // 3)[np.newaxis, :]
            image[:, :, 2] = np.roll(gradient, 2 * width // 3)[np.newaxis, :]

        cv2.putText(
            image,
            f"pattern_{index:03d}",
            (max(20, width // 20), max(60, height // 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(1.0, width / 900.0),
            (0, 0, 255),
            3,
            cv2.LINE_AA,
        )
        write_image(output / f"pattern_{index:03d}.png", image)

    print(f"Generated {count} dummy patterns in {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create numbered dummy test patterns.")
    parser.add_argument("--output", default="example_patterns", type=Path)
    parser.add_argument("--count", default=22, type=int)
    parser.add_argument("--width", default=1280, type=int)
    parser.add_argument("--height", default=800, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generate(args.output, args.count, args.width, args.height)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
