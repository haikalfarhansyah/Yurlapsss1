@echo off
REM ============================================================
REM  Jalankan YurLaps dengan decoder OpenStint asli
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist .venv (
  echo Membuat virtual env...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q pyzmq pywebview

echo.
echo  Pastikan decoder OpenStint sudah jalan (mengirim ke tcp://127.0.0.1:5556)
echo.
python -m app --port 8080
pause
