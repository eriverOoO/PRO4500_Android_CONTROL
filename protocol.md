# PC-Android capture protocol

The PC is always the scan master. Android only captures when the PC sends a
`capture` command, uploads the image to the PC, then confirms completion over
WebSocket.

## Endpoints

- WebSocket: `ws://<pc_ip>:8765/ws`
- Upload: `http://<pc_ip>:8765/upload`
- Health check: `GET http://<pc_ip>:8765/health`

## Pattern contract

The default scan uses 22 projected patterns per angle:

```text
00 White
01 Black
02 Gray0
03 Gray1
04 Gray2
05 Gray3
06 Gray4
07 Gray5
08 Gray6
09 Gray7
10 Sine_000
11 Sine_090
12 Sine_180
13 Sine_270
14 Gray0_inv
15 Gray1_inv
16 Gray2_inv
17 Gray3_inv
18 Gray4_inv
19 Gray5_inv
20 Gray6_inv
21 Gray7_inv
```

The default angle list is `0,180`, so a normal single-exposure scan captures
and saves 22 decoder frames for `angle_000` and 22 decoder frames for
`angle_180`. HDR is optional; when enabled, raw bracket frames are temporarily
stored under `exposures/` and merged back to the same 22 decoder frames per angle.
They are retained only with `--retain-raw-exposures`; HDR masks likewise require
`--retain-hdr-masks`.

## PC to Android

### `ping`

```json
{
  "type": "ping"
}
```

Android replies with `pong`.

### `capture`

```json
{
  "type": "capture",
  "scan_id": "scan_20260709_120000",
  "pattern_id": 14,
  "pattern_label": "Gray0_inv",
  "pattern_sequence_index": 3,
  "pattern_count": 22,
  "capture_id": 25,
  "angle_deg": 180,
  "angle_index": 1,
  "angle_count": 2,
  "attempt": 1,
  "upload_url": "http://192.168.0.12:8765/upload",
  "bracket_label": "single",
  "bracket": {
    "index": 0,
    "label": "single",
    "exposure_us": 10000,
    "iso": 100
  },
  "settings": {
    "manual": true,
    "manual_focus": true,
    "awb_locked": true,
    "exposure_us": 10000,
    "iso": 100,
    "focus_diopters": 0.0,
    "settle_ms_before_capture": 0
  }
}
```

Required fields are `type`, `scan_id`, `pattern_id`, `capture_id`, and
`upload_url`. Progress fields such as `pattern_sequence_index`,
`pattern_count`, `angle_index`, and `angle_count` are included so the Android
app can log and save files with the same scan context as the PC.

## Android to PC

### `pong`

```json
{
  "type": "pong",
  "timestamp_phone_ms": 1782348234234
}
```

### `capture_done`

Android sends this only after the HTTP upload succeeds.

```json
{
  "type": "capture_done",
  "scan_id": "scan_20260709_120000",
  "pattern_id": 14,
  "pattern_label": "Gray0_inv",
  "pattern_sequence_index": 3,
  "pattern_count": 22,
  "capture_id": 25,
  "angle_deg": 180,
  "angle_index": 1,
  "angle_count": 2,
  "bracket_label": "single",
  "filename": "pattern_014_Gray0_inv_single_capture_025.png",
  "timestamp_phone_ms": 1782348234234,
  "upload_status": "ok"
}
```

### `capture_error`

```json
{
  "type": "capture_error",
  "scan_id": "scan_20260709_120000",
  "pattern_id": 14,
  "capture_id": 25,
  "angle_deg": 180,
  "error": "Camera capture failed: ..."
}
```

## Upload

`POST /upload` uses `multipart/form-data`.

Fields:

- `scan_id`
- `pattern_id`
- `capture_id`
- `angle_deg`
- `pattern_sequence_index`
- `pattern_count`
- `angle_index`
- `angle_count`
- `bracket_label`
- `exposure_us`
- `iso`
- `focus_diopters`
- `file`

During the synchronized PC workflow, uploads are saved into the decoder folder:

```text
captures/<scan_id>/angle_000/exposures/pattern_000/single.png  # retained only when requested
captures/<scan_id>/angle_000/pattern_000.png
captures/<scan_id>/angle_180/exposures/pattern_000/single.png  # retained only when requested
captures/<scan_id>/angle_180/pattern_000.png
```

For a single-angle scan, the `angle_000` directory is omitted and files are
written directly under `captures/<scan_id>/`.

## Analysis selection

The PC writes `analysis_manifest.json` after every scan.

- `--analysis-mode single`: downstream analysis uses one angle only. Use
  `--single-analysis-angle 180` to choose a specific captured angle; otherwise
  the first angle is used.
- `--analysis-mode bidirectional`: downstream analysis uses every captured
  angle, normally both `0` and `180`.

Angle movement is intentionally separate from this protocol. For now, the PC
can pause between angles, wait for the GUI `Next Angle` signal, or call an
external `--rotation-command`.
