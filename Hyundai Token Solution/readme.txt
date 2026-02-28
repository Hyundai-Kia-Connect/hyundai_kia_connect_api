The only thing you need to do:

1. Download zip file and extract as folder. Make sure all the following files are in the same folder:
launch_hyundai_token.bat (main launcher to detect your system)
HyundaiToken.bat (for window system)
HyundaiToken.sh (for mac/Linux system)
hyundai_token.py

2.Run the script:
-Windows / Mac / Linux: Double-click launch_hyundai_token.bat. This script automatically detects your system and runs the correct commands.
-Alternative: If Python 3 is already installed, you can double-click hyundai_token.py directly. It will work the same as using launch_hyundai_token.bat.


3.Login to Hyundai:
-Chrome page will automatically open the Hyundai login page.
-Complete login with your Hyundai account and solve any reCAPTCHA if prompted. (After login, don't close chrome page yet)

Note: After you login with your Hyundai account, a weird message will appear on chrome page such as "result":"E","data":null,"message":"url is not defined". This message is not important. You can still keep open your chrome page and go back to terminal / CMD / PowerShell screen and press ENTER button.

4.Return to the terminal / CMD / PowerShell:
Press ENTER once login is complete.

5.Retrieve your token:

Your refresh token will appear in the terminal.
You can now use this token as the password in your Hyundai integration in Home Assistant.
