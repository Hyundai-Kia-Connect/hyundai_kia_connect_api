The only thing you need to do:

1. Download zip file and extract as folder. Make sure all the following files are in the same folder:
launch_hyundai_token.bat (main launcher to detect your system)
HyundaiToken.bat (for window system)
HyundaiToken.sh (for mac/Linux system)
hyundai_token.py

2. Run the script:
-Windows / Mac / Linux: Double-click launch_hyundai_token.bat. This script automatically detects your system and runs the correct commands.
-Alternative: If Python 3 is already installed, you can double-click hyundai_token.py directly. It will work the same as using launch_hyundai_token.bat.

The script will ask you how to install dependencies:
  [1] Install globally (pip install)
  [2] Use a virtual environment (venv) [RECOMMENDED]
  [3] Skip (if you already installed them)

Option 2 is recommended because it keeps the dependencies isolated from your system Python.
If you choose option 2, the script will automatically create and activate the venv for you.

If you prefer to set up the venv manually beforehand:

  Windows (CMD / PowerShell):
    python -m venv venv
    venv\Scripts\activate
    pip install -r requirements.txt

  Mac / Linux:
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

To leave the virtual environment later, simply run:
    deactivate


4. Login to Hyundai:
-Chrome page will automatically open the Hyundai login page.
-Complete login with your Hyundai account and solve any reCAPTCHA if prompted. (After login, don't close chrome page yet)

Note: After you login with your Hyundai account, a weird message will appear on chrome page such as "result":"E","data":null,"message":"url is not defined". This message is not important. You can still keep open your chrome page and go back to terminal / CMD / PowerShell screen and press ENTER button.

5. Return to the terminal / CMD / PowerShell:
Press ENTER once login is complete.

6. Retrieve your token:

Your refresh token will appear in the terminal.
You can now use this token as the password in your Hyundai integration in Home Assistant.
