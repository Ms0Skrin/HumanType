@echo off
rem Builds dist\HumanType (PyInstaller) and installer\HumanType-Setup-<version>.exe (Inno Setup 6).
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe

for /f %%v in ('%PY% -c "from humantype.paths import VERSION; print(VERSION)"') do set VER=%%v
echo === HumanType %VER%

%PY% -m PyInstaller --noconfirm --clean HumanType.spec || goto :fail

if not exist "%ISCC%" (
    echo Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup
    echo The program itself is ready in dist\HumanType
    exit /b 1
)
"%ISCC%" /DAppVersion=%VER% installer.iss || goto :fail
echo === Done: installer\HumanType-Setup-%VER%.exe
exit /b 0

:fail
echo === Build failed
exit /b 1
