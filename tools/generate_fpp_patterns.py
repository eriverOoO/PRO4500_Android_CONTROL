#!/usr/bin/env python3
"""Generate the decoder-contract structured-light pattern sequence.

This script creates only image files. It does not open a camera, show windows,
or send any hardware trigger/control commands.
"""

from __future__ import annotations

import argparse
import json
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

# Reflected Gray code naturally makes all non-MSB bit planes black at both
# image edges. For projector/camera calibration this looks like one black band
# has been split across the left and right boundaries, so make those edge-split
# planes white at the boundaries instead. This preserves Gray-code adjacency;
# decoders should XOR the recorded polarity mask before Gray-to-binary decode.
AVOID_SPLIT_EDGE_BLACK = True

# BMP is the default because TI/DLP pattern-loading workflows commonly prefer
# simple, uncompressed bitmap files. Change to "png" if a PNG-only workflow is
# needed later.
IMAGE_FORMAT = "bmp"

# Patterns are written to the project root, not inside tools/.
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "generated_patterns"

# Remove old generated PNG/BMP files before writing the new sequence.
CLEAN_EXISTING_IMAGES = True

# The new default decoder contract is 22 frames:
# White, Black, Gray0..Gray7, Sine_000..Sine_270, Gray0_inv..Gray7_inv.
INCLUDE_INVERTED_GRAY = True


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


def gray_bit_polarity_mask() -> int:
    """Return the Gray-code bit mask inverted in generated non-inverted frames."""
    if not AVOID_SPLIT_EDGE_BLACK:
        return 0

    # Keep the MSB unflipped: it already has one black half and one white half.
    return (1 << (GRAY_CODE_BITS - 1)) - 1


def vertical_gray_pattern(
    bit_index_from_msb: int,
    stripe_width_px: int,
    polarity_mask: int,
) -> np.ndarray:
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
    gray = gray_encode(code_cell) ^ polarity_mask

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


def sequence_record(index: int, label: str, filename: str) -> dict[str, object]:
    return {
        "pattern_id": index,
        "label": label,
        "filename": filename,
    }


def generate_patterns() -> list[Path]:
    stripe_width_px = validate_config()
    polarity_mask = gray_bit_polarity_mask()

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
    sequence: list[dict[str, object]] = [
        sequence_record(0, "White", f"00_White.{extension}"),
        sequence_record(1, "Black", f"01_Black.{extension}"),
    ]

    gray_images: list[np.ndarray] = []
    for gray_index in range(GRAY_CODE_BITS):
        image = vertical_gray_pattern(gray_index, stripe_width_px, polarity_mask)
        gray_images.append(image)
        filename = f"{gray_index + 2:02d}_Gray{gray_index}.{extension}"
        frames.append(
            (
                filename,
                image,
            )
        )
        sequence.append(sequence_record(gray_index + 2, f"Gray{gray_index}", filename))

    phase_shifts = (
        ("000", 0.0),
        ("090", math.pi / 2.0),
        ("180", math.pi),
        ("270", 3.0 * math.pi / 2.0),
    )
    for sine_index, (label, shift_rad) in enumerate(phase_shifts, start=10):
        filename = f"{sine_index:02d}_Sine_{label}.{extension}"
        frames.append(
            (
                filename,
                vertical_sine_pattern(shift_rad, stripe_width_px),
            )
        )
        sequence.append(sequence_record(sine_index, f"Sine_{label}", filename))

    if INCLUDE_INVERTED_GRAY:
        for gray_index, image in enumerate(gray_images):
            pattern_id = 14 + gray_index
            filename = f"{pattern_id:02d}_Gray{gray_index}_inv.{extension}"
            frames.append((filename, 255 - image))
            sequence.append(sequence_record(pattern_id, f"Gray{gray_index}_inv", filename))

    expected_count = 22 if INCLUDE_INVERTED_GRAY else 14
    if len(frames) != expected_count:
        raise RuntimeError(f"Expected {expected_count} frames, got {len(frames)}.")

    written: list[Path] = []
    for filename, image in frames:
        path = OUTPUT_DIR / filename
        write_image(path, image)
        written.append(path)

    (OUTPUT_DIR / "sequence.json").write_text(
        json.dumps(
            {
                "pattern_count": len(sequence),
                "patterns": sorted(sequence, key=lambda item: int(item["pattern_id"])),
                "gray_code_bits": GRAY_CODE_BITS,
                "projector_width": PROJECTOR_WIDTH,
                "projector_height": PROJECTOR_HEIGHT,
                "finest_gray_stripe_width_px": stripe_width_px,
                "sinusoidal_wavelength_px": stripe_width_px,
                "includes_inverted_gray": INCLUDE_INVERTED_GRAY,
                "avoid_split_edge_black": AVOID_SPLIT_EDGE_BLACK,
                "gray_code_polarity_mask": polarity_mask,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"Generated {len(written)} {IMAGE_FORMAT.upper()} patterns in: {OUTPUT_DIR}")
    print(f"Resolution: {PROJECTOR_WIDTH} x {PROJECTOR_HEIGHT}")
    print(f"Gray-code bits: {GRAY_CODE_BITS}")
    print(f"Finest Gray stripe width: {stripe_width_px} px")
    print(f"Sinusoidal wavelength: {stripe_width_px} px")
    print(f"Inverted Gray frames: {'yes' if INCLUDE_INVERTED_GRAY else 'no'}")
    print(f"Gray-code polarity mask: 0x{polarity_mask:0{max(1, (GRAY_CODE_BITS + 3) // 4)}X}")

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate PRO4500 Gray/PSP patterns for the decoder contract."
    )
    parser.add_argument("--output", default=OUTPUT_DIR, type=Path)
    parser.add_argument("--width", default=PROJECTOR_WIDTH, type=int)
    parser.add_argument("--height", default=PROJECTOR_HEIGHT, type=int)
    parser.add_argument("--gray-code-bits", default=GRAY_CODE_BITS, type=int)
    parser.add_argument("--format", default=IMAGE_FORMAT, choices=("bmp", "png"))
    parser.add_argument(
        "--legacy-14",
        action="store_true",
        help="Generate only White/Black, Gray0..Gray7, and 4 sine frames.",
    )
    parser.add_argument("--no-clean", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global PROJECTOR_WIDTH
    global PROJECTOR_HEIGHT
    global GRAY_CODE_BITS
    global IMAGE_FORMAT
    global OUTPUT_DIR
    global CLEAN_EXISTING_IMAGES
    global INCLUDE_INVERTED_GRAY

    PROJECTOR_WIDTH = args.width
    PROJECTOR_HEIGHT = args.height
    GRAY_CODE_BITS = args.gray_code_bits
    IMAGE_FORMAT = args.format
    OUTPUT_DIR = args.output
    CLEAN_EXISTING_IMAGES = not args.no_clean
    INCLUDE_INVERTED_GRAY = not args.legacy_14

    generate_patterns()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
