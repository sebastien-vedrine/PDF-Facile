@echo off
setlocal
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\PDF Facile"
set "EXE=%DEST%\PDF Facile.exe"

echo Installing PDF Facile to "%DEST%"...
taskkill /im "PDF Facile.exe" /f >nul 2>nul
robocopy "%SRC%." "%DEST%" /E /NFL /NDL /NJH /NJS /NP /XF install.bat >nul
if %ERRORLEVEL% GEQ 8 (
  echo Copy failed.
  pause & exit /b 1
)

rem Shortcuts: desktop + Start menu
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'PDF Facile.lnk')); $s.TargetPath=$env:LOCALAPPDATA+'\Programs\PDF Facile\PDF Facile.exe'; $s.WorkingDirectory=$env:LOCALAPPDATA+'\Programs\PDF Facile'; $s.Description='Lire, remplir et signer des PDF'; $s.Save() }"

rem Register in "Open with" for .pdf (current user only, no admin needed)
reg add "HKCU\Software\Classes\PDFFacile.Document" /ve /d "Document PDF" /f >nul
reg add "HKCU\Software\Classes\PDFFacile.Document\DefaultIcon" /ve /d "\"%EXE%\",0" /f >nul
reg add "HKCU\Software\Classes\PDFFacile.Document\shell\open\command" /ve /d "\"%EXE%\" \"%%1\"" /f >nul
reg add "HKCU\Software\Classes\.pdf\OpenWithProgids" /v PDFFacile.Document /t REG_NONE /f >nul
reg add "HKCU\Software\Classes\Applications\PDF Facile.exe\SupportedTypes" /v .pdf /t REG_SZ /d "" /f >nul
reg add "HKCU\Software\Classes\Applications\PDF Facile.exe\shell\open\command" /ve /d "\"%EXE%\" \"%%1\"" /f >nul

echo.
echo Done! A "PDF Facile" shortcut is on the desktop.
echo To open PDFs with it by default: right-click a PDF ^> Open with ^> Choose another app
echo ^> PDF Facile ^> tick "Always".
pause
