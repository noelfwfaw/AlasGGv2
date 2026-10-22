@echo off
setlocal

set "_root=%~dp0"
set "_root=%_root:~0,-1%"
cd /d "%_root%"

set "_pyBin=%_root%\toolkit"
set "_GitBin=%_root%\toolkit\Git\mingw64\bin"
set "_adbBin=%_root%\toolkit\Lib\site-packages\adbutils\binaries"
set "PATH=%_root%\toolkit\alias;%_root%\toolkit\command;%_pyBin%;%_pyBin%\Scripts;%_GitBin%;%_adbBin%;%PATH%"

title Alas
python gui.py
if errorlevel 1 pause
