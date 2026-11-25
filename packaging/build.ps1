# Build the Windows installer:   .\packaging\build.ps1            (or  -RelayUrl https://example.com  to bake your server in)
#
# Steps: draw the icon and wizard pictures -> package the app with PyInstaller -> wrap it in an installer with Inno Setup.
# Output: dist\AccessFromPhone-Setup-<version>.exe   (and the unpacked app in dist\AccessFromPhone\ for testing)
param(
    [string]$RelayUrl = $env:AFP_RELAY_URL,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run .\run.ps1 once first: it creates the .venv this build uses." }

& $py -m pip install --quiet pyinstaller pystray pillow
$version = (Select-String -Path "pyproject.toml" -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building AccessFromPhone $version" -ForegroundColor Cyan

# Stamp the version (and your relay address, if any) into the program, then put the file back as it was.
$stamp = "src\handoff\_build.py"
$original = Get-Content $stamp -Raw
$plain = New-Object System.Text.UTF8Encoding $false  # no hidden byte-order mark
[System.IO.File]::WriteAllText((Join-Path $root $stamp), "VERSION = `"$version`"`nRELAY_URL = `"$RelayUrl`"`n", $plain)
try {
    Write-Host "1/3  Drawing the icon and the installer pictures"
    & $py packaging\make_assets.py
    if ($LASTEXITCODE) { throw "make_assets failed" }

    Write-Host "2/3  Packaging the app (this takes a minute or two)"
    & $py -m PyInstaller packaging\AccessFromPhone.spec --noconfirm --distpath dist --workpath build\pyinstaller
    if ($LASTEXITCODE) { throw "PyInstaller failed" }

    if (-not $SkipInstaller) {
        Write-Host "3/3  Building the installer"
        $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $iscc) { throw "Inno Setup is not installed. Install it with:  winget install JRSoftware.InnoSetup" }
        & $iscc "/DAppVersion=$version" packaging\installer.iss
        if ($LASTEXITCODE) { throw "Inno Setup failed" }
        Write-Host "`nDone: dist\AccessFromPhone-Setup-$version.exe" -ForegroundColor Green
    }
}
finally {
    [System.IO.File]::WriteAllText((Join-Path $root $stamp), $original, $plain)
}
