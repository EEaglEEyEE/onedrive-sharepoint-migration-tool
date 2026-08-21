@echo off
REM Doppelklick-Starter fuer Windows: oeffnet ein Konsolenfenster und fuehrt
REM das Script darin aus (Explorer fuehrt .bat-Dateien so per Doppelklick aus).
cd /d "%~dp0"

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
py OneDrive_Copy.py %*
goto :end

:run_python
python OneDrive_Copy.py %*
goto :end

:end
echo.
pause
