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

## 처음 사용하는 사람을 위한 전체 사용 순서

이 절은 이 프로그램을 처음 받은 제3자가 실제 촬영까지 진행할 때 따라가는 절차입니다.
개발자가 아니라 측정 작업자라면 아래 순서만 먼저 확인하면 됩니다.

### 1. 장비 연결 확인

1. 프로젝터를 Windows PC에 HDMI로 연결합니다.
2. Windows 디스플레이 설정에서 프로젝터가 `확장 디스플레이`로 잡혀 있는지 확인합니다.
3. Android 휴대폰과 PC를 같은 Wi-Fi 또는 같은 LAN에 연결합니다.
4. LightCrafter 4500/DLPC350 USB 제어를 사용할 경우 프로젝터 USB도 PC에 연결합니다.
5. 휴대폰에 APK를 USB로 설치할 예정이면 Android 개발자 옵션에서 USB 디버깅을 켭니다.

프로젝터 화면이 Windows의 복제 화면으로 잡혀 있으면 패턴 표시 위치가 꼬일 수 있습니다.
반드시 확장 디스플레이로 설정한 뒤 GUI의 `Monitor` 번호를 맞춥니다.

### 2. PC 프로그램 준비

처음 받은 PC에서는 한 번만 Python 실행 환경을 준비합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\prepare_pc_python_env.ps1
```

그 다음 촬영에 사용할 구조광 패턴을 생성합니다.

```powershell
.\.venv-pc\Scripts\python.exe tools\generate_fpp_patterns.py
```

생성된 패턴은 기본적으로 `generated_patterns/` 폴더에 저장됩니다.
프로젝터 해상도가 `1280 x 800`이 아니라면 실제 프로젝터 해상도에 맞춰 `--width`, `--height` 값을 지정해 다시 생성합니다.

### 3. 휴대폰 앱 설치

이미 `dist\StructuredLightPhoneCamera-debug.apk` 파일이 있으면 그 APK를 휴대폰에 설치하면 됩니다.
APK가 없거나 새로 빌드해야 한다면 다음 순서로 준비합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\prepare_android_build_toolchain.ps1
.\build_phone_apk.bat
```

USB 디버깅이 가능한 휴대폰을 PC에 연결한 경우에는 다음 명령으로 설치할 수 있습니다.

```powershell
.\install_phone_apk_adb.bat
```

또는 GUI를 실행한 뒤 `Install APK by USB` 버튼을 눌러 같은 APK를 설치할 수 있습니다.

### 4. GUI 실행 및 기본 설정

PC에서 다음 파일을 실행합니다.

```powershell
.\StructuredLightControlPanel.exe
```

GUI에서 아래 값을 확인합니다.

- `Patterns`: 패턴 폴더입니다. 보통 `generated_patterns`를 사용합니다.
- `Output`: 결과 저장 폴더입니다. 보통 `captures`를 사용합니다.
- `Host`: 외부 접속을 받기 위해 보통 `0.0.0.0`을 사용합니다.
- `Public IP`: 휴대폰이 접속할 PC의 LAN IP입니다.
- `Port`: 기본값은 `8765`입니다.
- `Monitor`: 패턴을 표시할 프로젝터 화면 번호입니다.
- `Angles`: 한 각도만 찍으면 `0`, 앞뒤 방향을 찍으면 `0,180`처럼 입력합니다.
- `Settle ms`: 패턴을 띄운 뒤 촬영 명령을 보내기 전 대기 시간입니다. 흔들림이나 노출 안정이 필요하면 값을 늘립니다.
- `Exposure us`: 노출 시간입니다. 단위는 마이크로초입니다.
- `ISO`: 카메라 감도입니다.
- `Focus (D)`: 수동 초점을 사용할 때의 디옵터 값입니다. `0.0`은 무한대 초점입니다.

