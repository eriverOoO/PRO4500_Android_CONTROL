# PRO4500 패턴 투사 모드

이 프로젝트는 동일한 22장 FPP 패턴을 두 가지 방법으로 투사한다.

## 1. PC / HDMI 모드

개발 및 패턴 수정에 적합한 기본 모드다.

1. `StructuredLightControlPanel.exe`를 실행한다.
2. `Projection`에서 `PC / HDMI`를 선택한다.
3. `Patterns`에 PC의 패턴 폴더를 지정한다. 기본값은 `generated_patterns`이다.
4. `Monitor`에서 프로젝터가 연결된 Windows 디스플레이를 선택한다.
5. `Start Scan`을 누르고 휴대폰에서 `Connect`를 누른다.

PC가 지정 폴더의 각 패턴 이미지를 HDMI 화면에 표시한 뒤 휴대폰 촬영 완료 응답을 기다린다. 패턴을 수정하면 펌웨어를 다시 만들 필요가 없다.

## 2. Projector Flash 모드

현장 사용자용 모드다. 투사 이미지는 프로젝터의 32 MB 플래시에 저장되며 스캔 중 HDMI 영상 출력은 사용하지 않는다. PC와 프로젝터 사이의 USB 연결, PC와 휴대폰 사이의 네트워크 연결은 계속 필요하다.

### 최초 1회 준비

1. `build_native_control_panel.bat`를 실행해 네이티브 도구를 빌드한다.
2. 프로젝터와 일치하는 TI LightCrafter 4500 기본 펌웨어 `.bin` 파일을 준비한다.
3. 제어판에서 `Build Flash Package`를 누르고 기본 펌웨어를 선택한다.
4. 생성된 `dist\PRO4500_patterns_firmware.bin`을 확인한다.
5. 프로젝터 전원과 USB를 안정적으로 연결하고 다른 LightCrafter 제어 프로그램을 종료한다.
6. `Program Projector`를 누르고 경고를 확인한다. 완료될 때까지 전원과 USB를 분리하지 않는다.

패키지 생성 과정은 22개 패턴을 DLPC350 기본 해상도인 912 x 1140, 24-bit RGB BMP로 다시 만들고 기본 펌웨어의 이미지 영역에 0번부터 21번까지 순서대로 넣는다. 128 KB 부트로더 영역은 기록하지 않으며, 기록 후 체크섬을 검증한다.

기본 펌웨어는 반드시 해당 LightCrafter 4500 하드웨어와 호환되는 파일을 사용해야 한다. 잘못된 펌웨어나 기록 중 전원 차단은 TI 복구 절차가 필요한 상태를 만들 수 있다.

### 스캔

1. `Projection`에서 `Projector Flash`를 선택한다.
2. `Start Scan`을 누른다.
3. 휴대폰에서 `Connect`를 누른다.

PC 컨트롤러가 USB로 프로젝터 플래시의 이미지 0~21을 차례로 선택한다. 각 이미지마다 기존과 동일하게 휴대폰 촬영 및 업로드 완료를 기다리므로 촬영 순서가 유지된다.

### 독립 반복 재생

`Auto Play`는 저장된 22장을 프로젝터 내부 시퀀스로 반복 재생한다. 기본 재생 시간은 장당 0.5초다. 이 기능은 설치 확인과 시연용이며 휴대폰 촬영과 동기화되지 않는다. 중지는 `Stop Sequence`를 사용한다.

## 명령행 사용

플래시 패키지 생성:

```powershell
.\build_projector_flash_package.ps1 -BaseFirmware C:\path\to\base_firmware.bin
```

저장 이미지 수 확인:

```powershell
.\dlpc350_projector.exe status
```

독립 반복 재생 및 중지:

```powershell
.\dlpc350_projector.exe auto --count 22 --exposure-us 500000 --period-us 500000
.\dlpc350_projector.exe stop
```

펌웨어 기록은 제어판의 확인 절차를 사용하는 것을 권장한다. 명령행에서는 오작동 방지를 위해 명시적인 확인 토큰이 필요하다.

```powershell
.\dlpc350_projector.exe flash --firmware .\dist\PRO4500_patterns_firmware.bin --confirm ERASE_APP_FLASH
```

## 최초 장비 검증

플래시 기록 후 먼저 흰색, 검은색, Gray code 경계, 사인파 패턴이 HDMI 모드와 같은 순서와 방향으로 보이는지 확인한다. 그 다음 짧은 1회 스캔으로 휴대폰 파일의 `pattern_id` 0~21이 누락 없이 저장되는지 확인한다.
