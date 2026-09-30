@echo off
REM Start wireproxy in the current console window.
REM Close this window (or Ctrl+C) to stop the proxy.
cd /d "%~dp0"
echo Starting wireproxy on 127.0.0.1:1080 (SOCKS5)...
echo   user: wgp009
echo   pass: (see wireproxy.conf)
echo.
wireproxy.exe -c wireproxy.conf
pause
