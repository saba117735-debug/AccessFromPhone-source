# Build the Android app.      .\android\build.ps1            a debug APK you can install on a phone  (dist\AccessFromPhone-android-debug.apk)
#                             .\android\build.ps1 -Release   a smaller release APK (unsigned: sign it before the Play Store)
#                             .\android\build.ps1 -Test      run the app's unit tests only
#
# It uses the Java that comes with Android Studio and the Android SDK Android Studio installed, and keeps its own Gradle cache
# (%LOCALAPPDATA%\afp-gradle) so a damaged shared cache can't break it. It also prefers a Gradle already on this PC over downloading one,
# because a work network often blocks that download.
param([switch]$Release, [switch]$Test)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $PSScriptRoot

# ---- Java: the first one that really runs. A reinstall of Android Studio can leave a broken old folder next to the new one
#      (for example "Android Studio" beside "Android Studio1"), so each candidate is tested, newest first.
function Test-Java([string]$javaHome) {
    $exe = Join-Path $javaHome "bin\java.exe"
    if (-not (Test-Path $exe)) { return $false }
    # Java prints its version on the error stream, which PowerShell would treat as a failure: judge it by its exit code instead.
    $run = Start-Process $exe -ArgumentList "-version" -Wait -PassThru -WindowStyle Hidden -RedirectStandardError ([IO.Path]::GetTempFileName())
    return ($run.ExitCode -eq 0)
}
$candidates = @()
foreach ($folder in @("$env:ProgramFiles\Android", "$env:LOCALAPPDATA\Programs")) {
    if (Test-Path $folder) {
        $candidates += Get-ChildItem $folder -Directory -Filter "Android Studio*" | Sort-Object LastWriteTime -Descending | ForEach-Object { Join-Path $_.FullName "jbr" }
    }
}
$candidates += @($env:JAVA_HOME, [Environment]::GetEnvironmentVariable("JAVA_HOME", "Machine"), [Environment]::GetEnvironmentVariable("JAVA_HOME", "User")) | Where-Object { $_ }
$java = $candidates | Where-Object { Test-Java $_ } | Select-Object -First 1
if (-not $java) { throw "No working Java was found. Install Android Studio from developer.android.com/studio (it includes one), then open it once." }
$env:JAVA_HOME = $java
$javaMajor = 21
$javaInfo = Join-Path $java "release"  # (not named $release: PowerShell ignores case, and that is the -Release switch)
if (Test-Path $javaInfo) { $m = Select-String -Path $javaInfo -Pattern 'JAVA_VERSION="(\d+)' | Select-Object -First 1; if ($m) { $javaMajor = [int]$m.Matches[0].Groups[1].Value } }
Write-Host "Java $javaMajor from $java" -ForegroundColor DarkGray

$env:ANDROID_HOME = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { "$env:LOCALAPPDATA\Android\Sdk" }
if (-not (Test-Path "$env:ANDROID_HOME\platforms")) { throw "The Android SDK was not found at $env:ANDROID_HOME. Open Android Studio once to install it." }
$env:GRADLE_USER_HOME = "$env:LOCALAPPDATA\afp-gradle"
Remove-Item Env:JAVA_TOOL_OPTIONS -ErrorAction SilentlyContinue

# ---- Gradle: newest one already unpacked on this PC that works with this Java (Java 25 needs Gradle 9.1 or newer; Java 17-24 need 8.13+).
$needs = if ($javaMajor -ge 25) { [version]"9.1" } else { [version]"8.13" }
$local = Get-ChildItem "$env:USERPROFILE\.gradle\wrapper\dists" -Recurse -Filter gradle.bat -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "gradle-(\d+(\.\d+)+)\\bin" } |
    ForEach-Object { [pscustomobject]@{ Version = [version]$Matches[1]; Path = $_.FullName } } |
    Where-Object { $_.Version -ge $needs } | Sort-Object Version -Descending | Select-Object -First 1
if ($local) { $gradle = $local.Path; Write-Host "Gradle $($local.Version) (already on this PC)" -ForegroundColor DarkGray }
else { $gradle = Join-Path $PSScriptRoot "gradlew.bat"; Write-Host "Gradle from the project's wrapper (this downloads it the first time)" -ForegroundColor DarkGray }

# ---- Draw the icons from the same logo as the PC app (needs the project's .venv).
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (Test-Path $py) { & $py tools\make_icons.py }

$task = if ($Test) { "testDebugUnitTest" } elseif ($Release) { "assembleRelease" } else { "assembleDebug" }
& cmd /c "`"$gradle`" $task --no-daemon --console=plain 2>&1"
if ($LASTEXITCODE) { throw "The build failed. Scroll up for the first 'What went wrong'." }

if (-not $Test) {
    $kind = if ($Release) { "release" } else { "debug" }
    $apk = Get-ChildItem "app\build\outputs\apk\$kind" -Filter *.apk | Select-Object -First 1
    New-Item -ItemType Directory -Force "$repo\dist" | Out-Null
    Copy-Item $apk.FullName "$repo\dist\AccessFromPhone-android-$kind.apk" -Force
    Write-Host "`nDone: dist\AccessFromPhone-android-$kind.apk" -ForegroundColor Green
}
