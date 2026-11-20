@echo off
rem Opens the Foundry editor (a page in your browser) for this game's mods.
rem Choose a mod there, or make a new one. Close this window to stop it.
cd /d "%~dp0"
title Foundry editor
if not exist "foundry\mercfoundry.pyz" (
  echo The Foundry toolkit isn't in this folder ^(foundry\mercfoundry.pyz^).
  pause
  exit /b 1
)
set PY=foundry\python\python.exe
if not exist "%PY%" set PY=python
"%PY%" "foundry\mercfoundry.pyz" edit --game "%~dp0."
if errorlevel 1 pause
