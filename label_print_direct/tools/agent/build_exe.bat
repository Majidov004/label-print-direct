@echo off
rem Etiket cap agentini tek exe kimi yigir (PyInstaller).
rem Telebler:  py -3 -m pip install pywin32 customtkinter pyinstaller
cd /d "%~dp0"
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name LabelPrintAgent --icon "%~dp0agent.ico" --add-data "%~dp0agent.ico;." ^
  --collect-all customtkinter --collect-all pystray --hidden-import pystray._win32 --hidden-import win32timezone ^
  --distpath "%~dp0dist" --workpath "%TEMP%\label_agent_build" --specpath "%TEMP%\label_agent_build" ^
  label_print_agent.py
echo.
echo Hazir: %~dp0dist\LabelPrintAgent.exe
pause
