# PCB 3D 스캔 소프트웨어 계획

## 현재 시스템 요약

현재 스캔 파이프라인은 PC가 전체 촬영 흐름을 제어하는 동기식 구조입니다.

- `StructuredLightControlPanel.exe`는 사용자용 GUI입니다. 패턴 폴더, 저장 폴더, PC IP/포트, 모니터, 노출, ISO, 초점, 각도 옵션을 받아 `structured_light_pc_controller.py`를 실행합니다.
- `structured_light_pc_controller.py`는 PC 마스터입니다. 패턴을 프로젝터 화면에 표시하고, Android 앱에 WebSocket `capture` 명령을 보낸 뒤, HTTP 업로드와 `capture_done` 응답을 받은 다음 다음 패턴으로 진행합니다.
- `android/StructuredLightPhoneCamera`는 Android CameraX 기반 촬영 앱입니다. PC가 보낸 설정으로 수동 노출, ISO, 초점, AWB 잠금을 적용하고 PNG 이미지를 PC로 업로드합니다.
- LightCrafter 4500/DLPC350 USB 제어는 현재 Blue LED 전류 제어와 기본 연결 진단에 사용합니다. 패턴 투영 자체는 Windows 확장 디스플레이에 표시하는 이미지 기반 방식입니다.
- 결과는 `captures/<scan_id>/` 아래에 저장되며, `scan_log.json`, `scan_log.csv`, 각도별 `analysis_manifest.json`이 생성됩니다.

## 1단계: 패턴 생성 계약

기본 패턴 세트는 각 촬영 각도마다 22장입니다.

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

생성 스크립트는 `tools/generate_fpp_patterns.py`입니다.

```powershell
.\.venv-pc\Scripts\python.exe tools\generate_fpp_patterns.py
```

기본 해상도는 `1280 x 800`, 기본 형식은 BMP입니다. 다른 프로젝터 표시 해상도를 쓰는 경우 실제 Windows 디스플레이 해상도에 맞춰 다시 생성합니다.

```powershell
.\.venv-pc\Scripts\python.exe tools\generate_fpp_patterns.py --width 1280 --height 800 --gray-code-bits 8
```

현재 Gray-code 생성은 8비트 reflected Gray code를 사용합니다. `Gray1..Gray7`의 검정 영역이 좌우 화면 경계에서 반씩 갈라져 보이는 문제를 줄이기 위해, 비반전 Gray 프레임에는 `gray_code_polarity_mask`가 적용됩니다. 이 값은 `generated_patterns/sequence.json`에 기록되며, 향후 디코더는 Gray-to-binary 변환 전에 동일한 polarity mask를 XOR해야 합니다.

기존 14장 구성만 필요할 때는 호환 옵션을 사용합니다.

```powershell
.\.venv-pc\Scripts\python.exe tools\generate_fpp_patterns.py --legacy-14
```

## 2단계: 동기 촬영 루프

기본 촬영 루프는 다음 순서로 동작합니다.

```text
패턴 표시
settle_ms 대기
Android에 capture 명령 전송
Android PNG 촬영 및 HTTP 업로드
Android capture_done 수신
PC가 파일 저장 및 로그 기록
다음 패턴으로 진행
```

이 방식은 카메라 프레임을 계속 흘려보내며 나중에 맞추는 구조가 아니라, PC가 한 프레임씩 명령하고 완료 확인을 받은 뒤 진행하는 구조입니다. 따라서 패턴 ID, 캡처 ID, 각도, 브라켓 정보가 PC와 Android 로그에서 같은 컨텍스트로 유지됩니다.

기본 실행 예시는 다음과 같습니다.

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --patterns generated_patterns --output captures --monitor 1 --angles 0,180
```

주요 옵션:

- `--windowed`: 전체 화면 대신 창 모드로 패턴 표시를 확인합니다.
- `--stretch`: 표시 영역에 맞춰 패턴을 늘립니다. 기본값은 종횡비 유지입니다.
- `--settle-ms`: 패턴 표시 후 촬영 명령 전 대기 시간입니다.
- `--exposure-us`, `--iso`, `--focus-diopters`: Android에 전달할 수동 카메라 설정입니다.
- `--manual-focus-confirmed`, `--phone-mount-id`, `--rig-id`, `--calibration-id`: 촬영 조건 추적용 메타데이터입니다.
- `--dry-run --no-display`: 실제 Android 없이 저장 포맷과 로그 생성을 검증합니다.

## 3단계: HDR 브라켓 촬영

기본은 단일 노출입니다. HDR이 필요할 때는 패턴마다 여러 노출 브라켓을 촬영하고, PC에서 같은 pattern id의 브라켓 이미지를 하나의 디코더 프레임으로 병합합니다.

HDR 기본 브라켓을 쓰려면 다음 옵션을 사용합니다.

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --enable-hdr
```

