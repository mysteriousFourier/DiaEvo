@echo off
setlocal
set "DIAEVO_INSTALL_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DIAEVO_INSTALL_ROOT%diaevo-home.ps1" %*
exit /b %ERRORLEVEL%
