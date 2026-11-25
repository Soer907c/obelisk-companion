@echo off
cd /d "%~dp0"
if /i "%~1"=="--shortcut" (
    powershell.exe -NoProfile -Command "$ErrorActionPreference = 'Stop'; $root = (Get-Location).Path; $launcherDir = Join-Path $env:LOCALAPPDATA 'ObeliskCompanion\Launcher'; [IO.Directory]::CreateDirectory($launcherDir) | Out-Null; $icon = Join-Path $root 'assets\obelisk-companion.ico'; if (!(Test-Path -LiteralPath $icon)) { $encoded = [IO.File]::ReadAllText($icon + '.b64'); $icon = Join-Path $launcherDir 'obelisk-companion.ico'; [IO.File]::WriteAllBytes($icon, [Convert]::FromBase64String($encoded)) }; $shell = New-Object -ComObject WScript.Shell; function Save-Link($folder) { $path = Join-Path $folder 'Start Obelisk.lnk'; $link = $shell.CreateShortcut($path); $link.TargetPath = Join-Path $root 'Start Obelisk.cmd'; $link.WorkingDirectory = $root; $link.IconLocation = $icon + ',0'; $link.Save(); if (!(Test-Path -LiteralPath $path)) { throw 'Shortcut was not saved.' }; Write-Output $path }; try { Save-Link $root } catch { Save-Link $launcherDir }"
    if errorlevel 1 exit /b 1
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" app.py
    exit /b
)
start "" pythonw.exe app.py

