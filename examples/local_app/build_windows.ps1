param(
    [Parameter(Mandatory = $true)][string]$StatePath,
    [string]$OutputDirectory = "dist",
    [switch]$SingleFile
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$state = (Resolve-Path -LiteralPath $StatePath).Path
$version = (Get-Content -LiteralPath (Join-Path $repo "VERSION") -Raw).Trim()
$destination = [System.IO.Path]::GetFullPath((Join-Path $repo $OutputDirectory))
$stateData = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
if ($stateData.schema -ne "tsukushi-local-state-v1") {
    throw "Expected a derived Tsukushi state checkpoint, not a raw benchmark bundle."
}

Push-Location $repo
try {
    $bundleMode = if ($SingleFile) { "--onefile" } else { "--onedir" }
    $dataArgs = @(
        "--add-data", "$(Join-Path $PSScriptRoot 'index.html');examples/local_app",
        "--add-data", "$(Join-Path $PSScriptRoot 'icon.png');examples/local_app",
        "--add-data", "$(Join-Path $PSScriptRoot '说明.txt');examples/local_app",
        "--add-data", "$(Join-Path $repo 'LICENSE');examples/local_app",
        "--add-data", "$(Join-Path $repo 'examples/member_ensemble');examples/member_ensemble"
    )
    if ($SingleFile) {
        $dataArgs += @("--add-data", "$state;examples/local_app")
        $dataArgs += @("--add-data", "$(Join-Path $repo 'VERSION');examples/local_app")
    }
    python -m PyInstaller --noconfirm --clean $bundleMode --windowed --name Tsukushi `
        --icon (Join-Path $PSScriptRoot "icon.ico") `
        --distpath $destination --workpath (Join-Path $repo "build") `
        --specpath (Join-Path $repo "build") `
        @dataArgs `
        (Join-Path $PSScriptRoot "app.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    if ($SingleFile) {
        $single = Join-Path $destination "Tsukushi-Windows-SingleFile-v$version.exe"
        Copy-Item -LiteralPath (Join-Path $destination "Tsukushi.exe") -Destination $single -Force
        Get-Item -LiteralPath $single | Select-Object FullName, Length
        Get-FileHash -LiteralPath $single -Algorithm SHA256
        return
    }
    Copy-Item -LiteralPath $state -Destination (Join-Path $destination "Tsukushi\tsukushi-state.json")
    Copy-Item -LiteralPath (Join-Path $repo "VERSION") -Destination (Join-Path $destination "Tsukushi\VERSION")
    $archive = Join-Path $destination "Tsukushi-Windows-v$version.zip"
    Compress-Archive -LiteralPath (Join-Path $destination "Tsukushi") -DestinationPath $archive -Force
    Get-Item -LiteralPath $archive | Select-Object FullName, Length
    Get-FileHash -LiteralPath $archive -Algorithm SHA256
} finally {
    Pop-Location
}
