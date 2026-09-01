@echo off
REM Doppelklick-Starter fuer Windows - startet standardmaessig die GRAFISCHE
REM Oberflaeche (kein Argument = GUI). Fuer die Terminal-Oberflaeche (CLI)
REM stattdessen "onedrive-sharepoint-migration-tool.exe --cli" bzw. diese
REM .bat-Datei mit --cli als Argument aufrufen. Wenn
REM dist\onedrive-sharepoint-migration-tool.exe existiert - eine mit
REM PyInstaller gebaute, eigenstaendige Binary mit Python UND rclone bereits
REM eingebettet - wird die direkt verwendet, kein separates Python/winget/
REM rclone-Setup mehr noetig. Build-Ausgaben liegen immer unter dist/, nie
REM direkt im Projektordner (siehe build_exe.ps1). Sonst wie gewohnt Fallback
REM auf py/python (mit Auto-Install). Im GUI-Modus blendet die Binary ihr
REM eigenes Konsolenfenster selbststaendig aus (siehe onedrive-sharepoint-
REM migration-tool.py) - im --cli-Modus bleibt es sichtbar.
cd /d "%~dp0"

if exist "dist\onedrive-sharepoint-migration-tool.exe" (
    dist\onedrive-sharepoint-migration-tool.exe %*
    goto :end
)

where py >nul 2>nul
if %errorlevel%==0 goto :run_py

where python >nul 2>nul
if %errorlevel%==0 goto :run_python

echo Python wurde nicht gefunden - versuche automatische Installation via winget...
where winget >nul 2>nul
if %errorlevel% neq 0 (
    echo winget nicht verfuegbar. Bitte Python 3 manuell installieren: https://www.python.org/downloads/
    goto :end
)

winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
REM winget schreibt den neuen PATH-Eintrag nur in die Registry - dieses bereits
REM laufende Konsolenfenster sieht ihn erst nach einem Neustart. Der von winget
REM angelegte Alias-Ordner wird deshalb direkt fuer diese Sitzung ergaenzt.
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"

where py >nul 2>nul
if %errorlevel%==0 goto :run_py

where python >nul 2>nul
if %errorlevel%==0 goto :run_python

echo Python-Installation fehlgeschlagen. Bitte manuell installieren: https://www.python.org/downloads/
goto :end

:run_py
py onedrive-sharepoint-migration-tool.py %*
goto :end

:run_python
python onedrive-sharepoint-migration-tool.py %*
goto :end

:end
echo.
pause
