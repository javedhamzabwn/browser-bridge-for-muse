@echo off
title Browser Bridge for Muse
cd /d "%~dp0"

REM Find Python: prefer "python", fall back to C:\Python314 or "py" launcher
set "PYCMD=python"
if exist "C:\Python314\python.exe" (
    set "PYCMD=C:\Python314\python.exe"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        where py >nul 2>nul
        if errorlevel 1 (
            echo [ERROR] Python not found. Please install Python 3.10+
            pause
            exit /b 1
        ) else (
            set "PYCMD=py -3"
        )
    )
)

%PYCMD% browser_bridge_muse.py %*
echo.
pause
