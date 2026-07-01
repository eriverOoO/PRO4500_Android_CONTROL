@echo off
setlocal

set "ROOT=%~dp0"
set "APK=%ROOT%dist\StructuredLightPhoneCamera-debug.apk"
set "LOCAL_ADB=%ROOT%.toolchains\android-sdk\platform-tools\adb.exe"

if not exist "%APK%" (
  echo [error] APK not found:
  echo %APK%
  echo Run build_phone_apk.bat first.
  exit /b 1
)

if exist "%LOCAL_ADB%" (
  set "ADB=%LOCAL_ADB%"
) else (
  where adb >nul 2>nul
  if errorlevel 1 (
    echo [error] adb was not found on PATH.
    echo Run prepare_android_build_toolchain.ps1 first, or send the APK manually.
    exit /b 1
  )
  set "ADB=adb"
)

echo [install] Installing APK to connected Android device...
"%ADB%" install -r "%APK%"
if errorlevel 1 (
  echo [error] adb install failed. Check USB debugging and device authorization.
  exit /b 1
)

echo [ok] Installed:
echo %APK%
exit /b 0
