@echo off
cd /d "%~dp0"
py -m pip install --upgrade pyinstaller PyQt6 psutil winsdk
if errorlevel 1 goto fail
py -m PyInstaller --noconfirm --clean --onefile --noconsole --name "Panda Island" --icon icon.ico --version-file version.txt --add-data "icon.png;." --collect-submodules winsdk panda_island.pyw
if errorlevel 1 goto fail
set "target=%LOCALAPPDATA%\Panda Island"
mkdir "%target%" 2>nul
taskkill /f /im "Panda Island.exe" >nul 2>&1
copy /y "dist\Panda Island.exe" "%target%\" >nul
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs')+'\Panda Island.lnk'); $s.TargetPath='%target%\Panda Island.exe'; $s.WorkingDirectory='%target%'; $s.IconLocation='%target%\Panda Island.exe'; $s.Save()"
start "" "%target%\Panda Island.exe"
exit /b 0
:fail
echo Build failed
pause
exit /b 1
