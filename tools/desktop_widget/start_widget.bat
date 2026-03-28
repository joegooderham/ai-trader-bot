@echo off
REM ── AI Trader Bot — Desktop P&L Widget ──
REM Double-click this file to launch the floating P&L widget.
REM Requires Python 3.8+ (tkinter is built in).

cd /d "%~dp0"

REM Try common Python locations
where pythonw >nul 2>&1 && (start "" pythonw widget.py & exit /b)
where python >nul 2>&1 && (start "" python widget.py & exit /b)

REM Try Windows Store / AppData installs
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python*\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python*\python.exe"
    "%PROGRAMFILES%\Python*\pythonw.exe"
    "C:\Python*\pythonw.exe"
) do (
    for %%F in (%%P) do if exist "%%F" (start "" "%%F" widget.py & exit /b)
)

echo ERROR: Python not found. Install Python 3.8+ from https://python.org
echo Make sure to tick "Add Python to PATH" during install.
pause
