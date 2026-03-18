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
    echo ❌ Python not found! Please install it from https://www.python.org.
    pause
    exit /b
)

python -m pip install --user selenium chromedriver-autoinstaller requests >nul 2>nul
python hyundai_token.py

echo.
echo ============================================================
echo ✅ Process completed successfully!
echo You can use your refresh token as your password in your Hyundai integration in Home Assistant.
echo ============================================================
pause
