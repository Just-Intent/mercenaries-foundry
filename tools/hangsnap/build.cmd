@echo off
rem Build hangsnap.exe into tools\hangsnap\out\.
rem Needs Build Tools for Visual Studio 2022 (C++ workload) and a Windows 10/11 SDK.
rem Sets INCLUDE, LIB and PATH itself instead of calling vcvars64.bat, which
rem doesn't always put cl on PATH.
setlocal
set BT=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools
set KIT=C:\Program Files (x86)\Windows Kits\10
if not exist "%BT%\VC\Tools\MSVC" (echo Build Tools not found at "%BT%" & exit /b 1)
if not exist "%KIT%\Include" (echo Windows SDK not found at "%KIT%" & exit /b 1)

rem Newest MSVC toolset and newest SDK that has windows.h
set VC=
for /f "delims=" %%v in ('dir /b /ad /o:n "%BT%\VC\Tools\MSVC"') do set VC=%BT%\VC\Tools\MSVC\%%v
set KV=
for /f "delims=" %%v in ('dir /b /ad /o:n "%KIT%\Include"') do if exist "%KIT%\Include\%%v\um\windows.h" set KV=%%v
if not defined VC (echo no MSVC toolset under "%BT%\VC\Tools\MSVC" & exit /b 1)
if not defined KV (echo no Windows SDK with windows.h under "%KIT%\Include" & exit /b 1)

set INCLUDE=%VC%\include;%KIT%\Include\%KV%\ucrt;%KIT%\Include\%KV%\um;%KIT%\Include\%KV%\shared
set LIB=%VC%\lib\x64;%KIT%\Lib\%KV%\ucrt\x64;%KIT%\Lib\%KV%\um\x64
set PATH=%VC%\bin\Hostx64\x64;%KIT%\bin\%KV%\x64;%SystemRoot%\system32;%SystemRoot%

set HERE=%~dp0
set OUT=%HERE%out
if not exist "%OUT%" mkdir "%OUT%"
cl.exe /nologo /O2 /W3 "%HERE%hangsnap.c" /Fo"%OUT%\\" /Fe"%OUT%\hangsnap.exe" /link /NOLOGO || exit /b 1
echo built %OUT%\hangsnap.exe
