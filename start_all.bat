@echo off
title Astra Assistant 2.0 - Launcher
echo ========================================================
echo        Starting Astra Assistant (Server + Local Executor)
echo ========================================================
echo.

echo [1/2] Starting Astra Server (FastAPI + Web UI)...
start "Astra Server" cmd /k "python run.py"

timeout /t 2 >nul

echo [2/2] Starting Astra Local PC Executor...
start "Astra Local Executor" cmd /k "python local_executor.py"

echo.
echo All components started!
echo - Astra Web UI: Check the browser window that just opened.
echo - Local Executor: Connected in background to handle PC controls.
echo.
pause

