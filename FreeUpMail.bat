@echo off
rem Runs Free(up)Mail from source in its own window. Closing the window stops it.
rem The log is in %LOCALAPPDATA%\FreeUpMail\freeupmail.log.
cd /d "%~dp0"
start "" pythonw -m freeupmail
