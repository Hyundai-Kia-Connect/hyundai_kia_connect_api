@echo off
title Hyundai Token Generator
color 0A
cls

echo ============================================================
echo       Hyundai Token Generator - Windows
echo ============================================================
echo.
echo Please wait a few seconds. You will be redirected to Hyundai login page by Chrome.
echo.

cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Python not found! Please install it from https://www.python.org.
    pause
    exit /b
)

echo How would you like to set up dependencies?
echo.
echo   [1] Install dependencies globally (pip install)
echo   [2] Use a virtual environment (venv) [RECOMMENDED]
echo   [3] Skip - dependencies are already installed
echo.
set /p CHOICE="Your choice (1/2/3): "

if "%CHOICE%"=="1" (
    echo.
    echo Installing dependencies globally...
    python -m pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo.
        echo ERROR: Failed to install dependencies. Try option 2 (venv) instead.
        pause
        exit /b
    )
    python -m playwright install chromium
) else if "%CHOICE%"=="2" (
    echo.
    if not exist "venv" (
        echo Creating virtual environment...
        python -m venv venv
    )
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
    echo Installing dependencies in venv...
    pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo.
        echo ERROR: Failed to install dependencies.
        pause
        exit /b
    )
    python -m playwright install chromium
) else (
    echo.
    echo Skipping dependency installation.
)

echo.
python hyundai_token.py

echo.
echo ============================================================
echo Process completed successfully!
echo You can use your refresh token as your password in your Hyundai integration in Home Assistant.
echo ============================================================
pause
