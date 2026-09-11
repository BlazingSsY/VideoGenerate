@echo off
setlocal
"%~dp0..\.venv\Scripts\python.exe" "%~dp0generate_video.py"
if errorlevel 1 pause
endlocal
