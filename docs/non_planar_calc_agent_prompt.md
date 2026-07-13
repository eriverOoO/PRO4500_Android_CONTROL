# Non-planar_calc integration prompt

아래 프롬프트를 `C:\Users\shang\OneDrive\바탕 화면\Non-planar_calc` 작업공간을 담당하는 에이전트에게 그대로 전달한다.

```text
작업공간: C:\Users\shang\OneDrive\바탕 화면\Non-planar_calc

PRO4500_Control_System의 최신 촬영 저장 구조에 맞춰 PCB FPP 디버거와 디코더를 정합화하고, 사용자가 프로젝트 루트에서 run_debugger.bat 또는 "run debug"를 실행하면 새 촬영 폴더를 즉시 선택해 디버깅할 수 있게 구현·검증해라. 분석만 하지 말고 코드, 테스트, 문서, 실행 파일 빌드까지 완료해라.

중요한 촬영 저장 계약:

captures/<scan_id>/
  angle_000/
    pattern_000.png
    ...
    pattern_021.png
    scan_log.json
    hdr_merge_report.json
    channel_quality_report.json
  angle_180/
    pattern_000.png
    ...
    pattern_021.png
    scan_log.json
    hdr_merge_report.json
    channel_quality_report.json
  analysis_manifest.json
  scan_log.json
  scan_log.csv

필수 디코더 입력은 각 angle 폴더 최상단의 mono16 pattern_000.png~pattern_021.png뿐이다. scan_log와 report는 메타데이터이며, 다음 두 종류는 사용자가 촬영 GUI 체크박스를 선택했을 때만 존재하는 선택 산출물이다.

1. raw/exposure 원본:
   captures/<scan_id>/raw/angle_000/<bracket>/pattern_XXX.png
   captures/<scan_id>/raw/angle_180/<bracket>/pattern_XXX.png
2. HDR 진단 마스크:
   captures/<scan_id>/angle_000/hdr_masks/pattern_XXX_saturated.png
   captures/<scan_id>/angle_000/hdr_masks/pattern_XXX_dark.png

기본 촬영에서는 raw, exposures, hdr_masks 폴더가 전혀 없어야 정상이다. Non-planar_calc의 높이 계산, Gray code, PSP, 0/180 정합·융합, debug GUI는 이 선택 폴더를 요구하거나 스캔해서 입력 영상으로 선택하면 안 된다. scan_log 안에 삭제된 임시 raw 경로가 남아 있더라도, angle 폴더 최상단의 최종 pattern_XXX.png를 항상 우선해야 한다.

구현 요구사항:

- 입력으로 scan root(captures/<scan_id>) 또는 angle_000/angle_180 폴더를 모두 받을 수 있게 한다.
- scan root를 선택하면 angle_000을 기본 단일 뷰로 찾고, 0/180 자동 융합 모드에서는 두 angle 폴더를 정확히 찾는다.
- raw, exposures, hdr_masks, processed/debug 산출물 하위 폴더는 pattern 탐색 대상에서 제외한다.
- 최종 pattern 파일이 mono16 PNG이면 선형적으로 0..255 float 계산 도메인으로 정규화한다. 기존 saturation=250, dark=5 임계값 의미가 유지되어야 한다.
- 최종 mono16은 이미 촬영 시스템에서 하나의 고정 RGB 채널을 선택해 만든 영상이다. grayscale 입력에서는 input-color-mode가 blue/red/green이어도 추가 RGB 혼합이나 luminance 변환을 하지 않는다.
- GUI의 폴더 선택과 최근 경로 처리를 새 구조에 맞춘다. scan root를 골라도 실제로 해석된 angle 경로와 0/180 융합 여부를 화면과 debug report에 명확히 표시한다.
- 입력 검증 오류는 누락된 pattern id를 구체적으로 표시하되 raw/hdr_masks 부재를 오류로 취급하지 않는다.
- README와 docs/capture_workspace_prompts.md에서 exposures와 hdr_masks를 필수 저장물처럼 설명한 부분을 선택 사항으로 수정한다.
- 기존 사용자의 관련 없는 수정(run_debugger.bat, scripts/clean_generated.py 등 포함)을 되돌리지 말고 현재 코드와 함께 작업한다.

필수 테스트:

- raw/exposures/hdr_masks 없이 22개 최종 pattern만 있는 angle 폴더 로드
- 선택 폴더가 함께 있어도 최종 pattern만 매핑되는지 검증
- scan root에서 angle_000 자동 선택
- scan root에서 angle_000 + angle_180 자동 융합 입력 해석
- scan_log에 존재하지 않는 raw 경로와 유효한 최종 pattern 경로가 섞여 있어도 최종 pattern 우선
- uint16의 0, 32768, 65535가 계산 도메인 약 0, 127.502, 255로 선형 변환
- 기존 decoder/debugger 테스트 전체 회귀 실행

완료 검증:

1. run_debugger.bat --prepare
2. pytest 전체 실행
3. build_debugger.bat로 dist/PCB_FPP_Debugger/PCB_FPP_Debugger.exe 재빌드
4. dist/PCB_FPP_Debugger/PCB_FPP_Debugger.exe --self-test 성공 확인
5. 가능하면 새 구조의 synthetic 0/180 scan root로 GUI/debug pipeline smoke test

최종 보고에는 변경 파일, 실제 실행한 명령과 결과, 새 구조에서 기본/선택 산출물의 구분, 남은 제한 사항을 간결하게 정리해라.
```
