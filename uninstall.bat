@echo off
setlocal
taskkill /im "PDF Facile.exe" /f >nul 2>nul
del "%USERPROFILE%\Desktop\PDF Facile.lnk" >nul 2>nul
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Desktop')) 'PDF Facile.lnk'); Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Programs')) 'PDF Facile.lnk')"
reg delete "HKCU\Software\Classes\PDFFacile.Document" /f >nul 2>nul
reg delete "HKCU\Software\Classes\.pdf\OpenWithProgids" /v PDFFacile.Document /f >nul 2>nul
reg delete "HKCU\Software\Classes\Applications\PDF Facile.exe" /f >nul 2>nul
echo PDF Facile shortcuts and file association removed.
echo You can now delete the folder "%LOCALAPPDATA%\Programs\PDF Facile".
echo (Saved signatures are in "%APPDATA%\PDFFacile\PDF Facile\signatures".)
pause
