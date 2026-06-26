# PRO4500 Light Engine Control

PRO4500은 Windows에서 실행되는 간단한 Win32 GUI 프로그램입니다. TI DLP LightCrafter 4500 계열 장비를 USB HID로 연결해 Blue LED 밝기를 제어하고, 지정한 폴더의 이미지 파일을 선택한 디스플레이에 전체 화면 패턴으로 순차 투사합니다.

## 주요 기능

- Blue LED 밝기 조절: 슬라이더 값 0~255를 DLPC350 LED current 명령으로 전송합니다.
- LED 끄기: Blue LED 값을 0으로 설정해 출력을 중지합니다.
- 이미지 패턴 투사: 폴더 안의 이미지 파일을 정렬된 순서로 표시합니다.
- 노출/암전 시간 설정: 각 이미지 표시 시간과 이미지 사이의 검은 화면 시간을 ms 단위로 지정합니다.
- 반복 횟수 설정: 이미지 시퀀스를 원하는 횟수만큼 반복합니다.
- 디스플레이 선택: 다중 모니터 환경에서 투사할 화면 인덱스를 지정합니다.
- ESC 또는 Stop 버튼으로 투사 중지.

## 파일 구성

- `PRO4500.cpp`: 메인 Win32 GUI, LED 제어 버튼 처리, 이미지 투사 창, 타이머 기반 패턴 전환 로직을 포함합니다.
- `dlpc350_usb_standalone.cpp`: HIDAPI를 사용해 LightCrafter 4500 USB 장치를 열고, 읽기/쓰기/닫기 동작을 수행합니다.
- `projector_usb_diagnostics.h`: USB 연결 실패 원인을 UI에 표시하기 위한 마지막 오류 메시지 인터페이스입니다.
- `GUI/`: TI LightCrafter 4500 GUI에서 가져온 DLPC350 API, 공통 코드, HIDAPI 소스가 들어 있습니다.
- `build.bat`: MinGW-w64로 실행 파일을 빌드하는 Windows 배치 스크립트입니다.

## 동작 구조

프로그램 시작 시 `wWinMain`에서 공용 컨트롤과 GDI+를 초기화하고 메인 제어 창을 생성합니다. 메인 창에는 Blue LED 슬라이더, LED 적용/끄기 버튼, 패턴 폴더, 노출 시간, 암전 시간, 반복 횟수, 디스플레이 인덱스, 투사/중지 버튼이 배치됩니다.

LED 제어는 UI가 멈추지 않도록 별도 스레드에서 실행됩니다. `set_blue_led()`는 USB mutex를 잡은 뒤 `DLPC350_USB_Init()`, `DLPC350_USB_Open()`으로 장비에 연결하고, `DLPC350_SetLedEnables()`와 `DLPC350_SetLedCurrents()`를 호출한 뒤 연결을 닫습니다. 동시에 여러 USB 명령이 겹치지 않도록 `g_usbBusy`와 `g_usbMutex`를 사용합니다.

이미지 투사는 `start_projection()`에서 시작됩니다. 지정 폴더에서 `bmp`, `png`, `jpg`, `jpeg`, `gif`, `tif`, `tiff` 파일을 찾아 파일명 기준으로 정렬한 뒤, 별도 투사 스레드를 실행합니다. 투사 스레드는 선택된 모니터 영역에 최상위 전체 화면 팝업 창을 만들고, `WM_TIMER` 이벤트마다 이미지 표시 상태와 암전 상태를 번갈아 전환합니다.

이미지 렌더링은 GDI+ `Image`와 `Graphics`를 사용합니다. 이미지는 화면 비율을 유지한 채 창 안에 맞게 확대/축소되며, 보간 모드는 `InterpolationModeNearestNeighbor`로 설정되어 패턴 이미지의 픽셀 경계가 흐려지는 것을 줄입니다.

## 빌드 방법

이 프로젝트는 Windows와 MinGW-w64 환경을 기준으로 합니다. `build.bat`는 기본적으로 다음 경로의 컴파일러를 찾습니다.

```bat
C:\msys64\mingw64\bin\g++.exe
```

MSYS2 MinGW-w64가 다른 위치에 설치되어 있다면 `build.bat`의 `MINGW` 값을 수정하세요.

빌드:

```bat
build.bat
```

빌드가 성공하면 루트 폴더에 `PRO4500.exe`가 생성되고, 실행에 필요한 MinGW 런타임 DLL도 함께 복사됩니다.

## 실행 방법

1. LightCrafter 4500 장비를 USB로 PC에 연결합니다.
2. `PRO4500.exe`를 실행합니다.
3. Blue LED 슬라이더 값을 조정한 뒤 `Apply LED`를 누릅니다.
4. 패턴 이미지가 들어 있는 폴더 경로를 입력합니다. 기본값은 `.\patterns`입니다.
5. `Exposure (ms)`, `Dark (ms)`, `Repeat`, `Display index`를 설정합니다.
6. `Project images`를 눌러 이미지 시퀀스를 전체 화면으로 투사합니다.
7. 중지하려면 `Stop` 버튼을 누르거나 투사 화면에서 `ESC`를 누릅니다.

## USB 연결 참고

프로그램은 TI 장비 VID `0x0451`과 LightCrafter 4500 PID `0x6401`을 기준으로 HID 장치를 찾습니다. 장치가 감지되지 않거나 열리지 않으면 다음을 확인하세요.

- 장비 전원과 USB 데이터 케이블 연결 상태
- Windows 장치 관리자에서 HID 장치 인식 상태
- TI GUI 등 다른 프로그램이 장치를 사용 중인지 여부
- 관리자 권한 실행 필요 여부

## 주의 사항

- 이 프로그램은 Windows 전용입니다.
- 이미지 투사 기능은 실제 프로젝터 제어 명령으로 패턴을 전송하는 방식이 아니라, 선택한 디스플레이에 전체 화면 창을 띄워 이미지를 표시하는 방식입니다.
- `Display index`는 Windows에서 열거된 모니터 순서를 사용합니다. 기본값 `1`은 보통 두 번째 디스플레이를 의미합니다.
- 소스의 일부 한글 상태 메시지는 현재 파일 인코딩 문제로 깨져 보일 수 있습니다. UI 문구를 수정할 때는 UTF-8 인코딩을 유지하는 것이 좋습니다.
