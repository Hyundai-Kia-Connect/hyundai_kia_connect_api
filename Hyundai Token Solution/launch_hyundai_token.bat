@echo off
title Hyundai Token Launcher
color 0A
cls

echo ============================================================
echo  Hyundai Token Generator - Universal Launcher
echo ============================================================
echo.

REM Detect if Windows
ver | findstr /i "Windows" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo Detected platform: Windows 🪟
    echo.
    if exist "HyundaiToken.bat" (
        call HyundaiToken.bat
    ) else (
        echo ❌ HyundaiToken.bat not found in this folder!
        pause
    )
    exit /b
)

REM Detect if macOS/Linux (by checking bash existence)
echo Detected platform: macOS/Linux 🐧
echo.

if exist "HyundaiToken.sh" (
    echo Running HyundaiToken.sh...
    bash HyundaiToken.sh
) else (
    echo ❌ HyundaiToken.sh not found in this folder!
    echo Please make sure it's in the same directory as this launcher.
fi

echo.
pause
exit /b
