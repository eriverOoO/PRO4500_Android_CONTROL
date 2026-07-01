# PRO4500 Structured-Light Phone Capture System

이 저장소는 Windows PC와 Galaxy S23 스마트폰을 사용해 구조광 패턴 촬영을 자동화하는 실험용 제어 코드입니다.

현재 구성은 두 축으로 나뉩니다.

- `PRO4500.exe`: 기존 Win32 프로그램. PRO4500/LightCrafter 계열 장치의 Blue LED 전류 제어와 폴더 이미지 전체화면 투사를 담당합니다.
- `structured_light_pc_controller.py`: 새 PC 마스터 컨트롤러. 패턴을 PC 확장 디스플레이에 순차 표시하고, Android 앱에 Wi-Fi 촬영 명령을 보내고, 업로드된 이미지를 로그와 함께 저장합니다.

완전한 패턴-사진 번호 동기화가 필요하면 `structured_light_pc_controller.py`가 패턴 표시까지 직접 담당해야 합니다. 기존 `PRO4500.exe`를 독립 실행해서 타이머로 패턴을 넘기면 PC 컨트롤러가 "현재 어떤 패턴이 실제 표시 중인지"를 ACK로 받을 수 없어서 사진 번호가 꼬일 수 있습니다.

## 전체 구조

1. PC가 FastAPI WebSocket/HTTP 서버를 시작합니다.
2. Android 앱 `StructuredLightPhoneCamera`가 `ws://<PC_IP>:8765/ws`로 연결합니다.
3. PC가 프로젝터가 연결된 Windows 확장 디스플레이에 패턴 이미지를 표시합니다.
4. PC가 `settle-ms`만큼 기다립니다.
5. PC가 Android 앱에 `capture` 명령을 보냅니다.
6. Android 앱이 CameraX로 후면 카메라 사진을 촬영합니다.
7. Android 앱이 `/upload`로 JPEG를 multipart 업로드합니다.
8. Android 앱이 WebSocket으로 `capture_done`을 보냅니다.
9. PC는 업로드와 `capture_done` 둘 다 확인한 뒤 다음 패턴으로 넘어갑니다.
10. 종료 후 `scan_log.json`과 `scan_log.csv`를 저장합니다.

## 필요한 장비

- Windows PC
- Galaxy S23 또는 CameraX가 동작하는 Android 폰
- PRO4500 또는 HDMI 프로젝터
- PC와 폰이 같은 Wi-Fi/LAN에 연결된 공유기
- 구조광 촬영 대상, 고정 지그, 가능하면 주변광 차단

## 네트워크 설정

- PC와 폰은 같은 LAN에 있어야 합니다.
- Windows 방화벽에서 TCP `8765` 포트를 허용해야 합니다.
- PC IP는 `ipconfig`에서 확인합니다. 예: `192.168.0.12`
- Android 앱에는 `ws://192.168.0.12:8765/ws` 형식으로 입력합니다.

## PC Python 환경

Python 3.10 이상을 권장합니다. 현재 스크립트는 Python 3.12 기준으로 작성했지만, 3.10+에서도 동작하도록 제한했습니다.

이 PC에서 바로 준비하려면:

```powershell
powershell -ExecutionPolicy Bypass -File .\prepare_pc_python_env.ps1
```

성공 후 PC 컨트롤러는 다음 Python으로 실행합니다.

```powershell
.\.venv-pc\Scripts\python.exe structured_light_pc_controller.py --patterns generated_patterns --output captures --monitor 1
```

수동으로 준비하려면:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

더미 패턴을 만들려면:

```powershell
python tools\generate_dummy_patterns.py --output example_patterns --count 8 --width 1280 --height 800
```

기존 구조광 패턴을 쓰려면 `generated_patterns` 또는 `patterns\fpp_14`를 지정합니다.

## PC 실행 예

터미널 명령 대신 GUI를 쓰려면 루트의 실행 파일을 더블클릭합니다.

```text
StructuredLightControlPanel.exe
```

GUI에서 패턴 폴더, 출력 폴더, 모니터 번호, 노출값을 설정하고 `Start Scan`을 누르면 됩니다. Android 앱에는 GUI 상단에 표시되는 `Phone URL`을 입력합니다. `Patterns` 폴더 안의 지원 이미지 파일은 전부 순서대로 실행되므로 패턴 개수는 폴더 구성으로 조절합니다. `Angles`에 `0,180`처럼 여러 각도를 입력하면 한 각도 시퀀스가 끝난 뒤 `Next Angle` 버튼이 활성화됩니다. PCB를 회전한 다음 `Next Angle`을 누르면 다음 각도 패턴 시퀀스가 이어집니다. `Blue LED` 슬라이더, `Apply LED`, `LED Off`로 PRO4500/LightCrafter 4500의 Blue LED 세기를 조절할 수 있습니다. 스크립트 런처가 더 편하면 `StructuredLightControlPanel.vbs` 또는 `run_control_panel.bat`도 사용할 수 있습니다.

GUI 실행 파일을 다시 빌드하려면:

```text
build_native_control_panel.bat
```

```powershell
python structured_light_pc_controller.py --patterns generated_patterns --output captures --monitor 1 --settle-ms 300 --exposure-us 10000 --iso 100 --manual true
```

두 각도 촬영을 수동 회전으로 진행하려면:

```powershell
python structured_light_pc_controller.py --patterns generated_patterns --output captures --monitor 1 --angles 0,180 --settle-ms 300
```

GUI에서는 두 번째 각도부터 `Next Angle` 버튼으로 진행합니다. 터미널에서 직접 실행하면 콘솔 Enter 대기 방식을 사용할 수 있고, 모터 제어 명령이 있으면 다음처럼 붙일 수 있습니다.

