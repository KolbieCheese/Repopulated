param(
    [string]$GameExe = 'D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe'
)
$ErrorActionPreference = 'Stop'
$nativeRepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $GameExe -PathType Leaf)) { throw "Game executable missing: $GameExe" }
Push-Location -LiteralPath $nativeRepoRoot
try {
    if (-not (Test-Path -LiteralPath '.runtime/RepopulatedDiagnostic.dll' -PathType Leaf)) {
        python tools/build_native.py
        if ($LASTEXITCODE -ne 0) { throw 'Native build failed. See docs/native-alpha.md for setup.' }
    }
    python tools/native_desktop.py --exe $GameExe
} finally { Pop-Location }
