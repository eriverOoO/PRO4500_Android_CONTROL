#!/usr/bin/env python3
"""Generate a 14-frame structured-light pattern sequence.

This script creates only image files. It does not open a camera, show windows,
or send any hardware trigger/control commands.
"""

from __future__ import annotations

import math
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Easy-to-change scanner/projector configuration.
# ---------------------------------------------------------------------------

PROJECTOR_WIDTH = 1280
PROJECTOR_HEIGHT = 800

GRAY_CODE_BITS = 8

# BMP is the default because TI/DLP pattern-loading workflows commonly prefer
# simple, uncompressed bitmap files. Change to "png" if a PNG-only workflow is
# needed later.
IMAGE_FORMAT = "bmp"

# Patterns are written to the project root, not inside tools/.
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "generated_patterns"

# Remove old generated PNG/BMP files before writing the new 14-frame sequence.
CLEAN_EXISTING_IMAGES = True


def gray_encode(values: np.ndarray) -> np.ndarray:
    """Return reflected binary Gray code: g = n XOR (n >> 1)."""
    return values ^ (values >> 1)


def validate_config() -> int:
    if PROJECTOR_WIDTH <= 0 or PROJECTOR_HEIGHT <= 0:
        raise ValueError("PROJECTOR_WIDTH and PROJECTOR_HEIGHT must be positive.")

    if GRAY_CODE_BITS <= 0:
        raise ValueError("GRAY_CODE_BITS must be positive.")

    if IMAGE_FORMAT not in {"bmp", "png"}:
        raise ValueError('IMAGE_FORMAT must be either "bmp" or "png".')

    gray_code_count = 1 << GRAY_CODE_BITS
    if PROJECTOR_WIDTH % gray_code_count != 0:
        raise ValueError(
            "PROJECTOR_WIDTH must be divisible by 2**GRAY_CODE_BITS so the finest "
            "Gray-code stripe width is an exact integer number of projector pixels."
        )

    stripe_width_px = PROJECTOR_WIDTH // gray_code_count
    if stripe_width_px <= 0:
        raise ValueError("The computed Gray-code stripe width must be at least 1 px.")

    return stripe_width_px


def vertical_gray_pattern(bit_index_from_msb: int, stripe_width_px: int) -> np.ndarray:
    """Create one vertical-stripe Gray-code bit plane.

    The 8-bit Gray code divides projector X into 2**8 absolute-position cells.
    For 1280 px width, each cell is:

        1280 / 256 = 5 px

    The final Gray frame is the least-significant Gray bit and therefore the
    highest-frequency position code. Its finest stripe/cell width is exactly
    stripe_width_px. The sinusoidal PSP wavelength below intentionally uses this
    same value, so one wrapped sine period corresponds to one finest Gray-code
    cell. Keeping these values synchronized prevents off-by-one fringe-order
    errors during phase unwrapping.
    """
    bit = GRAY_CODE_BITS - 1 - bit_index_from_msb

    x = np.arange(PROJECTOR_WIDTH, dtype=np.uint16)
    code_cell = x // stripe_width_px
    gray = gray_encode(code_cell)

    row = (((gray >> bit) & 1) * 255).astype(np.uint8)
    return np.repeat(row[np.newaxis, :], PROJECTOR_HEIGHT, axis=0)


def vertical_sine_pattern(phase_shift_rad: float, wavelength_px: int) -> np.ndarray:
    """Create one vertical sinusoidal PSP pattern.

    Intensity model:

        I(x) = 127.5 + 127.5 * cos(2*pi*x / wavelength_px + phase_shift)

    wavelength_px is exactly the finest Gray-code stripe/cell width computed in
    validate_config(). For the default 1280 px / 8-bit setup, wavelength_px = 5.
    """
    x = np.arange(PROJECTOR_WIDTH, dtype=np.float64)
    phase = (2.0 * math.pi * x / float(wavelength_px)) + phase_shift_rad
    row = np.rint(127.5 + 127.5 * np.cos(phase))
    row = np.clip(row, 0, 255).astype(np.uint8)
    return np.repeat(row[np.newaxis, :], PROJECTOR_HEIGHT, axis=0)


def write_image(path: Path, image: np.ndarray) -> None:
    if image.dtype != np.uint8 or image.ndim != 2:
        raise ValueError(f"{path.name} must be a single-channel uint8 image.")

    ok = cv2.imwrite(str(path), image)
    if ok:
        return

    # Some Windows OpenCV builds fail to write directly to non-ASCII paths.
    # The project path can contain Korean characters, so fall back to an ASCII
    # temp path while still using cv2.imwrite for image encoding.
    temp_dir = Path(tempfile.gettempdir()) / "pro4500_cv2_pattern_write"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{path.stem}_{uuid.uuid4().hex}{path.suffix}"

    ok = cv2.imwrite(str(temp_path), image)
    if not ok:
        raise OSError(f"cv2.imwrite failed for both {path} and {temp_path}")

    temp_path.replace(path)


def generate_patterns() -> list[Path]:
    stripe_width_px = validate_config()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if CLEAN_EXISTING_IMAGES:
        for old_image in OUTPUT_DIR.iterdir():
            if old_image.is_file() and old_image.suffix.lower() in {".bmp", ".png"}:
                old_image.unlink()

    extension = IMAGE_FORMAT.lower()

    frames: list[tuple[str, np.ndarray]] = [
        (
            f"00_White.{extension}",
            np.full((PROJECTOR_HEIGHT, PROJECTOR_WIDTH), 255, dtype=np.uint8),
        ),
        (
            f"01_Black.{extension}",
            np.zeros((PROJECTOR_HEIGHT, PROJECTOR_WIDTH), dtype=np.uint8),
        ),
    ]

    for gray_index in range(GRAY_CODE_BITS):
        frames.append(
            (
                f"{gray_index + 2:02d}_Gray{gray_index}.{extension}",
                vertical_gray_pattern(gray_index, stripe_width_px),
            )
        )

    phase_shifts = (
        ("000", 0.0),
        ("090", math.pi / 2.0),
        ("180", math.pi),
        ("270", 3.0 * math.pi / 2.0),
    )
    for sine_index, (label, shift_rad) in enumerate(phase_shifts, start=10):
        frames.append(
            (
                f"{sine_index:02d}_Sine_{label}.{extension}",
                vertical_sine_pattern(shift_rad, stripe_width_px),
            )
        )

    if len(frames) != 14:
        raise RuntimeError(f"Expected 14 frames, got {len(frames)}.")

    written: list[Path] = []
    for filename, image in frames:
        path = OUTPUT_DIR / filename
        write_image(path, image)
        written.append(path)

    print(f"Generated {len(written)} {IMAGE_FORMAT.upper()} patterns in: {OUTPUT_DIR}")
    print(f"Resolution: {PROJECTOR_WIDTH} x {PROJECTOR_HEIGHT}")
    print(f"Gray-code bits: {GRAY_CODE_BITS}")
    print(f"Finest Gray stripe width: {stripe_width_px} px")
    print(f"Sinusoidal wavelength: {stripe_width_px} px")

    return written


def main() -> int:
    generate_patterns()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
