@echo off
setlocal enabledelayedexpansion
title Studio Montage - installation
chcp 65001 >nul

set "SRC=%~dp0"
set "DEST=%USERPROFILE%\StudioMontage"

echo ============================================
echo  Installation du Studio Montage (Windows)
echo ============================================
echo.

REM --- 1. Node present ? -----------------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
  echo [X] Node.js est introuvable sur cet ordinateur.
  echo.
  echo     Installe-le depuis https://nodejs.org  ^(gros bouton "LTS"^),
  echo     ouvre le fichier .msi telecharge, clique Suivant jusqu'au bout,
  echo     puis relance ce fichier d'installation.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('node -v') do set "NODEV=%%v"
echo [OK] Node.js %NODEV% trouve.

REM --- 2. Copier le studio ---------------------------------------------------
echo [..] Copie du studio dans "%DEST%"
if not exist "%DEST%" mkdir "%DEST%"
robocopy "%SRC%app"   "%DEST%\app"   /E /NFL /NDL /NJH /NJS /NP >nul
robocopy "%SRC%seeds" "%DEST%\seeds" /E /NFL /NDL /NJH /NJS /NP >nul
copy /y "%SRC%windows\StudioMontage.vbs" "%DEST%\StudioMontage.vbs" >nul
echo [OK] Fichiers copies.

REM --- 3. Raccourci sur le Bureau -------------------------------------------
echo [..] Creation du raccourci "Studio Montage" sur le Bureau
set "SM_DEST=%DEST%"
set "SM_VBS=%DEST%\StudioMontage.vbs"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); $l=$ws.CreateShortcut((Join-Path $d 'Studio Montage.lnk')); $l.TargetPath=(Join-Path $env:SystemRoot 'System32\wscript.exe'); $l.Arguments=('\"'+$env:SM_VBS+'\"'); $l.WorkingDirectory=$env:SM_DEST; $l.IconLocation=((Join-Path $env:SystemRoot 'System32\shell32.dll')+',137'); $l.Description='Studio Montage'; $l.Save()"
if errorlevel 1 (
  echo [!] Le raccourci n'a pas pu etre cree automatiquement, mais le studio est installe.
) else (
  echo [OK] Raccourci cree sur le Bureau.
)

REM --- 4. Premier lancement --------------------------------------------------
echo [..] Ouverture du studio
if exist "%DEST%\.port" del "%DEST%\.port" >nul 2>nul
start "" wscript.exe "%DEST%\StudioMontage.vbs"

echo.
echo ============================================
echo  Termine.
echo ============================================
echo Le studio s'ouvre dans ton navigateur.
echo Une icone "Studio Montage" est sur ton Bureau : double-clique dessus quand tu veux.
echo.
echo (Pour les vignettes des videos, ffmpeg est optionnel : https://www.gyan.dev/ffmpeg/builds/ )
echo.
pause
