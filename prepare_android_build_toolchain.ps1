param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Toolchains = Join-Path $Root ".toolchains"
$Downloads = Join-Path $Toolchains "downloads"
$JdkZip = Join-Path $Downloads "temurin-jdk17.zip"
$CmdlineZip = Join-Path $Downloads "android-commandlinetools.zip"
$GradleZip = Join-Path $Downloads "gradle-8.10.2-bin.zip"
$JdkDir = Join-Path $Toolchains "jdk17"
$AndroidSdk = Join-Path $Toolchains "android-sdk"
$GradleDir = Join-Path $Toolchains "gradle-8.10.2"
$Dist = Join-Path $Root "dist"
$AndroidProject = Join-Path $Root "android\StructuredLightPhoneCamera"
$ApkSource = Join-Path $AndroidProject "app\build\outputs\apk\debug\app-debug.apk"
$ApkTarget = Join-Path $Dist "StructuredLightPhoneCamera-debug.apk"

$JdkUrl = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk"
$CmdlineToolsUrl = "https://dl.google.com/android/repository/commandlinetools-win-14742923_latest.zip"
$GradleUrl = "https://services.gradle.org/distributions/gradle-8.10.2-bin.zip"

function Download-FileIfMissing {
    param(
        [string]$Url,
        [string]$Destination
    )

    if (Test-Path $Destination) {
        Write-Host "[skip] Already downloaded: $Destination"
        return
    }

    Write-Host "[download] $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination
}

function Remove-AndCreateDir {
    param([string]$Path)
    if (Test-Path $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Get-JavaHome {
    param([string]$Path)
    $Java = Get-ChildItem -Path $Path -Recurse -Filter java.exe |
        Where-Object { $_.FullName -like "*\bin\java.exe" } |
        Select-Object -First 1
    if ($null -eq $Java) {
        throw "Could not find java.exe under $Path"
    }
    return Split-Path -Parent (Split-Path -Parent $Java.FullName)
}

New-Item -ItemType Directory -Force -Path $Toolchains, $Downloads, $Dist | Out-Null

Download-FileIfMissing -Url $JdkUrl -Destination $JdkZip
Download-FileIfMissing -Url $CmdlineToolsUrl -Destination $CmdlineZip
Download-FileIfMissing -Url $GradleUrl -Destination $GradleZip

if (-not (Test-Path (Join-Path $JdkDir "bin\java.exe"))) {
    Write-Host "[extract] JDK 17"
    Remove-AndCreateDir -Path $JdkDir
    $TempJdk = Join-Path $Toolchains "jdk17_extract"
    Remove-AndCreateDir -Path $TempJdk
    Expand-Archive -LiteralPath $JdkZip -DestinationPath $TempJdk -Force
    $DetectedJavaHome = Get-JavaHome -Path $TempJdk
    Copy-Item -Path (Join-Path $DetectedJavaHome "*") -Destination $JdkDir -Recurse -Force
    Remove-Item -LiteralPath $TempJdk -Recurse -Force
}

if (-not (Test-Path (Join-Path $AndroidSdk "cmdline-tools\latest\bin\sdkmanager.bat"))) {
    Write-Host "[extract] Android command-line tools"
    $CmdlineRoot = Join-Path $AndroidSdk "cmdline-tools"
    $LatestRoot = Join-Path $CmdlineRoot "latest"
    Remove-AndCreateDir -Path $LatestRoot
    $TempCmdline = Join-Path $Toolchains "cmdline-tools_extract"
    Remove-AndCreateDir -Path $TempCmdline
    Expand-Archive -LiteralPath $CmdlineZip -DestinationPath $TempCmdline -Force
    $SourceCmdline = Join-Path $TempCmdline "cmdline-tools"
    if (-not (Test-Path $SourceCmdline)) {
        throw "Unexpected command-line tools archive layout."
    }
    Copy-Item -Path (Join-Path $SourceCmdline "*") -Destination $LatestRoot -Recurse -Force
    Remove-Item -LiteralPath $TempCmdline -Recurse -Force
}

if (-not (Test-Path (Join-Path $GradleDir "bin\gradle.bat"))) {
    Write-Host "[extract] Gradle 8.10.2"
    $TempGradle = Join-Path $Toolchains "gradle_extract"
    Remove-AndCreateDir -Path $TempGradle
    Expand-Archive -LiteralPath $GradleZip -DestinationPath $TempGradle -Force
    $ExtractedGradle = Get-ChildItem -Path $TempGradle -Directory |
        Where-Object { $_.Name -like "gradle-*" } |
        Select-Object -First 1
    if ($null -eq $ExtractedGradle) {
        throw "Could not find extracted Gradle directory."
    }
    if (Test-Path $GradleDir) {
        Remove-Item -LiteralPath $GradleDir -Recurse -Force
    }
    Move-Item -LiteralPath $ExtractedGradle.FullName -Destination $GradleDir
    Remove-Item -LiteralPath $TempGradle -Recurse -Force
}

$env:JAVA_HOME = $JdkDir
$env:ANDROID_HOME = $AndroidSdk
$env:ANDROID_SDK_ROOT = $AndroidSdk
$env:PATH = "$JdkDir\bin;$AndroidSdk\platform-tools;$GradleDir\bin;$env:PATH"

$SdkManager = Join-Path $AndroidSdk "cmdline-tools\latest\bin\sdkmanager.bat"
$Gradle = Join-Path $GradleDir "bin\gradle.bat"

Write-Host "[sdk] Accepting Android SDK licenses"
$LicenseInput = ("y`n" * 80)
$LicenseInput | & $SdkManager --sdk_root=$AndroidSdk --licenses | Write-Host

Write-Host "[sdk] Installing Android SDK packages"
& $SdkManager --sdk_root=$AndroidSdk "platform-tools" "platforms;android-35" "build-tools;35.0.0"

Write-Host "[check] Java"
& (Join-Path $JdkDir "bin\java.exe") -version

Write-Host "[check] Gradle"
& $Gradle -v

if ($SkipBuild) {
    Write-Host "[ok] Android build toolchain is ready."
    exit 0
}

Write-Host "[build] Building debug APK"
& $Gradle -p $AndroidProject :app:assembleDebug

if (-not (Test-Path $ApkSource)) {
    throw "Build completed but APK was not found: $ApkSource"
}

Copy-Item -LiteralPath $ApkSource -Destination $ApkTarget -Force
Write-Host "[ok] APK is ready: $ApkTarget"
