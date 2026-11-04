@echo off
REM ============================================================
REM  Build YurLaps.exe (Windows)
REM  Butuh Python 3.10+ 64-bit.
REM ============================================================
setlocal
cd /d "%~dp0"

echo [1/4] Membuat virtual env...
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat

echo [2/4] Install dependensi...
python -m pip install --upgrade pip >nul
pip install pyzmq pyinstaller

echo [3/4] Membersihkan build lama...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/4] Membuat YurLaps.exe...
pyinstaller --noconfirm --clean stintlab.spec

echo.
echo ============================================================
echo  Selesai: dist\YurLaps\YurLaps.exe
echo  Bagikan folder dist\YurLaps (zip) ke komputer lain.
echo ============================================================
pause