`Public IP`가 자동으로 잘못 잡히는 경우가 있습니다.
이때는 Windows에서 `ipconfig`로 Wi-Fi 또는 이더넷 IPv4 주소를 확인한 뒤 직접 입력합니다.

### 5. 휴대폰 앱 연결

1. 휴대폰에서 `StructuredLightPhoneCamera` 앱을 실행합니다.
2. 처음 실행할 때 카메라 권한을 허용합니다.
3. PC GUI 상단의 `Phone URL` 값을 휴대폰 앱의 `PC WebSocket URL`에 그대로 입력합니다.
   - 예: `ws://192.168.0.12:8765/ws`
4. 휴대폰 앱에서 `Connect`를 누릅니다.
5. 앱 상태가 `connected`로 바뀌는지 확인합니다.

연결이 되지 않으면 휴대폰 브라우저에서 `http://<PC_IP>:8765/health`를 열어 봅니다.
열리지 않으면 PC와 휴대폰이 같은 네트워크에 있는지, Windows 방화벽에서 TCP `8765`가 허용되어 있는지 확인합니다.

### 6. 초점과 노출 맞추기

촬영 전에 휴대폰 화면에서 대상 PCB가 선명하게 보이도록 위치와 초점을 맞춥니다.

- PC에서 노출과 ISO를 제어하려면 휴대폰 앱의 `Use PC camera settings`를 켭니다.
- 자동 초점으로 맞춘 뒤 고정하려면 앱에서 `Auto focus`를 켰다가 초점이 맞은 뒤 끕니다.
- `AF Once`, 화면 터치 초점, `Lock AF`를 사용해도 초점이 맞은 위치에서 고정할 수 있습니다.
- 정확한 수동 초점값을 알고 있으면 `Manual focus distance`를 켜고 `Focus` 값을 입력한 뒤 `Apply`를 누릅니다.

스캔 도중 초점이 계속 움직이면 Gray-code/PSP 디코딩 품질이 떨어질 수 있습니다.
최종 촬영 전에는 초점을 고정한 상태로 두는 것을 권장합니다.

### 7. 스캔 촬영 시작

1. 프로젝터가 대상 PCB를 비추고 있는지 확인합니다.
2. 휴대폰 앱이 `connected` 상태인지 확인합니다.
3. GUI에서 `Start Scan`을 누릅니다.
4. PC는 패턴을 하나씩 표시하고, 휴대폰은 각 패턴을 촬영해 PC로 업로드합니다.
5. 촬영 중에는 프로젝터, 휴대폰, PCB가 움직이지 않게 유지합니다.

`Angles`에 `0,180`처럼 여러 각도를 입력한 경우 첫 각도 촬영이 끝나면 GUI가 대기합니다.
이때 회전판이나 대상 물체를 다음 각도로 돌린 뒤 흔들림이 멈추면 `Next Angle`을 누릅니다.

### 8. 촬영 결과 확인

촬영이 끝나면 결과는 `captures/scan_YYYYMMDD_HHMMSS/` 아래에 저장됩니다.
먼저 다음 파일을 확인합니다.

- `scan_log.json`: 패턴 번호, 촬영 설정, 저장 파일명 같은 상세 기록입니다.
- `scan_log.csv`: 스프레드시트에서 확인하기 쉬운 촬영 로그입니다.
- `pattern_000.png`, `pattern_001.png` 등: 디코더 입력용 최종 패턴 이미지입니다.
- `raw/`: 원본 노출 이미지를 유지하도록 설정한 경우 저장되는 원본 이미지 폴더입니다.
- `channel_quality_report.json`: R/G/B 채널 품질 비교 결과입니다.

결과 이미지에서 패턴이 흐리거나 과노출이면 초점, 노출, ISO, 프로젝터 밝기, 편광 조건을 조정한 뒤 다시 촬영합니다.

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
- `Exposure us`, `ISO`: Android 앱에서 `Use PC camera settings`를 켰을 때 사용할 수동 노출 설정
- `Focus (D)`: `Manual focus`를 켠 경우에만 적용되는 디옵터 단위 수동 초점거리 (`0.0`은 무한대)

