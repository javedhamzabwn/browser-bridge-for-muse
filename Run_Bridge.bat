@echo off
title ixBrowser All-In-One Bridge
cd /d "%~dp0"

REM Find Python: prefer "python", fall back to the "py" launcher.
where python >nul 2>nul
if %errorlevel%==0 (
    set "PYCMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PYCMD=py -3"
    ) else (
        echo.
        echo [XX] Python was not found.
        echo      Install Python 3.10+ from https://www.python.org/downloads/
        echo      IMPORTANT: tick "Add python.exe to PATH" during install.
        echo.
        pause
        exit /b 1
    )
)

%PYCMD% ixbridge_all_in_one.py
echo.
echo Window will stay open so you can read any message above.
pause >nul
