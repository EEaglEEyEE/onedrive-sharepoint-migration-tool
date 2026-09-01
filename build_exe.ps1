<#
.SYNOPSIS
    Baut onedrive-sharepoint-migration-tool.exe (Windows) per PyInstaller.
.DESCRIPTION
    Automatisiert die Schritte aus BUILD_WINDOWS.txt:
      1. Python pruefen
      2. PyInstaller installieren/aktualisieren
      3. rclone.exe besorgen (automatischer Download, falls nicht schon vorhanden)
      4. Build ueber onedrive-sharepoint-migration-tool.spec (Icon + Splash-Screen
         + console=False, siehe dort - Python/rclone landen dabei weiterhin in
         einer einzelnen .exe)
    Muss im selben Ordner wie onedrive-sharepoint-migration-tool.py/.spec liegen,
    oder per -ScriptPath auf die .py zeigen.
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
$AppName = "onedrive-sharepoint-migration-tool"
$SpecPath = Join-Path $ProjectDir "$AppName.spec"
if (-not (Test-Path $SpecPath)) {
    Fail "Konnte '$SpecPath' nicht finden - die .spec-Datei gehoert zum Repository und sollte neben der .py liegen."
}
$IconPath = Join-Path $ProjectDir "app_icon\icon.ico"
$SplashPath = Join-Path $ProjectDir "app_icon\splash.png"
if (-not (Test-Path $IconPath) -or -not (Test-Path $SplashPath)) {
    Fail "app_icon\icon.ico bzw. app_icon\splash.png fehlen - beide werden von der .spec referenziert."
}

# --- Icon/Splash-Dateigroesse anzeigen ---
# Der Projektordner liegt auch bei dir unter OneDrive-Sync: eine Datei kann
# dort als "nur online" markiert sein (OneDrive-Platzhalter, noch nicht
# vollstaendig heruntergeladen) - PyInstaller wuerde dann ohne Fehlermeldung
# eine leere/unvollstaendige Datei einlesen und die .exe bekommt einfach kein
# Icon, statt dass der Build fehlschlaegt. icon.ico sollte ca. 24 KB gross
# sein - deutlich kleiner (z.B. 0 KB) ist ein Hinweis genau darauf; in dem
# Fall die Datei im Explorer einmal oeffnen (erzwingt den Download) und das
# Skript erneut starten.
$IconSizeKB = [math]::Round((Get-Item $IconPath).Length / 1KB, 1)
$SplashSizeKB = [math]::Round((Get-Item $SplashPath).Length / 1KB, 1)
Write-Host "Icon: $IconPath ($IconSizeKB KB)"
Write-Host "Splash: $SplashPath ($SplashSizeKB KB)"
if ($IconSizeKB -lt 5) {
    Fail "icon.ico ist verdaechtig klein ($IconSizeKB KB, erwartet ca. 24 KB) - vermutlich eine OneDrive-Onlinedatei, die noch nicht heruntergeladen wurde. Datei im Explorer oeffnen (laedt sie vollstaendig herunter) und dieses Skript erneut starten."
}

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
# Bewusst NUR die eigenen Build-Ausgaben (build-Ordner/die .exe selbst)
# entfernen, NIE den ganzen dist-Ordner - dort koennen accounts.conf
# (dauerhaft gespeicherte Konten) sowie transiente Lauf-Configs eines evtl.
# gerade laufenden Kopiervorgangs liegen, die nicht stillschweigend geloescht
# werden duerfen. Die .spec-Datei selbst bleibt unangetastet (Teil des Repos,
# kein Wegwerf-Artefakt mehr).
Remove-Item (Join-Path $ProjectDir "build") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $ProjectDir "dist\$AppName.exe") -Force -ErrorAction SilentlyContinue

# --- Build ueber die .spec-Datei (Icon + Splash-Screen + console=False) ---
Write-Host "`nBaue $AppName.exe (kann ein bis zwei Minuten dauern)..."
Push-Location $ProjectDir
try {
    & $PythonCmd -m PyInstaller --noconfirm $SpecPath
    if ($LASTEXITCODE -ne 0) {
        Fail "PyInstaller-Build fehlgeschlagen (Exit Code $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}

$ExePath = Join-Path $ProjectDir "dist\$AppName.exe"
if (Test-Path $ExePath) {
    Write-Host "`nFertig: $ExePath" -ForegroundColor Green
    Write-Host "Diese Datei in den Zielordner kopieren (neben onedrive-sharepoint-migration-tool.bat)."
    Write-Host "`nFalls die .exe jetzt IMMER NOCH kein Icon zeigt (weder Datei noch Taskleiste):" -ForegroundColor Yellow
    Write-Host "  - Explorer neu starten (Task-Manager -> Windows-Explorer -> Neu starten)"
    Write-Host "  - Bitte melden: die 'Icon:'/'Splash:'-Zeilen von oben (Pfad + KB) mitschicken"
}
else {
    Fail "Build abgeschlossen, aber '$ExePath' wurde nicht gefunden - irgendetwas ist schiefgelaufen."
}
