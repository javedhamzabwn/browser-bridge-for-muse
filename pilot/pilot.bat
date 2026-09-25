@echo off
setlocal
if exist "C:\Python314\python.exe" (
    set "PYCMD=C:\Python314\python.exe"
) else (
    set "PYCMD=python"
)
"%PYCMD%" "%~dp0pilot.py" %*
