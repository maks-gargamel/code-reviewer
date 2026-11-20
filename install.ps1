[CmdletBinding()]
param(
    [string]$Version = "latest",
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\code-reviewer",
    [string]$LocalBinary = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Repo = "maks-gargamel/code-reviewer"
$Asset = "code-reviewer-windows-x86_64.exe"
$TargetExe = Join-Path $InstallDir "code-reviewer.exe"
$AliasCmd = Join-Path $InstallDir "cr.cmd"

if ($Uninstall) {
    foreach ($file in @($TargetExe, $AliasCmd)) {
        if (Test-Path $file) {
            Remove-Item -Force $file
            Write-Host "Удалено: $file"
        }
    }
    exit 0
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if ($LocalBinary -ne "") {
    if (-not (Test-Path $LocalBinary)) {
        throw "Файл не найден: $LocalBinary"
    }
    Copy-Item -Force $LocalBinary $TargetExe
} else {
    if ($Version -eq "latest") {
        $BaseUrl = "https://github.com/$Repo/releases/latest/download/$Asset"
    } else {
        $BaseUrl = "https://github.com/$Repo/releases/download/$Version/$Asset"
    }

    $TempExe = Join-Path ([System.IO.Path]::GetTempPath()) $Asset
    $TempSum = "$TempExe.sha256"

    Write-Host "Скачивание $Asset ($Version)..."
    Invoke-WebRequest -Uri $BaseUrl -OutFile $TempExe

    try {
        Invoke-WebRequest -Uri "$BaseUrl.sha256" -OutFile $TempSum
        $Expected = ((Get-Content $TempSum -Raw).Trim().Split()[0]).ToLowerInvariant()
        $Actual = (Get-FileHash -Path $TempExe -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Expected -and ($Actual -ne $Expected)) {
            throw "Не совпала контрольная сумма SHA-256!"
        }
    } catch {
        if ($_.Exception.Message -like "*SHA-256*") { throw }
    } finally {
        if (Test-Path $TempSum) { Remove-Item -Force $TempSum }
    }

    Move-Item -Force $TempExe $TargetExe
}

Set-Content -Path $AliasCmd -Value "@echo off`r`n`"%~dp0code-reviewer.exe`" %*`r`n" -Encoding Ascii

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
$Parts = $UserPath -split ";" | Where-Object { $_ -ne "" }
if ($Parts -notcontains $InstallDir) {
    $NewPath = ($Parts + $InstallDir) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$env:Path;$InstallDir"
    Write-Host "Добавлено в пользовательский PATH: $InstallDir (перезапустите терминал при необходимости)"
}

Write-Host "Установлено: $TargetExe (и алиас $AliasCmd)"
& $TargetExe --version
