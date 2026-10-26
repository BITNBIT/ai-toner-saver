@echo off
chcp 65001 >nul
for %%E in (pdf jpg jpeg jfif png bmp tif tiff webp gif) do reg delete "HKCU\Software\Classes\SystemFileAssociations\.%%E\shell\AITonerSaver" /f >nul 2>&1
reg delete "HKCU\Software\Classes\Directory\shell\AITonerSaver" /f >nul 2>&1
powershell -NoProfile -Command "foreach($p in @([Environment]::GetFolderPath('SendTo'), [Environment]::GetFolderPath('Desktop'))){ Remove-Item -LiteralPath (Join-Path $p 'AI Toner Saver.lnk') -ErrorAction SilentlyContinue }"
echo Voci del menu, "Invia a" e collegamento sul Desktop rimossi.
pause
