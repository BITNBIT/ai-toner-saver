@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Versione portatile: Python e librerie sono gia' nella cartella "runtime".
if exist "%~dp0runtime\pythonw.exe" (
 if /i "%~1"=="--prepara" exit /b 0
 start "" "%~dp0runtime\pythonw.exe" "%~dp0app.py" %*
 exit /b 0
)

set "VPY=%~dp0.venv\Scripts\python.exe"
set "VPYW=%~dp0.venv\Scripts\pythonw.exe"

rem Un ambiente copiato da un altro PC non funziona: in quel caso si rifa'.
if exist "%VPY%" "%VPY%" -c "import sys" >nul 2>nul || (
 if exist ".venv" echo Ambiente Python non valido su questo PC: lo ricreo...
 if exist ".venv" rmdir /s /q ".venv"
)
if not exist "%VPY%" call :crea || goto :errore
rem Le librerie si installano solo la prima volta o quando cambia requirements.txt.
fc /b "requirements.txt" ".venv\requisiti_installati.txt" >nul 2>nul
if errorlevel 1 call :librerie || goto :errore
if /i "%~1"=="--prepara" exit /b 0

start "" "%VPYW%" "%~dp0app.py" %*
exit /b 0

:crea
echo Prima apertura: preparo l'ambiente Python (solo questa volta)...
py -3.11 -m venv .venv >nul 2>nul && exit /b 0
py -3 -m venv .venv >nul 2>nul && exit /b 0
python -m venv .venv && exit /b 0
echo Python non trovato. Installa Python 3.11 da python.org oppure scarica la versione portatile.
exit /b 1

:librerie
echo Installazione librerie (alcuni minuti, solo la prima volta)...
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt || exit /b 1
copy /y requirements.txt ".venv\requisiti_installati.txt" >nul
exit /b 0

:errore
echo.
echo Preparazione non riuscita. Controlla la connessione a internet e riprova.
pause
exit /b 1
