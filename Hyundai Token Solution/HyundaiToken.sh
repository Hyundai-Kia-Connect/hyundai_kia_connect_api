#!/bin/bash
# ============================================================
#  Hyundai Token Generator - macOS / Linux
# ============================================================

clear
echo "============================================================"
echo "      🚗 Hyundai Token Generator - macOS / Linux"
echo "============================================================"
echo
echo "Please wait a few seconds. You will be redirected to Hyundai login page by Chrome."
echo

cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found!"
    exit 1
fi

python3 -m pip install --user selenium chromedriver-autoinstaller requests > /dev/null 2>&1
python3 hyundai_token.py

echo
echo "============================================================"
echo "✅ Process completed successfully!"
echo "You can use your refresh token as your password in your Hyundai integration in Home Assistant."
echo "============================================================"
read -p "Press ENTER to close..."
