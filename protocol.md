# Structured Light Phone Capture Protocol

PC is the master. Android never advances the scan sequence by itself.

## Endpoints

- WebSocket: `ws://<pc_ip>:8765/ws`
- Upload: `http://<pc_ip>:8765/upload`
- Health: `GET http://<pc_ip>:8765/health`

## WebSocket: PC to Android

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
  "scan_id": "scan_20260629_001",
  "pattern_id": 0,
  "capture_id": 0,
  "angle_deg": 0,
  "attempt": 1,
  "upload_url": "http://192.168.0.12:8765/upload",
  "settings": {
    "manual": true,
    "exposure_us": 10000,
    "iso": 100,
    "focus_diopters": 0.0,
    "settle_ms_before_capture": 0
  }
}
```

Required fields:

- `type`: must be `capture`
- `scan_id`: stable ID for one scan
- `pattern_id`: zero-based pattern index within the pattern folder
- `capture_id`: globally unique capture number for this scan
- `upload_url`: PC HTTP upload endpoint
- `settings`: camera settings requested by PC

Optional fields:

- `angle_deg`: rotation angle label
- `attempt`: retry attempt number

## WebSocket: Android to PC

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
  "scan_id": "scan_20260629_001",
  "pattern_id": 0,
  "capture_id": 0,
  "angle_deg": 0,
  "filename": "scan_20260629_001_angle_000_pattern_000_capture_000.png",
  "timestamp_phone_ms": 1782348234234,
  "upload_status": "ok"
}
```

### `capture_error`

```json
{
  "type": "capture_error",
  "scan_id": "scan_20260629_001",
  "pattern_id": 0,
  "capture_id": 0,
  "angle_deg": 0,
  "error": "Camera capture failed: ..."
}
```

## HTTP Upload

`POST /upload`

Content type: `multipart/form-data`

Fields:

- `scan_id`: string
- `pattern_id`: integer
- `capture_id`: integer
- `angle_deg`: optional integer
- `file`: PNG image file

PC stores the file as:

```text
<scan_id>[_angle_000]_pattern_000_capture_000.png
```

## Synchronization Rule

For each pattern, the PC waits for both:

1. HTTP `/upload` has saved the image to disk.
2. WebSocket `capture_done` has arrived for the same `(scan_id, pattern_id, capture_id)`.

Only then may the PC display the next pattern.

If `capture_error` or timeout occurs, PC retries the same `pattern_id` with a new `capture_id`. After the configured retry limit, the scan aborts.

## Filename Rule

Every image filename must include:

- `scan_id`
- `pattern_id`
- `capture_id`

`angle_deg` is included when the PC sends it.
