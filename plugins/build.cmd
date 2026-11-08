@echo off
rem Build a Foundry plugin DLL: build.cmd <plugin folder> [mods folder to install into]
rem Needs Build Tools for Visual Studio 2022 and the port checkout in external\.
setlocal
set ROOT=%~dp0..
set PLUGIN=%~1
if "%PLUGIN%"=="" (echo usage: build.cmd ^<plugin folder^> [mods folder] & exit /b 1)
set SDK=%ROOT%\external\Mercenaries-Recompiled\ports\mercenaries\sdk
set OUT=%ROOT%\plugins\out\%~n1
set VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat
if not exist "%VCVARS%" (echo Build Tools not found at "%VCVARS%" & exit /b 1)
if not exist "%OUT%" mkdir "%OUT%"
pushd "%ROOT%"
python -m mercfoundry funcmap header -o "%OUT%\mercfoundry_functions.h" || (popd & exit /b 1)
popd
call "%VCVARS%" >nul || exit /b 1
for %%f in ("%PLUGIN%\*.c") do set SRC=%%f
for /f "tokens=2 delims==" %%l in ('findstr /b /i "Library=" "%PLUGIN%\mod.ini"') do set DLL=%%l
cl /nologo /O2 /W3 /LD /I"%SDK%" /I"%OUT%" "%SRC%" /Fo"%OUT%\\" /Fe"%OUT%\%DLL%" /link /NOLOGO || exit /b 1
copy /y "%PLUGIN%\mod.ini" "%OUT%\" >nul
echo built %OUT%\%DLL%
set "DEST=%~2"
if not defined DEST goto :eof
if not exist "%DEST%" mkdir "%DEST%"
copy /y "%OUT%\%DLL%" "%DEST%\" >nul
copy /y "%OUT%\mod.ini" "%DEST%\" >nul
echo installed into %DEST%