GUI 상단의 `Phone URL` 값을 Android 앱의 `PC WebSocket URL`에 입력하고 `Connect`를 누릅니다.
연결 후 GUI에서 `Start Scan`을 누르면 스캔이 시작됩니다.

## Android 앱 사용

1. 앱 실행 후 카메라 권한을 허용합니다.
2. `PC WebSocket URL`에 GUI가 보여주는 URL을 입력합니다.
   - 예: `ws://192.168.0.12:8765/ws`
3. `Connect`를 누르고 상태가 `connected`인지 확인합니다.
4. 휴대폰 자체 설정을 사용할 경우 앱에서 노출/ISO/초점 값을 입력하고 `Apply`를 누릅니다.
5. PC 설정을 촬영마다 반영하려면 `Use PC camera settings`를 켭니다.
6. 앱을 켜면 중앙 AF를 한 번 자동 수행하고 곧바로 OFF 고정 상태가 됩니다. 이것이 앱에서 자동으로 수행하는 유일한 초점 조작입니다.
7. 이후 `Auto focus` ON/OFF는 전부 사용자가 직접 조작합니다. ON에서는 연속 AF가 동작하고, OFF로 바꾸면 그 순간의 렌즈 위치가 고정됩니다. AF가 ON인 상태에서는 촬영을 시작하지 않고 앱 로그에 OFF 고정 안내를 표시합니다.
8. 180도 회전 전후에도 앱은 AF를 자동으로 켜거나 끄지 않습니다. 재초점이 필요할 때만 사용자가 ON으로 맞춘 뒤 OFF로 고정하고 `Next Angle`로 촬영을 계속합니다.
9. 화면 터치, `AF Once`, `Lock AF`도 초점을 맞춘 뒤 OFF 고정하며, 다시 자동으로 맞추려면 `Auto focus`를 ON으로 바꾸거나 `Resume AF`를 누릅니다.
10. 정확한 디옵터 값을 직접 지정하려면 `Manual focus distance`를 켜고 `Focus` 값을 입력한 뒤 `Apply`를 누릅니다. `0.0 D`는 무한대이며 가까운 피사체에는 흐릴 수 있습니다.

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
    hdr_masks/                  # 선택 사항: HDR 마스크 유지
      pattern_000_saturated.png
      pattern_000_dark.png
    channel_quality_report.json
    scan_log.json
    hdr_merge_report.json
    scan_log.csv
  raw/                          # 선택 사항: 원본 노출 이미지 유지
    angle_000/
      short/
        pattern_000.png         # YUV_420_888에서 변환한 무손실 RGB
      mid/
      long/
```

Android 카메라는 CameraX `YUV_420_888`에서 변환한 무손실 RGB PNG 프레임을 업로드합니다.
PC는 모든 Gray-code 및 phase 프레임에 하나의 고정 측정 채널을 사용합니다. 기본 채널은 `blue`입니다.
디코더가 바로 읽을 수 있는 `pattern_###.png` 파일은 mono16 형식으로 저장합니다.
현재 원본은 8-bit YUV이므로 mono16은 선형 처리용 컨테이너이며 센서 정밀도를 추가로 늘리지는 않습니다.
`channel_quality_report.json`은 이후 스캔에서 채널을 선택할 수 있도록 R/G/B 4-step 변조, 포화 비율, 암부 비율을 비교합니다.

RGB 원본과 mono16 디코더 이미지는 무손실 PNG DEFLATE level 3으로 저장합니다.
이 설정은 저장 용량과 전송 시간만 바꾸며, 픽셀값을 양자화하거나 리샘플링하거나 JPEG로 변환하지 않습니다.
Android는 파일을 스트림으로 업로드하고, PC는 multipart payload를 1 MiB 단위로 기록합니다.

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
