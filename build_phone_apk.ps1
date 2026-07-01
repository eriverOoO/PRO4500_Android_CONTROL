param()

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
if (-not $root) {
    $root = (Get-Location).Path
}

$androidProject = Join-Path $root "android\StructuredLightPhoneCamera"
$dist = Join-Path $root "dist"
$apkSource = Join-Path $androidProject "app\build\outputs\apk\debug\app-debug.apk"
$apkTarget = Join-Path $dist "StructuredLightPhoneCamera-debug.apk"
$localTools = Join-Path $root ".toolchains"
$jdk = Join-Path $localTools "jdk17"
$androidSdk = Join-Path $localTools "android-sdk"
$gradleBin = Join-Path $localTools "gradle-8.10.2\bin"
$gradle = Join-Path $gradleBin "gradle.bat"
$gradleWrapper = Join-Path $androidProject "gradlew.bat"

Write-Host "[build] StructuredLightPhoneCamera debug APK"
Write-Host "[build] Project: $androidProject"

if (Test-Path (Join-Path $jdk "bin\java.exe")) {
    $env:JAVA_HOME = $jdk
    $env:ANDROID_HOME = $androidSdk
    $env:ANDROID_SDK_ROOT = $androidSdk
    $env:Path = "$(Join-Path $jdk "bin");$(Join-Path $androidSdk "platform-tools");$gradleBin;$env:Path"
}

if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    throw "Java/JDK was not found. Run prepare_android_build_toolchain.ps1 first, or install Android Studio."
}

Push-Location $androidProject
try {
    Write-Host "[build] Running Gradle..."
    if (Test-Path $gradleWrapper) {
        & $gradleWrapper :app:assembleDebug
    } elseif (Test-Path $gradle) {
        & $gradle :app:assembleDebug
    } else {
        throw "Gradle was not found. Run prepare_android_build_toolchain.ps1 first, or install Gradle."
    }
    if ($LASTEXITCODE -ne 0) {
        throw "APK build failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

if (-not (Test-Path $apkSource)) {
    throw "Build succeeded but APK was not found: $apkSource"
}

New-Item -ItemType Directory -Force -Path $dist | Out-Null
Copy-Item -Force $apkSource $apkTarget

Write-Host ""
Write-Host "[ok] APK is ready:"
Write-Host $apkTarget
Write-Host ""
Write-Host "Send this APK to the phone and open it there to install."
