@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo ============================================================
echo   PDF Facile - build + installer (Windows)
echo ============================================================

rem ---------- 1. Python (installed automatically if missing)
call :find_python
if not defined PY (
  echo Python not found - installing the latest Python with winget...
  winget install -e --id Python.Python.3.14 --scope user --silent --accept-package-agreements --accept-source-agreements
  if errorlevel 1 winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
  call :find_python
)
if not defined PY (
  echo Could not find or install Python. Install it from https://www.python.org/downloads/ and run build.bat again.
  goto :err
)
echo Using Python: !PY!

rem ---------- 2. Virtual env + dependencies
if not exist ".venv\Scripts\python.exe" (
  "!PY!" -m venv .venv || goto :err
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install --upgrade -r requirements.txt pyinstaller || goto :err

rem ---------- 3. Executable
"%VPY%" -m PyInstaller --noconfirm --clean --windowed --name "PDF Facile" ^
  --icon icon.ico --add-data "icon.ico;." pdf_facile.py || goto :err
copy /y install.bat "dist\PDF Facile\" >nul
copy /y uninstall.bat "dist\PDF Facile\" >nul

rem ---------- 4. Installer (Inno Setup installed automatically if missing)
call :find_iscc
if not defined ISCC (
  echo Inno Setup not found - installing it with winget...
  winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
  call :find_iscc
)
if not defined ISCC (
  echo.
  echo Inno Setup is not available: no single-file installer was created.
  echo You can still copy "dist\PDF Facile" and run install.bat inside it.
  goto :end
)
"%VPY%" -c "open('dist/version.txt','w').write(open('pdf_facile.py',encoding='utf-8').read().split('APP_VERSION = ')[1].split(chr(10))[0].strip().strip(chr(34)))" || goto :err
set /p APPVER=<"dist\version.txt"
"!ISCC!" /Q /DAppVersion=!APPVER! installer.iss || goto :err

echo.
echo ============================================================
echo   DONE:  dist\Installer-PDF-Facile.exe   (version !APPVER!)
echo   Send this single file to your mother: double-click = install / update.
echo ============================================================
explorer /select,"%CD%\dist\Installer-PDF-Facile.exe"
goto :end

:find_python
set "PY="
for %%v in (314 313 312 311) do (
  if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"
  if not defined PY if exist "%ProgramFiles%\Python%%v\python.exe" set "PY=%ProgramFiles%\Python%%v\python.exe"
)
if not defined PY (
  for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%p"
)
if not defined PY (
  for /f "delims=" %%p in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PY set "PY=%%p"
)
exit /b 0

:find_iscc
set "ISCC="
for %%d in ("%ProgramFiles(x86)%\Inno Setup 6" "%ProgramFiles%\Inno Setup 6" "%LOCALAPPDATA%\Programs\Inno Setup 6") do (
  if not defined ISCC if exist "%%~d\ISCC.exe" set "ISCC=%%~d\ISCC.exe"
)
exit /b 0

:err
echo.
echo *** Build failed - see the messages above. ***
pause
exit /b 1

:end
pause
