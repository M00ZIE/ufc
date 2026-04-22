@echo off
REM Politica do Windows bloqueia ".\push-github.ps1" direto — use este .bat ou Bypass (ver comentario no .ps1).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push-github.ps1" %*
if errorlevel 1 pause
