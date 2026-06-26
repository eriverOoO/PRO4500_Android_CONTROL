@echo off
setlocal
cd /d "%~dp0"

set "MINGW=C:\msys64\mingw64\bin"
if not exist "%MINGW%\g++.exe" (
    echo [ERROR] MinGW-w64 g++.exe was not found:
    echo         %MINGW%\g++.exe
    echo Install MSYS2 MinGW-w64 or edit MINGW in build.bat.
    exit /b 1
)

if not exist build mkdir build

echo [1/2] Compiling HIDAPI...
"%MINGW%\gcc.exe" -std=gnu11 -O2 -Wall ^
    -I"GUI\hidapi-master\hidapi" ^
    -c "GUI\hidapi-master\windows\hid.c" ^
    -o "build\hidapi.o"
if errorlevel 1 goto :fail

echo [2/2] Building PRO4500.exe...
"%MINGW%\g++.exe" -std=c++17 -O2 -Wall -Wextra -municode -mwindows ^
    -I"GUI" -I"GUI\hidapi-master\hidapi" ^
    "PRO4500.cpp" ^
    "dlpc350_usb_standalone.cpp" ^
    "GUI\dlpc350_api.cpp" ^
    "GUI\dlpc350_common.cpp" ^
    "build\hidapi.o" ^
    -o "PRO4500.exe" ^
    -lsetupapi -lhid -lgdiplus -lcomctl32 -lole32 -luuid
if errorlevel 1 goto :fail

echo Copying MinGW runtime DLLs...
copy /y "%MINGW%\libgcc_s_seh-1.dll" . >nul
copy /y "%MINGW%\libstdc++-6.dll" . >nul
copy /y "%MINGW%\libwinpthread-1.dll" . >nul

echo.
echo Build complete: %CD%\PRO4500.exe
exit /b 0

:fail
echo.
echo [ERROR] Build failed.
exit /b 1
