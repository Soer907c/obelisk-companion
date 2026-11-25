@echo off
cd /d "%~dp0"
if /i "%~1"=="--shortcut" (
    if not exist "assets\obelisk-companion.ico" (
        echo The shortcut icon is missing. Download the current version from GitHub.
        exit /b 1
    )
    powershell.exe -NoProfile -Command "$root = (Get-Location).Path; $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $root 'Start Obelisk.lnk')); $link.TargetPath = Join-Path $root 'Start Obelisk.cmd'; $link.WorkingDirectory = $root; $link.IconLocation = (Join-Path $root 'assets\obelisk-companion.ico') + ',0'; $link.Save()"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" app.py
    exit /b
)
start "" pythonw.exe app.py
