@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "%~dp0AVVIA.bat" --prepara || exit /b 1
set "VPYW=%~dp0.venv\Scripts\pythonw.exe"
set "APP=%~dp0app.py"
set "ICO=%SystemRoot%\System32\imageres.dll,-51"

rem Tasto destro su uno o piu' file (anche molti insieme: arrivano tutti nella stessa finestra).
for %%E in (pdf jpg jpeg jfif png bmp tif tiff webp gif) do (
 reg add "HKCU\Software\Classes\SystemFileAssociations\.%%E\shell\AITonerSaver" /ve /d "Ottimizza con AI Toner Saver" /f >nul
 reg add "HKCU\Software\Classes\SystemFileAssociations\.%%E\shell\AITonerSaver" /v Icon /d "%ICO%" /f >nul
 reg add "HKCU\Software\Classes\SystemFileAssociations\.%%E\shell\AITonerSaver" /v MultiSelectModel /d Player /f >nul
 reg add "HKCU\Software\Classes\SystemFileAssociations\.%%E\shell\AITonerSaver\command" /ve /d "\"%VPYW%\" \"%APP%\" \"%%1\"" /f >nul
)
rem Tasto destro su una cartella: carica tutti i documenti contenuti.
reg add "HKCU\Software\Classes\Directory\shell\AITonerSaver" /ve /d "Ottimizza documenti con AI Toner Saver" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\AITonerSaver" /v Icon /d "%ICO%" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\AITonerSaver\command" /ve /d "\"%VPYW%\" \"%APP%\" \"%%1\"" /f >nul

rem "Invia a" e collegamento sul Desktop.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$s=New-Object -ComObject WScript.Shell;" ^
 "foreach($p in @([Environment]::GetFolderPath('SendTo'), [Environment]::GetFolderPath('Desktop'))){" ^
 " $l=$s.CreateShortcut((Join-Path $p 'AI Toner Saver.lnk'));" ^
 " $l.TargetPath='%VPYW%'; $l.Arguments='\"%APP%\"'; $l.WorkingDirectory='%~dp0';" ^
 " $l.IconLocation='%SystemRoot%\System32\imageres.dll,51'; $l.Description='AI Toner Saver 1.7'; $l.Save() }"

echo.
echo Fatto. Ora puoi:
echo  - selezionare uno o piu' file, tasto destro ^> Mostra altre opzioni ^> Ottimizza con AI Toner Saver
echo  - tasto destro su una cartella per caricare tutti i documenti
echo  - tasto destro ^> Invia a ^> AI Toner Saver  (comodo con tanti file)
echo  - aprire il programma dal collegamento sul Desktop
pause
