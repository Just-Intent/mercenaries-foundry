@echo off
rem Build a Foundry plugin DLL: build.cmd <plugin folder> [mods folder to install into]
rem Needs Build Tools for Visual Studio 2022 and the port checkout in external\
rem (or set MERC_PORT to the port checkout's folder).
setlocal
set ROOT=%~dp0..
set PLUGIN=%~1
if "%PLUGIN%"=="" (echo usage: build.cmd ^<plugin folder^> [mods folder] & exit /b 1)
if not defined MERC_PORT set MERC_PORT=%ROOT%\external\Mercenaries-Recompiled
set SDK=%MERC_PORT%\ports\mercenaries\sdk
if not exist "%SDK%\mercenaries_plugin.h" (echo The port's plugin SDK isn't at "%SDK%"; set MERC_PORT & exit /b 1)
set OUT=%ROOT%\plugins\out\%~n1
set VS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools
set VCVARS=%VS%\VC\Auxiliary\Build\vcvars64.bat
if not exist "%VCVARS%" (echo Build Tools not found at "%VCVARS%" & exit /b 1)
if not exist "%OUT%" mkdir "%OUT%"
pushd "%ROOT%"
python -m mercfoundry funcmap header -o "%OUT%\mercfoundry_functions.h" || (popd & exit /b 1)
popd
call "%VCVARS%" >nul 2>&1
rem vcvars64 sometimes leaves cl off PATH (e.g. when vswhere is missing):
rem then set the compiler and Windows SDK paths directly.
where cl >nul 2>&1 && goto :build
set MSVC=
for /d %%d in ("%VS%\VC\Tools\MSVC\*") do set MSVC=%%~d
set KITS=C:\Program Files (x86)\Windows Kits\10
set KITVER=
for /d %%d in ("%KITS%\Include\10.*") do set KITVER=%%~nxd
if not defined MSVC (echo no MSVC tools under "%VS%\VC\Tools\MSVC" & exit /b 1)
if not defined KITVER (echo no Windows SDK under "%KITS%\Include" & exit /b 1)
set "PATH=%MSVC%\bin\Hostx64\x64;%KITS%\bin\%KITVER%\x64;%PATH%"
set "INCLUDE=%MSVC%\include;%KITS%\Include\%KITVER%\ucrt;%KITS%\Include\%KITVER%\um;%KITS%\Include\%KITVER%\shared"
set "LIB=%MSVC%\lib\x64;%KITS%\Lib\%KITVER%\ucrt\x64;%KITS%\Lib\%KITVER%\um\x64"
where cl >nul 2>&1 || (echo cl not found in "%MSVC%\bin\Hostx64\x64" & exit /b 1)
:build
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
