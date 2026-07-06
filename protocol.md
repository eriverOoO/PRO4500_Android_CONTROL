# 구조광 휴대폰 캡처 프로토콜

PC가 항상 마스터입니다. Android 앱은 스스로 스캔 순서를 다음 단계로 진행하지 않습니다.

## 엔드포인트

- WebSocket: `ws://<pc_ip>:8765/ws`
- 업로드: `http://<pc_ip>:8765/upload`
- 상태 확인: `GET http://<pc_ip>:8765/health`

## WebSocket: PC에서 Android로

### `ping`

```json
{
  "type": "ping"
}
```

Android는 `pong`으로 응답합니다.

### `capture`

```json
{
  "type": "capture",
  "scan_id": "scan_20260629_001",
  "pattern_id": 0,
  "pattern_label": "White",
  "capture_id": 0,
  "angle_deg": 0,
  "attempt": 1,
  "upload_url": "http://192.168.0.12:8765/upload",
  "bracket_label": "mid",
  "bracket": {
    "index": 1,
    "label": "mid",
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

필수 필드:

- `type`: 반드시 `capture`여야 합니다.
- `scan_id`: 하나의 스캔을 식별하는 고정 ID입니다.
- `pattern_id`: 패턴 폴더 안에서 0부터 시작하는 패턴 번호입니다.
- `capture_id`: 해당 스캔 안에서 전역으로 고유한 촬영 번호입니다.
- `upload_url`: PC의 HTTP 업로드 엔드포인트입니다.
- `settings`: PC가 요청하는 카메라 촬영 설정입니다.

선택 필드:

- `angle_deg`: 회전 각도 라벨입니다.
- `attempt`: 재시도 횟수입니다.
- `pattern_label`: `Gray0_inv`처럼 디코더와 약속한 패턴 라벨입니다.
- `bracket_label`과 `bracket`: HDR 노출 브라켓 메타데이터입니다.

## WebSocket: Android에서 PC로

### `pong`

```json
{
  "type": "pong",
  "timestamp_phone_ms": 1782348234234
}
```

### `capture_done`

Android는 HTTP 업로드가 성공한 뒤에만 이 메시지를 보냅니다.

```json
{
  "type": "capture_done",
  "scan_id": "scan_20260629_001",
  "pattern_id": 0,
  "capture_id": 0,
  "angle_deg": 0,
  "pattern_label": "White",
  "bracket_label": "mid",
  "filename": "scan_20260629_001_angle_000_pattern_000_capture_000.png",
  "timestamp_phone_ms": 1782348234234,
  "upload_status": "ok",
  "settings": {
    "manual": true,
    "manual_focus": true,
    "awb_locked": true,
    "exposure_us": 10000,
    "iso": 100,
    "focus_diopters": 0.0
  }
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

## HTTP 업로드

`POST /upload`

콘텐츠 타입: `multipart/form-data`

필드:

- `scan_id`: 문자열
- `pattern_id`: 정수
- `capture_id`: 정수
- `angle_deg`: 선택 정수값
- `bracket_label`: 선택 HDR 브라켓 라벨
- `exposure_us`: 선택 카메라 노출 메타데이터
- `iso`: 선택 카메라 감도 메타데이터
- `focus_diopters`: 선택 초점 메타데이터
- `file`: PNG 이미지 파일

HDR 워크플로에서 PC는 원본 브라켓 파일을 다음 구조로 저장합니다.

```text
exposures/pattern_000/short.png
exposures/pattern_000/mid.png
exposures/pattern_000/long.png
```

그리고 디코더 입력 이미지는 다음 파일로 기록합니다.

```text
pattern_000.png
```

기존 단일 업로드 대체 경로에서는 파일을 다음 형식으로 저장합니다.

```text
<scan_id>[_angle_000]_pattern_000_capture_000.png
```

## 동기화 규칙

각 패턴에서 PC는 다음 두 조건을 모두 기다립니다.

1. HTTP `/upload`가 이미지를 디스크에 저장했습니다.
2. 같은 `(scan_id, pattern_id, capture_id)`에 대한 WebSocket `capture_done`이 도착했습니다.

두 조건이 모두 만족된 뒤에만 PC는 다음 패턴을 표시할 수 있습니다.

`capture_error` 또는 타임아웃이 발생하면 PC는 같은 `pattern_id`를 새 `capture_id`로 다시 시도합니다. 설정된 재시도 한도를 넘으면 스캔을 중단합니다.

## 파일명 규칙

모든 이미지 파일명에는 다음 값이 포함되어야 합니다.

- `scan_id`
- `pattern_id`
- `capture_id`

PC가 `angle_deg`를 보낸 경우에는 파일명에도 이 값을 포함합니다.
