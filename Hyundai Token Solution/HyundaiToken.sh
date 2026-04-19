#!/bin/bash
# ============================================================
#  Hyundai Token Generator - macOS / Linux
# ============================================================

clear
echo "============================================================"
echo "      Hyundai Token Generator - macOS / Linux"
echo "============================================================"
echo
echo "Please wait a few seconds. You will be redirected to Hyundai login page by Chrome."
echo

cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "Python3 not found!"
    exit 1
fi

echo "How would you like to set up dependencies?"
echo
echo "  [1] Install dependencies globally (pip install)"
echo "  [2] Use a virtual environment (venv) [RECOMMENDED]"
echo "  [3] Skip - dependencies are already installed"
echo
read -p "Your choice (1/2/3): " CHOICE

case "$CHOICE" in
    1)
        echo
        echo "Installing dependencies globally..."
        python3 -m pip install -r requirements.txt
        if [ $? -ne 0 ]; then
            echo
            echo "ERROR: Failed to install dependencies. Try option 2 (venv) instead."
            read -p "Press ENTER to exit..."
            exit 1
        fi
        python3 -m playwright install chromium
        ;;
    2)
        echo
        if [ ! -d "venv" ]; then
            echo "Creating virtual environment..."
            python3 -m venv venv
        fi
        echo "Activating virtual environment..."
        source venv/bin/activate
        echo "Installing dependencies in venv..."
        pip install -r requirements.txt
        if [ $? -ne 0 ]; then
            echo
            echo "ERROR: Failed to install dependencies."
            read -p "Press ENTER to exit..."
            exit 1
        fi
        python -m playwright install chromium
        ;;
    3)
        echo
        echo "Skipping dependency installation."
        ;;
    *)
        echo
        echo "Invalid choice. Skipping dependency installation."
        ;;
esac

echo
python3 hyundai_token.py

echo
echo "============================================================"
echo "Process completed successfully!"
echo "You can use your refresh token as your password in your Hyundai integration in Home Assistant."
echo "============================================================"
read -p "Press ENTER to close..."
