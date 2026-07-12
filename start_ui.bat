@echo off
chcp 65001 >nul
title MoA Gateway Pro — Desktop UI
cd /d "%~dp0"
python start_ui.py %*
pause
