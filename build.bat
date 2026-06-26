@echo off
setlocal
cd /d "%~dp0"

set "MINGW=C:\msys64\mingw64\bin"
set "BUILD_DIR=build"
set "HIDAPI_OBJ=%BUILD_DIR%\hidapi.o"
if not exist "%MINGW%\g++.exe" (
    echo [ERROR] MinGW-w64 g++.exe was not found:
    echo         %MINGW%\g++.exe
    echo Install MSYS2 MinGW-w64 or edit MINGW in build.bat.
    exit /b 1
)

set "GUI_DIR="
set "GUI_PARENT="

if exist "GUI\dlpc350_api.cpp" if exist "GUI\hidapi-master\windows\hid.c" (
    set "GUI_DIR=GUI"
    set "GUI_PARENT=."
)

if not defined GUI_DIR (
    for /d %%D in (*) do (
        if not defined GUI_DIR if exist "%%D\GUI\dlpc350_api.cpp" if exist "%%D\GUI\hidapi-master\windows\hid.c" (
            set "GUI_DIR=%%D\GUI"
            set "GUI_PARENT=%%D"
        )
    )
)

if not defined GUI_DIR (
    echo [ERROR] LightCrafter 4500 GUI source folder was not found.
    echo.
    echo Expected one of:
    echo   %CD%\GUI
    echo   %CD%\^<extracted-folder^>\GUI
    echo.
    echo Extract LightCrafter4500_GUI_Source_Code_v3.1.0 into this project folder
    echo so that dlpc350_api.cpp and hidapi-master\windows\hid.c are under a GUI folder.
    exit /b 1
)

echo Using GUI source: %GUI_DIR%

if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

echo [1/2] Compiling HIDAPI...
"%MINGW%\gcc.exe" -std=gnu11 -O2 -Wall -Wno-stringop-truncation ^
    -I"%GUI_DIR%\hidapi-master\hidapi" ^
    -c "%GUI_DIR%\hidapi-master\windows\hid.c" ^
    -o "%HIDAPI_OBJ%"
if errorlevel 1 goto :fail
if not exist "%HIDAPI_OBJ%" (
    echo [ERROR] HIDAPI object file was not created:
    echo         %CD%\%HIDAPI_OBJ%
    goto :fail
)

echo [2/2] Building PRO4500.exe...
"%MINGW%\g++.exe" -std=c++17 -O2 -Wall -Wextra -municode -mwindows ^
    -I"%GUI_PARENT%" -I"%GUI_DIR%" -I"%GUI_DIR%\hidapi-master\hidapi" ^
    "PRO4500.cpp" ^
    "dlpc350_usb_standalone.cpp" ^
    "%GUI_DIR%\dlpc350_api.cpp" ^
    "%GUI_DIR%\dlpc350_common.cpp" ^
    "%HIDAPI_OBJ%" ^
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