브라켓을 직접 지정할 수도 있습니다.

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --hdr-brackets short:3000:100,mid:10000:100,long:30000:100
```

HDR 결과 구조는 다음과 같습니다.

```text
captures/<scan_id>/angle_000/
  exposures/
    pattern_000/
      short.png
      mid.png
      long.png
  pattern_000.png
  hdr_masks/
  scan_log.json
  hdr_merge_report.json
  scan_log.csv
```

디코더 입력용 최종 이미지는 항상 `pattern_XXX.png`로 저장됩니다. `exposures/` 원본과 `hdr_masks/` 진단 마스크는 기본적으로 병합 직후 제거됩니다. 재병합이나 노출 품질 점검이 필요할 때만 GUI의 `Keep exposure originals`, `Keep HDR masks`를 선택합니다.

현재 Android 경로는 CameraX `YUV_420_888`을 무손실 RGB PNG로 변환해 업로드합니다. PC는 GUI의 `Channel` 값(기본 `blue`)을 모든 Gray/PSP 프레임에 동일하게 적용하고 최종 이미지를 mono16 PNG로 기록합니다. 원본 보관을 선택하면 `raw/angle_XXX/<bracket>/pattern_XXX.png` 구조로 RGB가 남습니다. `channel_quality_report.json`에는 4-step 사인 프레임의 R/G/B 변조, 포화, 암부 비율과 다음 촬영 권장 채널이 기록됩니다.

mono16은 현재 8-bit YUV 소스를 선형적으로 담는 컨테이너이므로 센서 유효 비트가 증가하는 것은 아닙니다. RAW_SENSOR/DNG는 CameraX ImageCapture와 별도의 Camera2 캡처 세션이 필요한 후속 확장 항목입니다.

## 4단계: 0도/180도 촬영

기본 각도 목록은 `0,180`입니다. 각도마다 같은 22프레임 패턴 세트를 촬영합니다.

```text
captures/<scan_id>/angle_000/
  pattern_000.png
  ...
  pattern_021.png
captures/<scan_id>/angle_180/
  pattern_000.png
  ...
  pattern_021.png
```

각도 이동은 촬영 프로토콜과 분리되어 있습니다. 현재 가능한 방식은 다음과 같습니다.

- GUI에서 각도 사이에 멈춘 뒤 `Next Angle`로 진행합니다.
- `--no-angle-prompt`로 각도 대기 없이 연속 진행합니다.
- `--angle-advance-file`로 외부 신호 파일을 기다립니다.
- `--rotation-command "my_rotate_command --angle {angle}"`로 각도별 외부 회전 명령을 실행합니다.
- `--rotate-first-angle`을 함께 사용하면 첫 각도에서도 회전 명령을 실행합니다.

분석 대상은 촬영 각도와 별도로 지정할 수 있습니다.

- `--analysis-mode bidirectional`: 촬영한 모든 각도를 분석 대상으로 기록합니다.
- `--analysis-mode single`: 한 각도만 분석 대상으로 기록합니다.
- `--single-analysis-angle 180`: single 모드에서 특정 각도를 선택합니다.

## 5단계: 로그와 메타데이터

각 스캔은 다음 정보를 남깁니다.

- 패턴 ID, 라벨, 파일명, 표시 시각
- 캡처 ID, Android 업로드 파일명, 업로드 시각
- 각도 정보: `angle_deg`, `angle_index`, `angle_count`
- 노출/ISO/초점/브라켓 정보
- 스캔 종류: `object` 또는 `reference`
- 장비/조건 메타데이터: `rig_id`, `phone_mount_id`, `calibration_id`, `projector_tilt_deg`, `projector_brightness`
- HDR 설정과 병합 결과
- 분석 대상 각도 목록

현재 산출물은 디코딩 전 입력 데이터 정리까지를 목표로 합니다. Height map, point cloud, Gray/PSP 디코딩 파이프라인은 아직 별도 구현 대상입니다.

## 다음 작업

1. `sequence.json`의 `gray_code_polarity_mask`를 반영하는 Gray/PSP 디코더를 추가합니다.
2. `analysis_manifest.json`을 입력으로 받아 각도별 디코딩 결과를 같은 스캔 폴더에 저장합니다.
3. 기준 평면 또는 기준 보드 촬영을 이용해 높이 보정 단계를 정의합니다.
4. 필요하면 LightCrafter USB 제어 범위를 LED 밝기 제어에서 패턴 모드/트리거 제어까지 확장합니다.
