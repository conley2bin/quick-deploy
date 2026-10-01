@echo off
rem Native Windows entrypoint: installed Python 3.10+ (py launcher), PyYAML,
rem system OpenSSH and official Moonlight.exe on PATH or --moonlight-binary PATH.
rem This script performs no installation, host enrollment or preference repair.
py -3 "%~dp0service\run-server.py" %*
exit /b %errorlevel%
