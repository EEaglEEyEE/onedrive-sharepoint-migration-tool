<#
.SYNOPSIS
    Baut onedrive-sharepoint-migration-tool.exe (Windows) per PyInstaller.
.DESCRIPTION
    Automatisiert die Schritte aus BUILD_WINDOWS.txt:
      1. Python pruefen
      2. PyInstaller installieren/aktualisieren
      3. rclone.exe besorgen (automatischer Download, falls nicht schon vorhanden)
      4. Build starten (Python + rclone werden in eine einzelne .exe eingebettet)
    Muss im selben Ordner wie onedrive-sharepoint-migration-tool.py liegen, oder
    per -ScriptPath auf die .py zeigen.
.EXAMPLE
    .\build_exe.ps1
.EXAMPLE
    .\build_exe.ps1 -ScriptPath C:\Pfad\zu\onedrive-sharepoint-migration-tool.py
#>

param(
    [string]$ScriptPath = (Join-Path $PSScriptRoot "onedrive-sharepoint-migration-tool.py")
)

$ErrorActionPreference = "Stop"

function Fail($Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $ScriptPath)) {
    Fail "Konnte '$ScriptPath' nicht finden. Mit -ScriptPath auf den Pfad zur .py-Datei zeigen."
}
$ProjectDir = Split-Path -Parent (Resolve-Path $ScriptPath)
$ScriptName = Split-Path -Leaf $ScriptPath

# --- Python finden ---
$PythonCmd = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $PythonCmd = $candidate
        break
    }
}
if (-not $PythonCmd) {
    Fail "Python wurde nicht gefunden. Bitte zuerst installieren: https://www.python.org/downloads/ (beim Installer 'Add python.exe to PATH' anhaken) und dieses Skript erneut ausfuehren."
}
Write-Host "Verwende Python: $PythonCmd"

# --- tkinter-Verfuegbarkeit pruefen (Voraussetzung fuer die GUI) ---
& $PythonCmd -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail "Das gefundene Python ($PythonCmd) hat kein funktionierendes tkinter (fuer die GUI benoetigt). Bitte die offizielle Python-Installation von https://www.python.org/downloads/ verwenden (bringt tkinter von Haus aus mit) und dieses Skript erneut ausfuehren."
}

# --- PyInstaller + customtkinter installieren/aktualisieren ---
Write-Host "`nInstalliere/aktualisiere PyInstaller und customtkinter..."
& $PythonCmd -m pip install --quiet --upgrade pyinstaller customtkinter
if ($LASTEXITCODE -ne 0) {
    Fail "'pip install pyinstaller customtkinter' fehlgeschlagen (Exit Code $LASTEXITCODE)."
}

# --- rclone.exe sicherstellen ---
$RclonePath = Join-Path $ProjectDir "rclone.exe"
if (-not (Test-Path $RclonePath)) {
    Write-Host "`nrclone.exe nicht gefunden - lade aktuelle Version herunter..."
    $ZipPath = Join-Path $env:TEMP "rclone-current-windows-amd64.zip"
    $ExtractDir = Join-Path $env:TEMP "rclone-extract-$(Get-Random)"
    try {
        Invoke-WebRequest -Uri "https://downloads.rclone.org/rclone-current-windows-amd64.zip" -OutFile $ZipPath
        Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force
        $FoundExe = Get-ChildItem -Path $ExtractDir -Filter "rclone.exe" -Recurse | Select-Object -First 1
        if (-not $FoundExe) {
            Fail "rclone.exe wurde im heruntergeladenen Archiv nicht gefunden."
        }
        Copy-Item $FoundExe.FullName $RclonePath -Force
        Write-Host "rclone.exe nach '$RclonePath' kopiert."
    }
    catch {
        Fail "Download/Entpacken von rclone fehlgeschlagen: $_`nAlternativ manuell von https://rclone.org/downloads/ laden und als '$RclonePath' ablegen."
    }
    finally {
        Remove-Item $ZipPath -ErrorAction SilentlyContinue
        Remove-Item $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host "`nrclone.exe bereits vorhanden: $RclonePath"
}

# --- Alte Build-Artefakte aufraeumen ---
foreach ($dir in @("build", "dist")) {
    Remove-Item (Join-Path $ProjectDir $dir) -Recurse -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $ProjectDir "*.spec") -Force -ErrorAction SilentlyContinue

# --- Build ---
Write-Host "`nBaue onedrive-sharepoint-migration-tool.exe (kann ein bis zwei Minuten dauern)..."
Push-Location $ProjectDir
try {
    & $PythonCmd -m PyInstaller --onefile --console --name onedrive-sharepoint-migration-tool --add-binary "rclone.exe;." --collect-data customtkinter $ScriptName
    if ($LASTEXITCODE -ne 0) {
        Fail "PyInstaller-Build fehlgeschlagen (Exit Code $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}

$ExePath = Join-Path $ProjectDir "dist\onedrive-sharepoint-migration-tool.exe"
if (Test-Path $ExePath) {
    Write-Host "`nFertig: $ExePath" -ForegroundColor Green
    Write-Host "Diese Datei in den Zielordner kopieren (neben onedrive-sharepoint-migration-tool.bat)."
}
else {
    Fail "Build abgeschlossen, aber '$ExePath' wurde nicht gefunden - irgendetwas ist schiefgelaufen."
}