```powershell
python structured_light_pc_controller.py --angles 0,180 --rotation-command "python tools\rotate_stage.py --angle {angle}"
```

## Android 앱 빌드

Android Studio에서 다음 폴더를 엽니다.

```text
android/StructuredLightPhoneCamera
```

이 저장소에는 Gradle Wrapper JAR를 생성해 넣지 않았습니다. Android Studio에서 프로젝트를 열어 Gradle Sync를 실행하거나, 로컬 Gradle이 있다면 `gradle -p android/StructuredLightPhoneCamera :app:assembleDebug`로 빌드합니다.

빌드 후 Galaxy S23에 설치합니다. 앱 실행 시 카메라 권한을 허용하고 PC WebSocket URL을 입력한 뒤 `Connect`를 누릅니다.

앱은 CameraX Preview + ImageCapture를 사용합니다. 수동 노출/ISO/초점은 Camera2Interop으로 적용을 시도합니다. 기기/렌즈에서 수동 설정을 지원하지 않으면 앱 로그에 표시하고 자동 모드로 촬영합니다.

첫 버전은 JPEG 촬영/업로드만 구현했습니다. DNG/RAW 저장은 Camera2 기반 확장 작업으로 남겨두었습니다.

## APK만 만들어서 폰으로 보내기

이 PC에 Android Studio가 없어도, 루트에서 다음 PowerShell 스크립트를 실행하면 프로젝트 폴더 안의 `.toolchains`에 JDK/Gradle/Android SDK를 내려받고 APK까지 빌드합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\prepare_android_build_toolchain.ps1
```

이미 도구가 준비되어 있으면 다음 배치 파일만 실행해도 됩니다.

```powershell
.\build_phone_apk.bat
```

성공하면 아래 APK가 생성됩니다.

```text
dist\StructuredLightPhoneCamera-debug.apk
```

이 APK를 Galaxy S23으로 복사해서 열면 설치할 수 있습니다. 폰에서 "알 수 없는 앱 설치 허용"과 카메라 권한 허용이 필요할 수 있습니다.

USB 디버깅과 `adb`가 준비되어 있으면 다음 명령으로 바로 설치할 수도 있습니다.

```powershell
.\install_phone_apk_adb.bat
```

## 스캔 절차

1. PC와 폰을 같은 Wi-Fi에 연결합니다.
2. 프로젝터를 Windows 확장 디스플레이로 설정합니다.
3. PC에서 Python 컨트롤러를 실행합니다.
4. Android 앱에서 PC WebSocket URL을 입력하고 연결합니다.
5. PC 컨트롤러가 패턴을 표시하고 폰 촬영/업로드를 반복합니다.
6. 완료 후 `captures/<scan_id>/`에서 이미지를 확인합니다.
7. `scan_log.json`과 `scan_log.csv`에서 패턴 파일명, `pattern_id`, `capture_id`, 업로드 파일명을 확인합니다.

## 출력 구조

```text
captures/
  scan_YYYYMMDD_HHMMSS/
    scan_YYYYMMDD_HHMMSS_angle_000_pattern_000_capture_000.jpg
    scan_YYYYMMDD_HHMMSS_angle_000_pattern_001_capture_001.jpg
    scan_log.json
    scan_log.csv
```

## 간단 테스트

1. `python tools\generate_dummy_patterns.py --output example_patterns --count 4`
2. `python structured_light_pc_controller.py --patterns example_patterns --output captures --windowed --monitor 0`
3. Android 앱에서 `ws://<PC_IP>:8765/ws`로 연결
4. 4장의 패턴마다 사진이 촬영되고 PC로 업로드되는지 확인
5. `scan_log.csv`에서 `pattern_000.png`와 `capture_000.jpg`가 매칭되는지 확인
6. Wi-Fi를 잠시 끊어 timeout/retry 로그가 남는지 확인

## 문제 해결

- 폰이 PC에 연결 안 됨: PC IP, 같은 LAN 여부, Windows 방화벽의 8765 포트 허용을 확인합니다.
- 업로드 실패: Android 앱의 `upload_url` 로그와 PC 콘솔의 `/upload` 수신 로그를 확인합니다.
- 카메라 권한 실패: Android 설정에서 앱 카메라 권한을 허용합니다.
- 이미지가 어둡거나 밝음: `--exposure-us`, `--iso`, 프로젝터 밝기를 조정합니다.
- 패턴과 사진 번호가 안 맞음: `PRO4500.exe` 독립 투사 대신 `structured_light_pc_controller.py`의 패턴 표시 모드를 사용합니다.
- 프로젝터 화면이 다른 모니터에 뜸: `--monitor`, `--windowed`, `--window-x`, `--window-y` 옵션을 조정합니다.

## 구조광 촬영 주의사항

- 가능하면 자동노출, 자동초점, 자동화이트밸런스를 끕니다.
- 카메라, 프로젝터, 대상은 촬영 중 움직이지 않게 고정합니다.
- 프로젝터 밝기와 주변 조명을 고정합니다.
- 패턴 표시 직후에는 `--settle-ms`로 안정화 시간을 둡니다.
- HDR, Night mode, Beauty/filter, flash는 사용하지 않습니다.

## Height Map / Point Cloud

이 커밋은 원격 촬영과 이미지 수집 자동화까지 구현합니다. 실제 height map과 point cloud 산출에는 다음 단계가 추가로 필요합니다.

- Gray-code/PSP 디코딩
- 카메라-프로젝터 calibration
- 기준 평면 보정
- 삼각측량 및 `.ply`/height image 출력

캡처 결과와 로그는 이 후처리 파이프라인의 입력으로 사용할 수 있게 `scan_id`, `pattern_id`, `capture_id`, `angle_deg`를 일관되게 남깁니다.
