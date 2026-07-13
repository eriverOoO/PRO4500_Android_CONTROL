# PRO4500 구조광 휴대폰 캡처 시스템

Windows PC에서 PRO4500/LightCrafter 4500 계열 프로젝터 패턴을 표시하고,
Android 휴대폰 카메라로 각 패턴 이미지를 촬영해 PC로 업로드하는 구조광 캡처 시스템입니다.

현재 기준의 주 실행 경로는 `StructuredLightControlPanel.exe` GUI입니다.
GUI는 Python PC 컨트롤러를 실행하고, Android 앱 연결 URL을 보여주며, 멀티 각도 촬영의 `Next Angle` 신호와 DLPC350 Blue LED 제어를 제공합니다.

## 출처 및 공통 모듈

이 작업 공간의 초기 PRO4500/LightCrafter 4500 제어 뼈대는
[lee-lab-skku/PRO4500_Control_System](https://github.com/lee-lab-skku/PRO4500_Control_System)을
바탕으로 확장되었습니다.

두 작업 공간에서 공통으로 사용하는 LightCrafter 4500/DLPC350 라이트엔진
제어 코드는 [eriverOoO/PRO4500_CONTROL](https://github.com/eriverOoO/PRO4500_CONTROL)
저장소로 분리했고, 이 저장소에서는 `GUI/` 경로의 Git submodule로 참조합니다.
Android 휴대폰 카메라 연동과 스캔 워크플로 코드는 이 작업 공간에 남겨 둡니다.

## 직접 조작하는 프로그램

촬영 중 사용자가 직접 다루는 프로그램은 PC 쪽 제어 프로그램과 Android 촬영 앱입니다.
일반적인 스캔에서는 아래 두 프로그램만 실행하고 조작하면 됩니다.

- `StructuredLightControlPanel.exe`
  - Windows용 PC 마스터 컨트롤러 GUI입니다.
  - 사용자는 이 화면에서 패턴 폴더, 출력 폴더, PC IP/포트, 모니터 번호, 각도, 노출/ISO/초점 값을 설정합니다.
  - `Start Scan`으로 스캔을 시작하고, 여러 각도를 촬영할 때는 `Next Angle` 버튼으로 다음 각도 진행 신호를 보냅니다.
  - GUI 내부에서는 `structured_light_pc_controller.py`를 백그라운드로 실행해 실제 패턴 표시와 휴대폰 촬영 동기화를 처리합니다.
  - LightCrafter 4500/DLPC350 USB 연결이 가능하면 Blue LED 밝기를 제어합니다.
  - `.toolchains`에 ADB가 준비되어 있으면 APK를 USB로 설치할 수 있습니다.

- `android/StructuredLightPhoneCamera`
  - Android CameraX 기반 휴대폰 촬영 앱입니다.
  - 사용자는 GUI가 보여주는 PC WebSocket URL을 입력하고 `Connect`를 누릅니다.
  - PC에서 `capture` 명령을 받으면 PNG 파일로 촬영 이미지를 저장하고, PC의 `/upload` 엔드포인트로 업로드한 뒤 `capture_done`을 보냅니다.
  - `Use PC camera settings`를 켠 경우에만 PC에서 보낸 노출/ISO/초점 값이 촬영에 반영됩니다.

## 내부 실행 및 보조 파일

아래 파일들은 시스템 동작에 필요하지만, 일반 촬영 과정에서 사용자가 직접 조작하는 대상은 아닙니다.
동작을 바꾸거나 개발/점검할 때만 직접 실행하거나 수정합니다.

- `structured_light_pc_controller.py`
  - GUI가 백그라운드로 실행하는 PC 마스터 컨트롤러 엔진입니다.
  - FastAPI WebSocket/HTTP 서버를 열고 Android 앱을 기다립니다.
  - OpenCV 창으로 패턴을 표시합니다.
  - 각 패턴마다 Android 앱에 `capture` 명령을 보내고, HTTP 업로드와 WebSocket `capture_done`을 모두 받은 뒤 다음 패턴으로 넘어갑니다.
  - 결과 이미지는 `captures/<scan_id>/`에 저장하고, `scan_log.json`, `scan_log.csv`를 남깁니다.
  - GUI 없이 실험하거나 자동화할 때는 명령줄에서 직접 실행할 수 있습니다.

- `tools/generate_fpp_patterns.py`
  - 현재 기본 FPP 패턴 22장을 `generated_patterns/`에 생성합니다.
  - 구성: White 1장, Black 1장, 8-bit Gray-code 8장, 4-step PSP sine 4장, 반전 Gray-code 8장.
  - 기본 해상도는 `1280 x 800`, 기본 포맷은 BMP입니다.

- `PRO4500.exe`
  - 기존 네이티브 패턴 표시/LED 제어 유틸리티입니다.
  - 휴대폰 촬영 동기화까지 포함한 현재 워크플로에서는 `StructuredLightControlPanel.exe`를 사용합니다.

## 필요 장비

- Windows PC
- PRO4500 또는 HDMI 확장 디스플레이로 인식되는 프로젝터
- Galaxy S23 또는 CameraX가 동작하는 Android 휴대폰
- PC와 휴대폰이 같은 Wi-Fi/LAN에 연결된 네트워크
- Windows 방화벽에서 TCP `8765` 인바운드 허용
- 선택 사항: LightCrafter 4500/DLPC350 USB 연결, ADB USB 디버깅

## 빠른 시작

### 1. PC Python 환경 준비

처음 한 번 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\prepare_pc_python_env.ps1
```

이 스크립트는 로컬 `.toolchains/python312`와 `.venv-pc`를 준비하고 `requirements.txt`를 설치합니다.

### 2. FPP 패턴 생성

```powershell
.\.venv-pc\Scripts\python.exe tools\generate_fpp_patterns.py
```

생성 위치:

```text
generated_patterns/
  00_White.bmp
  01_Black.bmp
  02_Gray0.bmp
  ...
  13_Sine_270.bmp
  14_Gray0_inv.bmp
  ...
  21_Gray7_inv.bmp
  sequence.json
```

프로젝터가 다른 해상도로 노출되는 경우:

```powershell
.\.venv-pc\Scripts\python.exe tools\generate_fpp_patterns.py --width 1280 --height 800 --gray-code-bits 8
```

기존 14장 패턴만 필요할 때는 `--legacy-14`를 사용합니다.

### 3. Android 앱 준비

Android Studio에서 다음 프로젝트를 열어 빌드할 수 있습니다.

```text
android/StructuredLightPhoneCamera
```

로컬 빌드 도구를 자동으로 준비하고 APK까지 만들려면:

```powershell
powershell -ExecutionPolicy Bypass -File .\prepare_android_build_toolchain.ps1
```

이미 도구가 준비되어 있으면 APK만 다시 빌드합니다.

```powershell
.\build_phone_apk.bat
```

빌드 결과:

```text
dist\StructuredLightPhoneCamera-debug.apk
```

ADB가 준비되어 있고 휴대폰 USB 디버깅이 켜져 있으면:

```powershell
.\install_phone_apk_adb.bat
```

GUI의 `Install APK by USB` 버튼도 같은 APK를 설치합니다.

### 4. GUI 실행

```powershell
.\StructuredLightControlPanel.exe
```

또는:

```powershell
.\run_control_panel.bat
```

주요 입력값:

- `Patterns`: `generated_patterns` 폴더
- `Output`: `captures` 폴더
- `Host`: 보통 `0.0.0.0`
- `Public IP`: 휴대폰에서 접근 가능한 PC LAN IP
- `Port`: 기본 `8765`
- `Monitor`: 프로젝터가 연결된 확장 디스플레이 번호. 보통 주 모니터가 `0`, 프로젝터가 `1`
- `Angles`: 예: `0` 또는 `0,180`
- `Settle ms`: 패턴 표시 후 촬영 명령 전 대기 시간
- `Exposure us`, `ISO`, `Focus`: Android 앱에서 `Use PC camera settings`를 켰을 때 사용할 촬영 설정

GUI 상단의 `Phone URL` 값을 Android 앱의 `PC WebSocket URL`에 입력하고 `Connect`를 누릅니다.
연결 후 GUI에서 `Start Scan`을 누르면 스캔이 시작됩니다.

## Android 앱 사용

1. 앱 실행 후 카메라 권한을 허용합니다.
2. `PC WebSocket URL`에 GUI가 보여주는 URL을 입력합니다.
   - 예: `ws://192.168.0.12:8765/ws`
3. `Connect`를 누르고 상태가 `connected`인지 확인합니다.
4. 휴대폰 자체 설정을 사용할 경우 앱에서 노출/ISO/초점 값을 입력하고 `Apply`를 누릅니다.
5. PC 설정을 촬영마다 반영하려면 `Use PC camera settings`를 켭니다.
6. 초점 고정이 필요하면 `AF Once`, `Lock AF`, 화면 터치 초점, 또는 `Manual focus distance`를 사용합니다.

현재 Android 앱은 PNG 촬영 이미지 저장과 업로드를 구현합니다. RAW/DNG 저장과 포인트클라우드 계산은 아직 포함되어 있지 않습니다.

## 고급: Python PC 컨트롤러 직접 실행

일반 촬영에서는 GUI를 사용합니다.
GUI 없이 점검, 실험, 자동화를 할 때만 `structured_light_pc_controller.py`를 직접 실행합니다.

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py `
  --patterns generated_patterns `
  --output captures `
  --monitor 1 `
  --settle-ms 300 `
  --exposure-us 10000 `
  --iso 100 `
  --manual true
```

테스트용 작은 패턴을 만들고 창 모드로 실행하려면:

```powershell
.\.venv-pc\Scripts\python.exe tools\generate_dummy_patterns.py --output example_patterns --count 4 --width 1280 --height 800
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --patterns example_patterns --output captures --windowed --monitor 0
```

멀티 각도 촬영 예:

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --patterns generated_patterns --output captures --monitor 1 --angles 0,180
```

CLI에서는 다음 각도로 이동할 때 콘솔에서 Enter를 누릅니다.
GUI에서는 같은 상황에서 `Next Angle` 버튼이 활성화됩니다.

외부 회전 스테이지 명령을 붙일 수도 있습니다.

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --angles 0,180 --rotation-command "my_rotate_command --angle {angle}"
```

사용 가능한 주요 옵션은 다음 명령으로 확인합니다.

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --help
```

## 네트워크 프로토콜

PC가 항상 마스터입니다. Android 앱은 스스로 다음 패턴으로 진행하지 않습니다.

- WebSocket: `ws://<pc_ip>:8765/ws`
- Upload: `http://<pc_ip>:8765/upload`
- Health: `GET http://<pc_ip>:8765/health`

각 패턴에서 PC는 다음 두 조건을 모두 만족해야 다음 패턴으로 넘어갑니다.

1. Android가 HTTP `/upload`로 이미지를 업로드했습니다.
2. Android가 같은 `scan_id`, `pattern_id`, `capture_id`에 대해 WebSocket `capture_done`을 보냈습니다.

자세한 메시지 형식은 `protocol.md`를 참고하세요.

## 출력 구조

기본 출력 폴더는 `captures/`입니다.

```text
captures/
  scan_YYYYMMDD_HHMMSS/
    pattern_000.png
    pattern_001.png
    ...
    pattern_021.png
    hdr_masks/                  # optional: Keep HDR masks
      pattern_000_saturated.png
      pattern_000_dark.png
    channel_quality_report.json
    scan_log.json
    hdr_merge_report.json
    scan_log.csv
  raw/                          # optional: Keep exposure originals
    angle_000/
      short/
        pattern_000.png         # lossless RGB converted from YUV_420_888
      mid/
      long/
```

The Android camera uploads lossless RGB PNG frames converted from CameraX
`YUV_420_888`. The PC uses one fixed measurement channel (`blue` by default)
for every Gray-code and phase frame and writes decoder-ready `pattern_###.png`
files as mono16. The source is currently 8-bit YUV, so mono16 is a linear
processing container and does not create additional sensor precision.
`channel_quality_report.json` compares R/G/B 4-step modulation, saturation,
and dark ratios for selecting the channel on later scans.

RGB source and mono16 decoder images use lossless PNG DEFLATE level 3. This
changes only storage size and transfer time: pixel values are not quantized,
resampled, or converted to JPEG. Android uploads from the file as a stream and
the PC writes the multipart payload in 1 MiB chunks.

`scan_log.json`에는 pattern id, label, 최종 파일명, 브라켓 파일명, exposure_us, ISO, focus, scan_type, projector_tilt_deg, keystone_predistortion 정보가 기록됩니다. 실제 휴대폰 없이 저장 포맷을 검증하려면:

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --patterns generated_patterns --output captures --dry-run --no-display
```

## 보조 도구

- `tools/project_angle_sequence.py`
  - 카메라 없이 패턴 표시 순서와 0/180도 같은 각도 전환을 테스트합니다.
  - 결과 로그는 `projection_runs/`에 저장됩니다.

- `tools/run_scan_sequence.py`
  - OpenCV 카메라 또는 IP 카메라 URL을 직접 열어 촬영하는 초기 실험용 스크립트입니다.
  - 현재 휴대폰 앱 동기화 워크플로의 주 경로는 아닙니다.

- `build_native_control_panel.bat`
  - MinGW-w64로 `StructuredLightControlPanel.exe`를 다시 빌드합니다.

- `build.bat`
  - 기존 `PRO4500.exe`를 다시 빌드합니다.

## 문제 해결

- 휴대폰이 연결되지 않음
  - PC와 휴대폰이 같은 네트워크에 있는지 확인합니다.
  - GUI의 `Public IP`가 휴대폰에서 접근 가능한 IP인지 확인합니다.
  - Windows 방화벽에서 TCP `8765`를 허용합니다.
  - 휴대폰 브라우저에서 `http://<PC_IP>:8765/health`가 열리는지 확인합니다.

- 업로드 실패
  - Android 앱 로그의 `upload_url`과 PC 콘솔/GUI 로그의 `/upload` 수신 로그를 확인합니다.
  - PC IP가 VPN, 가상 어댑터, 다른 LAN IP로 잘못 잡힌 경우 `Public IP`를 수동으로 입력합니다.

- 프로젝터가 아닌 다른 화면에 패턴이 표시됨
  - Windows 디스플레이 설정에서 프로젝터를 확장 디스플레이로 설정합니다.
  - GUI의 `Monitor` 값을 바꿔 봅니다.
  - 확인용으로 `Windowed projection`을 켜고 위치를 확인합니다.

- PC에서 입력한 노출/ISO가 적용되지 않음
  - Android 앱에서 `Use PC camera settings`가 켜져 있는지 확인합니다.
  - 휴대폰 카메라가 Camera2 수동 노출을 지원하지 않으면 앱 로그에 unsupported 메시지가 표시되고 자동 모드로 촬영됩니다.

- 패턴 번호와 사진 번호가 어긋남
  - 휴대폰 동기화 촬영에는 `StructuredLightControlPanel.exe` 또는 `structured_light_pc_controller.py`를 사용합니다.
  - 기존 `PRO4500.exe`를 단독으로 실행하면 Android 앱과 패턴 진행 ACK를 맞출 수 없습니다.

- Blue LED 제어 실패
  - LightCrafter 4500/DLPC350 USB가 연결되어 있는지 확인합니다.
  - 다른 프로그램이 장치를 점유하고 있지 않은지 확인합니다.
  - 장치를 열 수 없으면 GUI 로그에 `LightCrafter 4500 not found or cannot be opened`가 표시됩니다.

## 현재 한계

- 휴대폰 앱은 PNG 촬영 이미지 저장/업로드를 지원합니다.
- Height map, point cloud, Gray/PSP 디코딩 파이프라인은 아직 포함되어 있지 않습니다.
- 프로젝터와 카메라는 소프트웨어 핸드셰이크로 동기화됩니다. 하드웨어 트리거 동기화는 구현되어 있지 않습니다.
- 기본 FPP 패턴 생성 설정은 코드 상수로 고정되어 있습니다.
- 회전 스테이지는 수동 `Next Angle` 또는 외부 `--rotation-command`로 연결합니다.
